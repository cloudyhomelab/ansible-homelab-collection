#!/usr/bin/python
# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``external_package`` module: its documentation and argument spec. The work is done by
the action plugin of the same name, on the controller."""

from __future__ import annotations

DOCUMENTATION = r"""
module: external_package
short_description: Install a deb or rpm package file downloaded from a URL
version_added: 3.0.0
author:
  - binarycodes (@binarycodes)
description:
  - Installs a package file that no repository the host uses carries, such as a project's
    release download, unless the package is installed at O(version) already.
  - The format follows the host's package manager - a deb on apt, an rpm on dnf or dnf5. It is
    read from C(ansible_facts.pkg_mgr), gathered if the play did not gather it.
  - Every option is checked against the format's rules before anything is downloaded, and every
    problem is reported in one run.
  - The installed version is read with C(dpkg-query) or C(rpm). Otherwise the file is
    downloaded into a fresh temporary directory, where M(ansible.builtin.get_url) refuses it
    unless O(checksum) matches; installed with M(ansible.builtin.apt), which updates the lists
    for the package's dependencies, or M(ansible.builtin.dnf); and the directory is removed,
    also when the install failed.
  - The installed version is read again and the task fails unless it is O(version). A version
    that does not match the package's own would otherwise download and reinstall on every run.
  - An rpm is installed with dnf's signature check off, since a release's rpm is unsigned or
    signed with a key the host has not imported. The checksum is what the file is trusted by,
    so pin it from a source you trust.
  - Raising O(version) installs the newer file over the old one. Lowering it fails, since
    neither apt nor dnf downgrades unless told to.
  - One package per task; use C(loop) for several. Removing a package is
    M(ansible.builtin.package) with O(ansible.builtin.package#module:state=absent).
options:
  name:
    description: The package's name, as the package manager reports it.
    type: str
    required: true
  version:
    description:
      - The version the package file carries - C([epoch:]upstream[-revision]) for a deb,
        C([epoch:]version-release) for an rpm. It decides whether the package is installed
        already, so it has to match what the package manager reports exactly.
      - Read it from the file once with C(dpkg-deb -f <file> Version), or
        C(rpm -qp --qf '%|EPOCH?{%{EPOCH}:}:{}|%{VERSION}-%{RELEASE}\n' <file>).
    type: str
    required: true
  url:
    description:
      - Where to download the package file from - an http, https, ftp or file URL whose file is
        in the host's package format. Which file suits which host is up to the caller.
    type: str
    required: true
  checksum:
    description:
      - C(<algorithm>:<hex digest>) of the package file, with the algorithm one of C(md5),
        C(sha1), C(sha224), C(sha256), C(sha384), C(sha512).
    type: str
    required: true
extends_documentation_fragment:
  - ansible.builtin.action_common_attributes
  - ansible.builtin.action_common_attributes.flow
attributes:
  action:
    support: full
  async:
    support: none
  bypass_host_loop:
    support: none
  check_mode:
    support: full
    details: Reads the installed version and reports a package it would install as changed, without downloading anything.
  diff_mode:
    support: none
  platform:
    platforms: debian, fedora
notes:
  - Runs as root, so the task or play sets C(become=true).
  - Fedora 43 and Debian 13 are what the collection's tests converge.
seealso:
  - module: ansible.builtin.package
"""

EXAMPLES = r"""
- name: Install grype from its release
  become: true
  binarycodes.homelab.external_package:
    name: grype
    version: 0.121.0
    url: https://github.com/anchore/grype/releases/download/v0.121.0/grype_0.121.0_linux_amd64.deb
    checksum: sha256:3a23dcb0dfe903131eabb4af9e8977ea2a47e2bbbec5aa15b60026ca5467d46d

- name: Install several, each at its pinned version
  become: true
  binarycodes.homelab.external_package:
    name: "{{ item.name }}"
    version: "{{ item.version }}"
    url: "{{ item.url }}"
    checksum: "{{ item.checksum }}"
  loop: "{{ release_packages }}"
  loop_control:
    label: "{{ item.name }}"
"""

RETURN = r"""
name:
  description: The package's name.
  type: str
  returned: success
  sample: grype
version:
  description: The version the package is installed at.
  type: str
  returned: success
  sample: 0.121.0
previous_versions:
  description: The versions installed before this task, empty if the package was not installed.
  type: list
  elements: str
  returned: success
  sample: ["0.120.0"]
"""

from ansible.module_utils.basic import AnsibleModule

from ..module_utils.external_package import ARGUMENT_SPEC


def main() -> None:
    module = AnsibleModule(argument_spec=ARGUMENT_SPEC, supports_check_mode=True)
    module.fail_json(msg="external_package runs as an action plugin; this module only documents it.")


if __name__ == "__main__":
    main()
