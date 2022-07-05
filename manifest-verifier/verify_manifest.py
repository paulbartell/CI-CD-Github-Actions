#!/usr/bin/env python3
#
# FreeRTOS Project
# Copyright (C) 2021 Amazon.com, Inc. or its affiliates.  All Rights Reserved.
#
# SPDX-License-Identifier: MIT
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
# the Software, and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
# FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
# IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
# https://www.FreeRTOS.org
# https://github.com/FreeRTOS
#

import os, sys
import yaml
import requests
import git
import gitdb
from argparse import ArgumentParser
from termcolor import cprint

def process_commandline_args():
    parser = ArgumentParser(description='manifest.yml verifier')
    parser.add_argument('manifest_path',
                        metavar='manifest_path',
                        default=os.getcwd(),
                        help='Path to the manifest.yml file.')
    parser.add_argument('-i',
                        '--ignore-paths',
                        action='store',
                        dest='ignore_paths',
                        nargs='+',
                        default="",
                        help='Submodules that are not listed in the mnanifest file and should be ignored.')
    parser.add_argument('-s',
                        '--ignore-spdx',
                        action='store',
                        dest='ignore_spdx',
                        nargs='+',
                        default="",
                        help='SPDX identifiers which are expected and non-standard.')

    args = parser.parse_args()
    return args

def print_error(indent_level, error_str):
    """Print an error to standard out in red with a given indentation level"""
    cprint('{}ERROR: {}'.format('\t' * indent_level, error_str),'red')


def print_warning(indent_level, warn_str):
    """Print a warning to standard out in cyan with a given indentation level"""
    cprint('{}WARN:  {}'.format('\t' * indent_level, warn_str),'cyan')

def validate_submodule_repo(dep, manifest_dir):
    """Validate a given submodule referred to in a package manifest dependency stanza."""
    error_count = 0
    repo = dep["repository"]
    submodule = git.Repo(os.path.join(manifest_dir, repo["path"]))

    print("    Manifest URL:     {}".format(repo["url"]))
    print("    Submodule URL:    {}".format(submodule.remotes.origin.url))

    # URLs are case-insensitive
    if not submodule.remotes.origin.url.lower() == repo["url"].lower():
        print_error(1, "Repository url mismatch between manifest and submodule.")
        error_count += 1

    manifest_hash = "unknown"
    try:
        manifest_hash = submodule.commit(dep["version"]).hexsha
    except gitdb.exc.BadName:
        print_error(
            1, 'Manifest "version" is not a valid tag or branch in the submodule.'
        )

    # Try with lowercase version
    if manifest_hash == "unknown":
        try:
            manifest_hash = submodule.commit(dep["version"].lower()).hexsha
            print_warning("Manifest version tag is valid when converted to lowercase.")
        except gitdb.exc.BadName:
            pass

    print("    Manifest Version: {}".format(dep["version"]))
    print("    Manifest hash:    {}".format(manifest_hash))
    print("    Submodule hash:   {}".format(submodule.head.commit.hexsha))
    if not submodule.head.commit.hexsha == manifest_hash:
        print_error(1, "Revision mismatch between submodule and manifest version.")
        error_count += 1
    return error_count

def validate_repository(dep, manifest_dir):
    """Validate a given repository stanza from a package manifest."""
    repo = dep["repository"]
    repo_name = "unknown"
    if "name" in dep:
        repo_name = dep
    else:
        repo_name = str(repo)

    print("Validating repo:      {}".format(repo_name))
    error_count = 0
    if "type" not in repo:
        print_error(1, '"type" field not found in repository.')
        error_count += 1
    if "git" != repo["type"]:
        print_error(1, '"type" field is not set to "git" in repository')
        error_count += 1
    if "url" not in repo:
        print_error(1, '"url" field not found in repository')
        error_count += 1
    if "path" in repo:
        if not os.path.exists(os.path.join(manifest_dir, repo["path"])):
            print_error(1, "Relative path does not exist: {}".format(repo["path"]))
            error_count += 1
        # Validate submodule revision and url.
        else:
            error_count += validate_submodule_repo(dep, manifest_dir)
    return error_count

def load_spdx_data(ignore_spdx):
    """Load spdx license and license exception information from github."""
    spdx_data = dict()
    spdx_url = "https://raw.githubusercontent.com/spdx/license-list-data/"

    print("Downloading SPDX license data...")

    licenses_url = spdx_url + "master/json/licenses.json"
    licenses = requests.get(licenses_url).json()

    assert "licenses" in licenses
    spdx_data["licenses"] = dict()

    for license in licenses["licenses"]:
        spdx_data["licenses"][license["licenseId"]] = True

    for license in ignore_spdx:
        spdx_data["licenses"][license] = True

    exceptions_url = spdx_url + "master/json/exceptions.json"
    exceptions = requests.get(exceptions_url).json()

    assert "exceptions" in exceptions

    spdx_data["exceptions"] = dict()

    for exception in exceptions["exceptions"]:
        spdx_data["exceptions"][exception["licenseExceptionId"]] = True

    return spdx_data

def check_license_tag(spdx_data, cur_license):
    """
    Validate a given SPDX license tag against SPDX data previously fetched with
    load_spdx_data()
    """
    error_count = 0
    licenses = cur_license.split(" ")
    license_exception_flag = False
    paren_depth = 0
    last_paren_depth = 0
    for i in range(0, len(licenses)):
        if licenses[i][0] == "(":
            paren_depth += 1
        # skip "and" "or" keywords
        if licenses[i] in ["and", "AND", "or", "OR"]:
            pass
        # "with" keyword denotes a license exception
        elif licenses[i] in ["with", "WITH"]:
            # Set flag for next iteration
            license_exception_flag = True
        elif license_exception_flag:
            if not licenses[i].strip("()") in spdx_data["exceptions"]:
                print_error(
                    0,
                    "Invalid license exception id {} in SPDX tag {}".format(
                        licenses[i], cur_license
                    ),
                )
                error_count += 1
            # No '(' character -> single license exception
            if paren_depth <= last_paren_depth:
                license_exception_flag = False
        else:
            if not licenses[i].strip("()") in spdx_data["licenses"]:
                print_error(
                    0,
                    "Invalid license id {} in SPDX tag {}".format(
                        licenses[i], cur_license
                    ),
                )
                error_count += 1

        last_paren_depth = paren_depth
        if licenses[i][-1] == ")":
            paren_depth -= 1

    return error_count


def list_repo_paths_in_manifest(manifest):
    """Returns a list of submodule paths in the given manifest"""
    repo_list = []
    if "dependencies" in manifest:
        dependencies = manifest["dependencies"]
        for dep in dependencies:
            if "repository" in dep:
                repo = dep["repository"]
                if "path" in repo:
                    repo_list.append(repo["path"])
    return repo_list

def get_all_submodules(repo_root_path, ignored_paths):
    """Returns a list of all submodules contained in repo_root_path"""
    path_list = []
    repo = git.Repo(repo_root_path)
    for submodule in repo.submodules:
        path = os.path.relpath(submodule.abspath,repo_root_path)
        if path not in ignored_paths:
            path_list.append(path)

    return path_list

def check_for_missing_submodules(manifest_repos, git_submodules):
    """Check for any extra git submodules in this repository"""
    error_count = 0
    for submodule in git_submodules:
        if submodule not in manifest_repos:
            print_error(0, "Git submodule {} was not found in the manifest.".format(submodule))
            error_count += 1
    return error_count

def validate_dependency(dependency, spdx_data):
    """Validate a given dependency stanza from a package manifest."""
    error_count = 0
    if "name" not in dependency:
        print_error(0, "name field not found in dependency stanza.")
        error_count += 1

    if "version" not in dependency:
        print_error(0, "version field not found in dependency stanza.")
        error_count += 1

    if "license" not in dependency:
        print_error(1, '"license" field not found dependency stanzay.')
        error_count += 1
    else:
        print("Validating SPDX license tag: {}".format(dependency["license"]))
        error_count += check_license_tag(spdx_data, dependency["license"])
    return error_count

def validate_manifest(manifest_path, ignored_paths, spdx_data):
    error_count = 0
    manifest_dir = os.path.dirname(manifest_path)
    with open(manifest_path, "r") as file:
        manifest = yaml.load(file, Loader=yaml.FullLoader)

        if "name" not in manifest:
            print_error(0, "name field not found in manifest root.")
            error_count += 1

        if "version" not in manifest:
            print_error(0, "version field not found in manifest root.")
            error_count += 1

        if "description" not in manifest:
            print_error(0, "version field not found in manifest root.")
            error_count += 1

        if "license" not in manifest:
            print_error(0, "version field not found in manifest root.")
            error_count += 1
        else:
            print("Validating SPDX license tag: {}".format(manifest["license"]))
            error_count += check_license_tag(spdx_data, manifest["license"])

        if "dependencies" in manifest:
            dependencies = manifest["dependencies"]
            for dep in dependencies:
                error_count += validate_dependency(dep, spdx_data)

                if "repository" in dep:
                    error_count += validate_repository(dep, manifest_dir)

        repo_paths = list_repo_paths_in_manifest(manifest)
        submodule_paths = get_all_submodules(os.path.dirname(manifest_path), ignored_paths)

        error_count += check_for_missing_submodules(repo_paths, submodule_paths)

    return error_count

def main():
    args = process_commandline_args()

    # Convert any relative path (like './') in passed argument to absolute path.
    manifest_path = os.path.abspath(args.manifest_path)

    ignore_paths = args.ignore_paths

    if not os.path.exists(manifest_path):
        print_error(0, "Error: no file found at {}".format(manifest_path))
        sys.exit(-1)

    manifest_dir = os.path.dirname(manifest_path)

    for p in ignore_paths:
        if not os.path.exists(os.path.join(manifest_dir, p)):
            print_error(0, "Ignored path: {} was not found.".format(p))

    ignore_spdx = args.ignore_spdx

    spdx_data = load_spdx_data(ignore_spdx)

    error_count = validate_manifest(manifest_path, ignore_paths, spdx_data)

    print("Total errors: {}".format(error_count))

    sys.exit(error_count > 0)

if __name__ == '__main__':
    main()

