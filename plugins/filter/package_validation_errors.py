# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``package_validation_errors`` filter. Runs on the controller; touches no managed host."""

from __future__ import annotations

import re
from collections.abc import Callable


DOCUMENTATION = r"""
name: package_validation_errors
short_description: Why a list of package files cannot be installed, one string per problem
version_added: 2.1.0
author:
  - binarycodes (@binarycodes)
description:
  - Checks each package's name, version, URL and checksum before anything is downloaded.
  - The name and version are checked against the rules of the host's package format. The name
    becomes an argument to C(dpkg-query) or C(rpm) and part of a file name, and the version is
    compared with what those print, so an rpm version must carry its release.
  - The checksum is required and checked for length, so a truncated paste is refused rather
    than failing the download.
  - Never raises; one string per problem, so a typo is fixed in one pass.
  - C(re.fullmatch), not a C($) anchor, which would let a trailing newline through.
options:
  _input:
    description:
      - The packages, each a dict with C(name), C(version), C(url) and C(checksum), all
        required.
    type: list
    elements: dict
    required: true
  package_format:
    description:
      - The host's package format.
    type: str
    choices: [deb, rpm]
    required: true
"""

RETURN = r"""
_value:
  description: One human-readable string per problem. Empty when every package can be installed.
  type: list
  elements: str
"""

EXAMPLES = r"""
- name: Refuse a package whose download cannot be verified
  ansible.builtin.assert:
    that:
      - packages | binarycodes.homelab.package_validation_errors('deb') | length == 0
    # [{'name': 'grype', 'version': '0.121.0', 'url': 'https://...', 'checksum': 'abc'}] produces:
    # ["external_packages_list[0].checksum 'abc' is not '<algorithm>:<hex digest>', ..."]
"""


# Debian policy §5.6.1 and §5.6.12; a dash in the version separates the Debian revision.
_DEB_NAME_RE = re.compile(r"[a-z0-9][a-z0-9+.-]+")
_DEB_VERSION_RE = re.compile(r"(?:[0-9]+:)?[0-9][A-Za-z0-9.+~-]*")
# What rpm accepts, less a leading dash, which dpkg-query and rpm would take for an option.
_RPM_NAME_RE = re.compile(r"[A-Za-z0-9_+][A-Za-z0-9._+-]*")
# Exactly one dash: rpm reports version and release apart, and the role compares both.
_RPM_VERSION_RE = re.compile(r"(?:[0-9]+:)?[A-Za-z0-9._+~^]+-[A-Za-z0-9._+~^]+")
_FORMATS = {
    "deb": (_DEB_NAME_RE, _DEB_VERSION_RE, "[epoch:]upstream[-revision], as dpkg-deb -f prints it"),
    "rpm": (_RPM_NAME_RE, _RPM_VERSION_RE, "[epoch:]version-release, as rpm -qp prints it"),
}
# The schemes get_url downloads from.
_URL_RE = re.compile(r"(?:https?|ftp|file)://\S+")
_DIGEST_LENGTHS = {"md5": 32, "sha1": 40, "sha224": 56, "sha256": 64, "sha384": 96, "sha512": 128}
_CHECKSUM_RE = re.compile(r"([a-z0-9]+):([0-9a-fA-F]+)")
_KEYS = {"name", "version", "url", "checksum"}


def _package_problems(where: str, package: object, package_format: str) -> list[str]:
    if not isinstance(package, dict):
        return [f"{where} is not a dict with 'name', 'version', 'url' and 'checksum'"]
    problems: list[str] = []
    name_re, version_re, version_shape = _FORMATS[package_format]

    unknown = sorted(str(k) for k in package if k not in _KEYS)
    if unknown:
        problems.append(f"{where} has unknown keys {unknown}; expected {sorted(_KEYS)}")

    name = package.get("name")
    if not isinstance(name, str) or not name_re.fullmatch(name):
        problems.append(f"{where}.name {name!r} is not a {package_format} package name")

    version = package.get("version")
    if not isinstance(version, str) or not version_re.fullmatch(version):
        problems.append(f"{where}.version {version!r} is not {version_shape}")

    url = package.get("url")
    if not isinstance(url, str) or not _URL_RE.fullmatch(url):
        problems.append(f"{where}.url {url!r} is not an http, https, ftp or file URL")

    checksum = package.get("checksum")
    match = _CHECKSUM_RE.fullmatch(checksum) if isinstance(checksum, str) else None
    if match is None or len(match.group(2)) != _DIGEST_LENGTHS.get(match.group(1)):
        problems.append(
            f"{where}.checksum {checksum!r} is not '<algorithm>:<hex digest>', with the "
            f"algorithm one of {sorted(_DIGEST_LENGTHS)}"
        )
    return problems


def package_validation_errors(packages: object, package_format: str) -> list[str]:
    """Why these packages cannot be installed, one string per problem; empty means they can."""
    if package_format not in _FORMATS:
        return [f"package format {package_format!r} is not one of {sorted(_FORMATS)}"]
    if not isinstance(packages, list):
        return ["external_packages_list is not a list"]
    problems: list[str] = []
    seen: set[str] = set()
    for index, package in enumerate(packages):
        where = f"external_packages_list[{index}]"
        problems.extend(_package_problems(where, package, package_format))
        name = package.get("name") if isinstance(package, dict) else None
        if isinstance(name, str):
            if name in seen:
                problems.append(f"{where}.name {name!r} is listed twice")
            seen.add(name)
    return problems


class FilterModule:
    """Input checks for the package files the external_packages role installs."""

    def filters(self) -> dict[str, Callable[..., object]]:
        return {"package_validation_errors": package_validation_errors}
