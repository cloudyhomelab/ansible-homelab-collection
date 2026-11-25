# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""What the ``external_package`` module and its action plugin share: the argument spec, the
input rules and the package-database queries."""

from __future__ import annotations

import re
from collections.abc import Mapping

ARGUMENT_SPEC: dict[str, dict[str, object]] = {
    "name": {"type": "str", "required": True},
    "version": {"type": "str", "required": True},
    "url": {"type": "str", "required": True},
    "checksum": {"type": "str", "required": True},
}

# The package format each supported ansible_facts.pkg_mgr installs.
FORMATS = {"apt": "deb", "dnf": "rpm", "dnf5": "rpm"}

# Prints "installed <version>" for an installed package, in the shape an entry's version takes.
# dpkg-query also lists a removed package whose configuration is left, hence the status.
QUERIES = {
    "deb": ["dpkg-query", "--show", "--showformat=${db:Status-Status} ${Version}\n"],
    "rpm": ["rpm", "--query", "--queryformat=installed %|EPOCH?{%{EPOCH}:}:{}|%{VERSION}-%{RELEASE}\n"],
}

# Debian policy §5.6.1 and §5.6.12; a dash in the version separates the Debian revision.
_DEB_NAME_RE = re.compile(r"[a-z0-9][a-z0-9+.-]+")
_DEB_VERSION_RE = re.compile(r"(?:[0-9]+:)?[0-9][A-Za-z0-9.+~-]*")
# What rpm accepts, less a leading dash, which dpkg-query and rpm would take for an option.
_RPM_NAME_RE = re.compile(r"[A-Za-z0-9_+][A-Za-z0-9._+-]*")
# Exactly one dash: rpm reports version and release apart, and both are compared.
_RPM_VERSION_RE = re.compile(r"(?:[0-9]+:)?[A-Za-z0-9._+~^]+-[A-Za-z0-9._+~^]+")
_RULES = {
    "deb": (_DEB_NAME_RE, _DEB_VERSION_RE, "[epoch:]upstream[-revision], as dpkg-deb -f prints it"),
    "rpm": (_RPM_NAME_RE, _RPM_VERSION_RE, "[epoch:]version-release, as rpm -qp prints it"),
}
# The schemes get_url downloads from.
_URL_RE = re.compile(r"(?:https?|ftp|file)://\S+")
_DIGEST_LENGTHS = {"md5": 32, "sha1": 40, "sha224": 56, "sha256": 64, "sha384": 96, "sha512": 128}
_CHECKSUM_RE = re.compile(r"([a-z0-9]+):([0-9a-fA-F]+)")


def validation_errors(args: Mapping[str, object], package_format: str) -> list[str]:
    """Why this package cannot be installed, one string per problem; empty means it can.

    The name becomes a command argument and part of a file name written as root, and the
    version is compared with what the package database prints, so both are held to the
    format's rules. A checksum of the wrong length is refused here, before the download.
    ``re.fullmatch``, so a trailing newline is refused.
    """
    if package_format not in _RULES:
        return [f"package format {package_format!r} is not one of {sorted(_RULES)}"]
    problems: list[str] = []
    name_re, version_re, version_shape = _RULES[package_format]

    name = args.get("name")
    if not isinstance(name, str) or not name_re.fullmatch(name):
        problems.append(f"name {name!r} is not a {package_format} package name")

    version = args.get("version")
    if not isinstance(version, str) or not version_re.fullmatch(version):
        problems.append(f"version {version!r} is not {version_shape}")

    url = args.get("url")
    if not isinstance(url, str) or not _URL_RE.fullmatch(url):
        problems.append(f"url {url!r} is not an http, https, ftp or file URL")

    checksum = args.get("checksum")
    match = _CHECKSUM_RE.fullmatch(checksum) if isinstance(checksum, str) else None
    if match is None or len(match.group(2)) != _DIGEST_LENGTHS.get(match.group(1)):
        problems.append(
            f"checksum {checksum!r} is not '<algorithm>:<hex digest>', with the "
            f"algorithm one of {sorted(_DIGEST_LENGTHS)}"
        )
    return problems


def installed_versions(stdout: str) -> list[str]:
    """The versions a query in QUERIES reports installed. rpm prints one line per installed
    instance, which is several for a multilib package."""
    prefix = "installed "
    return [line[len(prefix):] for line in stdout.splitlines() if line.startswith(prefix)]
