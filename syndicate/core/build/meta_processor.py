"""
    Copyright 2018 EPAM Systems, Inc.

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
"""
import os
from json import load

from syndicate.commons.log_helper import get_logger
from syndicate.core import CONFIG, S3_PATH_NAME
from syndicate.core.build.helper import build_py_package_name
from syndicate.core.conf.config_holder import GLOBAL_AWS_SERVICES
from syndicate.core.constants import (API_GATEWAY_TYPE, ARTIFACTS_FOLDER,
                                      BUILD_META_FILE_NAME, EBS_TYPE,
                                      LAMBDA_CONFIG_FILE_NAME, LAMBDA_TYPE,
                                      RESOURCES_FILE_NAME, RESOURCE_LIST)
from syndicate.core.helper import (build_path, prettify_json,
                                   resolve_aliases_for_string,
                                   write_content_to_file)
from syndicate.core.resources.helper import resolve_dynamic_identifier

_LOG = get_logger('syndicate.core.build.meta_processor')


def validate_deployment_packages(meta_resources):
    package_paths = artifact_paths(meta_resources)
    bundles_path = build_path(CONFIG.project_path, ARTIFACTS_FOLDER)
    nonexistent_packages = []
    for package in package_paths:
        package_path = build_path(bundles_path, package)
        if not os.path.exists(package_path):
            nonexistent_packages.append(package_path)

    if nonexistent_packages:
        raise AssertionError('Bundle is not properly configured.'
                             ' Nonexistent deployment packages: '
                             '{0}'.format(prettify_json(nonexistent_packages)))


def artifact_paths(meta_resources):
    return [i for i in
            [_retrieve_package(v) for v in list(meta_resources.values())] if i]


def _retrieve_package(meta):
    s3_path = meta.get(S3_PATH_NAME)
    if s3_path:
        return s3_path


def _check_duplicated_resources(initial_meta_dict, additional_item_name,
                                additional_item):
    """ Match two meta dicts (overall and separated) for duplicates.

    :type initial_meta_dict: dict
    :type additional_item_name: str
    :type additional_item: dict
    """
    if additional_item_name in initial_meta_dict:
        additional_type = additional_item['resource_type']
        initial_item = initial_meta_dict.get(additional_item_name)
        if not initial_item:
            return
        initial_type = initial_item['resource_type']
        if additional_type == initial_type == API_GATEWAY_TYPE:
            # check if APIs have same resources
            for each in list(initial_item['resources'].keys()):
                if each in list(additional_item['resources'].keys()):
                    raise AssertionError(
                        "API '{0}' has duplicated resource '{1}'! Please, "
                        "change name of one resource or remove one.".format(
                            additional_item_name, each))
                    # check is APIs have once described cache configuration
            initial_cache_config = initial_item.get(
                'cluster_cache_configuration')
            additional_cache_config = additional_item.get(
                'cluster_cache_configuration')
            if initial_cache_config and additional_cache_config:
                raise AssertionError(
                    "API '{0}' has duplicated cluster cache configurations. "
                    "Please, remove one cluster cache configuration.".format(
                        additional_item_name)
                )
            if initial_cache_config:
                additional_item[
                    'cluster_cache_configuration'] = initial_cache_config
            # join items dependencies
            dependencies_dict = {each['resource_name']: each
                                 for each in additional_item['dependencies']}
            for each in initial_item['dependencies']:
                if each['resource_name'] not in dependencies_dict:
                    additional_item['dependencies'].append(each)
            # join items resources
            additional_item['resources'].update(initial_item['resources'])
            # return aggregated API description
            init_deploy_stage = initial_item.get('deploy_stage')
            if init_deploy_stage:
                additional_item['deploy_stage'] = init_deploy_stage
            init_apply = initial_item.get('apply_changes', [])
            add_apply = additional_item.get('apply_changes', [])
            additional_item['apply_changes'] = init_apply + add_apply

            return additional_item

        elif additional_type == initial_type:
            if additional_item == initial_item:
                raise AssertionError(
                    'Warn. Two equals resources descriptions were found! '
                    'Please, remove one of them. Resource name:'
                    ' {0}'.format(additional_item_name))
            else:
                raise AssertionError(
                    "Error! Two resources with equal names were found! Name:"
                    " {0}. Please, rename one of them. Fist resource: {1}. "
                    "Second resource: {2}".format(additional_item_name,
                                                  initial_item,
                                                  additional_item))


def _populate_s3_path_python(meta, bundle_name):
    name = meta.get('name')
    version = meta.get('version')
    if not name or not version:
        raise AssertionError('Lambda config must contain name and version. '
                             'Existing configuration'
                             ': {0}'.format(prettify_json(meta)))
    else:
        meta[S3_PATH_NAME] = build_path(bundle_name,
                                        build_py_package_name(name, version))


def _populate_s3_path_java(meta, bundle_name):
    deployment_package = meta.get('deployment_package')
    if not deployment_package:
        raise AssertionError('Lambda config must contain deployment_package. '
                             'Existing configuration'
                             ': {0}'.format(prettify_json(meta)))
    else:
        meta[S3_PATH_NAME] = build_path(bundle_name, deployment_package)


def _populate_s3_path_lambda(meta, bundle_name):
    runtime = meta.get('runtime')
    if not runtime:
        raise AssertionError(
            'Lambda config must contain runtime. '
            'Existing configuration: {0}'.format(prettify_json(meta)))
    resolver_func = RUNTIME_PATH_RESOLVER.get(runtime.lower())
    if resolver_func:
        resolver_func(meta, bundle_name)
    else:
        raise AssertionError(
            'Lambda config must contain runtime. '
            'Existing configuration: {0}'.format(prettify_json(meta)))


def _populate_s3_path_ebs(meta, bundle_name):
    deployment_package = meta.get('deployment_package')
    if not deployment_package:
        raise AssertionError('Beanstalk_app config must contain '
                             'deployment_package. Existing configuration'
                             ': {0}'.format(prettify_json(meta)))
    else:
        meta[S3_PATH_NAME] = build_path(bundle_name, deployment_package)


def _populate_s3_path(meta, bundle_name):
    resource_type = meta.get('resource_type')
    mapping_func = S3_PATH_MAPPING.get(resource_type)
    if mapping_func:
        mapping_func(meta, bundle_name)


RUNTIME_PATH_RESOLVER = {
    'python2.7': _populate_s3_path_python,
    'java8': _populate_s3_path_java
}

S3_PATH_MAPPING = {
    LAMBDA_TYPE: _populate_s3_path_lambda,
    EBS_TYPE: _populate_s3_path_ebs
}


def _look_for_configs(nested_files, resources_meta, path, bundle_name):
    """ Look for all config files in project structure. Read content and add
    all meta to overall meta if there is no duplicates. If duplicates found -
    raise AssertionError.

    :type nested_files: list
    :type resources_meta: dict
    :type path: str
    """
    for each in nested_files:
        if each.endswith(LAMBDA_CONFIG_FILE_NAME):
            lambda_config_path = os.path.join(path, each)
            _LOG.debug('Processing file: {0}'.format(lambda_config_path))
            with open(lambda_config_path) as data_file:
                lambda_conf = load(data_file)

            lambda_name = lambda_conf['name']
            _LOG.debug('Found lambda: {0}'.format(lambda_name))
            _populate_s3_path(lambda_conf, bundle_name)
            res = _check_duplicated_resources(resources_meta, lambda_name,
                                              lambda_conf)
            if res:
                lambda_conf = res
            resources_meta[lambda_name] = lambda_conf

        if each == RESOURCES_FILE_NAME:
            additional_config_path = os.path.join(path, RESOURCES_FILE_NAME)
            _LOG.debug('Processing file: {0}'.format(additional_config_path))
            with open(additional_config_path) as json_file:
                deployment_resources = load(json_file)
            for resource_name in deployment_resources:
                _LOG.debug('Found resource ' + resource_name)
                resource = deployment_resources[resource_name]
                # check if resource type exists in deployment framework and
                #  has resource_type field
                try:
                    resource_type = resource['resource_type']
                except KeyError:
                    raise AssertionError(
                        "There is not 'resource_type' in {0}".format(
                            resource_name))
                if resource_type not in RESOURCE_LIST:
                    raise KeyError(
                        "You specified new resource type in configuration"
                        " file {0}, but it doesn't have creation function."
                        " Please, add new creation function or change "
                        "resource name with existing one.".format(
                            resource_type))
                _populate_s3_path(resource, bundle_name)
                res = _check_duplicated_resources(resources_meta,
                                                  resource_name, resource)
                if res:
                    resource = res
                resources_meta[resource_name] = resource


# todo validate all required configs
def create_resource_json(bundle_name):
    """ Create resource catalog json with all resource metadata in project.
    :type bundle_name: name of the bucket subdir
    """
    resources_meta = {}

    for path, _, nested_items in os.walk(CONFIG.project_path):
        # there is no duplicates in single json, because json is a dict

        _look_for_configs(nested_items, resources_meta, path, bundle_name)

    # check if all dependencies were described
    for resource_name in resources_meta:
        meta = resources_meta[resource_name]
        dependencies = meta.get('dependencies')
        if dependencies:
            for dependency in meta['dependencies']:
                dependency_name = dependency.get('resource_name')
                if dependency_name not in list(resources_meta.keys()):
                    err_mess = ("One of resource dependencies wasn't "
                                "described: {0}. Please, describe this "
                                "resource in {1} if it is Lambda or in "
                                "deployment_resources.json"
                                .format(dependency_name,
                                        LAMBDA_CONFIG_FILE_NAME))
                    raise AssertionError(err_mess)

    return resources_meta


def _resolve_names_in_meta(resources_dict, old_value, new_value):
    if isinstance(resources_dict, dict):
        for k, v in resources_dict.items():
            # if k == old_value:
            #     resources_dict[new_value] = resources_dict.pop(k)
            if isinstance(v, str) and old_value == v:
                resources_dict[k] = v.replace(old_value, new_value)
            elif isinstance(v, str) and old_value in v and v.startswith('arn'):
                resources_dict[k] = v.replace(old_value, new_value)
            else:
                _resolve_names_in_meta(v, old_value, new_value)
    elif isinstance(resources_dict, list):
        for item in resources_dict:
            if isinstance(item, dict):
                _resolve_names_in_meta(item, old_value, new_value)
            elif isinstance(item, str):
                if item == old_value:
                    index = resources_dict.index(old_value)
                    del resources_dict[index]
                    resources_dict.append(new_value)


def create_meta(bundle_name):
    # create overall meta.json with all resource meta info
    meta_path = build_path(CONFIG.project_path, ARTIFACTS_FOLDER,
                           bundle_name)
    _LOG.info("Bundle path: {0}".format(meta_path))
    overall_meta = create_resource_json(bundle_name=bundle_name)

    write_content_to_file(meta_path, BUILD_META_FILE_NAME, overall_meta)


def resolve_meta(overall_meta):
    for key, value in CONFIG.aliases.items():
        name = '${' + key + '}'
        overall_meta = resolve_dynamic_identifier(name, value, overall_meta)
    _LOG.debug('Resolved meta was created')
    _LOG.debug(prettify_json(overall_meta))
    # get dict with resolved prefix and suffix in meta resources
    # key: current_name, value: resolved_name
    resolved_names = {}
    for name, res_meta in overall_meta.items():
        resource_type = res_meta['resource_type']
        if resource_type in GLOBAL_AWS_SERVICES:
            resolved_name = resolve_resource_name(name)
            if name != resolved_name:
                resolved_names[name] = resolved_name
    _LOG.debug('Going to resolve names in meta')
    _LOG.debug('Resolved names mapping: {0}'.format(str(resolved_names)))
    for current_name, resolved_name in resolved_names.items():
        overall_meta[resolved_name] = overall_meta.pop(current_name)
        _resolve_names_in_meta(overall_meta, current_name, resolved_name)
    return overall_meta


def resolve_resource_name(resource_name):
    return _resolve_suffix_name(
        _resolve_prefix_name(resource_name, CONFIG.resources_prefix),
        CONFIG.resources_suffix)


def resolve_resource_name_by_data(resource_name, resource_prefix,
                                  resource_suffix):
    return _resolve_suffix_name(
        _resolve_prefix_name(resource_name, resource_prefix), resource_suffix)


def _resolve_prefix_name(resource_name, resource_prefix):
    if resource_prefix:
        return resolve_aliases_for_string(resource_prefix) + resource_name
    return resource_name


def _resolve_suffix_name(resource_name, resource_suffix):
    if resource_suffix:
        return resource_name + resolve_aliases_for_string(resource_suffix)
    return resource_name
