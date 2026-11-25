# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``source_tree`` filter. Runs on the controller, where the app definitions are."""

from __future__ import annotations

import os
import posixpath
from collections.abc import Callable, Iterable
from typing import TypedDict

from ansible.errors import AnsibleFilterError


DOCUMENTATION = r"""
name: source_tree
short_description: What a C(source) app ships, and the host paths it installs to
version_added: 1.1.0
author:
  - binarycodes (@binarycodes)
description:
  - What a C(source) deploy installs from an app's directory, grouped as the role installs it,
    with the host path each lands at - which is what the install manifest records.
  - C(quadlet/) and C(unit/) are flat and installed by basename, so they list regular files
    one level down, hidden ones excluded, as a C(*) glob would. C(config/) is a tree, so every
    file below it is listed with its path relative to C(config/).
  - A C(unit/<unit>.d/) directory holds drop-ins for C(<unit>), installed to
    C(<unit_dir>/<unit>.d/). The unit need not be one the app ships; a unit a distribution
    package installs is the usual case. Raises when C(<unit>) is not a service, socket, timer,
    path, mount or automount, when the directory holds anything but C(*.conf) files (hidden
    ones excluded), or when a file is named in O(reserved_dropins). Other directories in
    C(unit/) are ignored.
  - A symlink to a file counts as a file everywhere - the copy follows it and puts a regular
    file on the host, and a path the copy installs but the manifest omits gets pruned next
    deploy.
  - Raises on a missing directory. Returning nothing would install nothing, report success,
    and prune every path the last deploy recorded.
  - Runs on the controller; C(src) values are controller paths, RV(_value.installed) are host
    paths.
positional: system_dir, unit_dir, config_dir, reserved_dropins
options:
  _input:
    description: The app's directory on the controller, normally C(<systemd_app_apps_dir>/<name>).
    type: str
    required: true
  system_dir:
    description: The host's Quadlet install directory, normally C(/etc/containers/systemd).
    type: str
    required: true
  unit_dir:
    description: The host's plain-unit install directory, normally C(/etc/systemd/system).
    type: str
    required: true
  config_dir:
    description: Where the app's config tree lands on the host, normally C(/var/app/<name>/config).
    type: str
    required: true
  reserved_dropins:
    description:
      - Drop-in file names the caller writes itself, refused in the app's C(<unit>.d/)
        directories, where the app's copy and the caller's would overwrite each other.
    type: list
    elements: str
    default: []
    version_added: 2.2.0
"""

RETURN = r"""
_value:
  description: The files the app ships and where they install to. Every list is sorted.
  type: dict
  contains:
    quadlet_files:
      description: Controller paths of the files in C(quadlet/).
      type: list
      elements: str
    unit_files:
      description: Controller paths of the files in C(unit/).
      type: list
      elements: str
    dropin_files:
      description:
        - Every drop-in under C(unit/<unit>.d/), as C(src) (controller path) and C(path)
          (C(<unit>.d/<name>.conf), relative to the unit directory).
      type: list
      elements: dict
      version_added: 2.2.0
    config_files:
      description: Every file under C(config/), as C(src) (controller path) and C(path) (relative to C(config/)).
      type: list
      elements: dict
    config_dir:
      description: The controller path of C(config/) when the app ships one, else none.
      type: str
    installed:
      description: The absolute host path of every file above, as the install manifest records them.
      type: list
      elements: str
"""

EXAMPLES = r"""
- name: Learn what the app ships and where it lands
  ansible.builtin.set_fact:
    tree: >-
      {{ '/srv/apps/myapp' | binarycodes.homelab.source_tree(
           '/etc/containers/systemd', '/etc/systemd/system', '/var/app/myapp/config') }}
  # For quadlet/myapp.container, unit/myapp-extra.service, unit/nginx.service.d/50-myapp.conf
  # and config/nested/app.conf:
  #   quadlet_files: ['/srv/apps/myapp/quadlet/myapp.container']
  #   unit_files:    ['/srv/apps/myapp/unit/myapp-extra.service']
  #   dropin_files:  [{'src': '/srv/apps/myapp/unit/nginx.service.d/50-myapp.conf',
  #                    'path': 'nginx.service.d/50-myapp.conf'}]
  #   config_files:  [{'src': '/srv/apps/myapp/config/nested/app.conf', 'path': 'nested/app.conf'}]
  #   config_dir:    '/srv/apps/myapp/config'
  #   installed:     ['/etc/containers/systemd/myapp.container',
  #                   '/etc/systemd/system/myapp-extra.service',
  #                   '/etc/systemd/system/nginx.service.d/50-myapp.conf',
  #                   '/var/app/myapp/config/nested/app.conf']

- name: Refuse an app drop-in that would overwrite one the role writes
  ansible.builtin.set_fact:
    tree: >-
      {{ '/srv/apps/myapp' | binarycodes.homelab.source_tree(
           '/etc/containers/systemd', '/etc/systemd/system', '/var/app/myapp/config',
           reserved_dropins=['10-private.conf', '20-slice.conf']) }}
"""


# The unit types install_manifest admits a drop-in directory for. Checked here as well, so a
# wrong name fails before anything is copied rather than at the manifest call after it.
_PLAIN_UNIT_SUFFIXES = (
    ".service",
    ".socket",
    ".timer",
    ".path",
    ".mount",
    ".automount",
)
_DROPIN_DIR_SUFFIX = ".d"
_DROPIN_FILE_SUFFIX = ".conf"


class ConfigFile(TypedDict):
    src: str
    path: str


class SourceTree(TypedDict):
    """The filter's return; see RETURN."""

    quadlet_files: list[str]
    unit_files: list[str]
    dropin_files: list[ConfigFile]
    config_files: list[ConfigFile]
    config_dir: str | None
    installed: list[str]


def source_tree(
    app_dir: object,
    system_dir: str,
    unit_dir: str,
    config_dir: str,
    reserved_dropins: Iterable[str] = (),
) -> SourceTree:
    """What a source app ships, grouped as it is installed, with the host paths it lands at."""
    app_dir = str(app_dir)
    if not os.path.isdir(app_dir):
        raise AnsibleFilterError(
            f"{app_dir} is not a directory, so there is no 'source' app to install from; "
            "check systemd_app_apps_dir and systemd_app_name"
        )

    quadlet_files = _flat_files(os.path.join(app_dir, "quadlet"))
    unit_files = _flat_files(os.path.join(app_dir, "unit"))
    dropin_files = _dropin_files(os.path.join(app_dir, "unit"), frozenset(reserved_dropins))
    config_source = os.path.join(app_dir, "config")
    has_config = os.path.isdir(config_source)
    config_files = _tree_files(config_source) if has_config else []

    installed = sorted(
        [posixpath.join(system_dir, os.path.basename(path)) for path in quadlet_files]
        + [posixpath.join(unit_dir, os.path.basename(path)) for path in unit_files]
        + [posixpath.join(unit_dir, entry["path"]) for entry in dropin_files]
        + [posixpath.join(config_dir, entry["path"]) for entry in config_files]
    )
    return {
        "quadlet_files": quadlet_files,
        "unit_files": unit_files,
        "dropin_files": dropin_files,
        "config_files": config_files,
        "config_dir": config_source if has_config else None,
        "installed": installed,
    }


def _flat_files(directory: str) -> list[str]:
    """The files one level down, hidden ones excluded: what a `*` glob returns."""
    if not os.path.isdir(directory):
        return []
    return sorted(
        os.path.join(directory, name)
        for name in os.listdir(directory)
        if not name.startswith(".") and os.path.isfile(os.path.join(directory, name))
    )


def _dropin_files(unit_source: str, reserved: frozenset[str]) -> list[ConfigFile]:
    """The files in every `<unit>.d/` directly inside `unit_source`, relative to it."""
    if not os.path.isdir(unit_source):
        return []
    entries: list[ConfigFile] = []
    for directory in sorted(os.listdir(unit_source)):
        dir_path = os.path.join(unit_source, directory)
        if (directory.startswith(".") or not directory.endswith(_DROPIN_DIR_SUFFIX)
                or not os.path.isdir(dir_path)):
            continue
        unit = directory[: -len(_DROPIN_DIR_SUFFIX)]
        if not unit.endswith(_PLAIN_UNIT_SUFFIXES):
            raise AnsibleFilterError(
                f"{dir_path} is not a drop-in directory this role installs: '{unit}' is not a "
                f"unit name ending in one of {', '.join(_PLAIN_UNIT_SUFFIXES)}"
            )
        for name in sorted(os.listdir(dir_path)):
            if name.startswith("."):
                continue
            src = os.path.join(dir_path, name)
            # systemd reads only *.conf from a drop-in directory, so anything else would be
            # installed and ignored.
            if not (os.path.isfile(src) and name.endswith(_DROPIN_FILE_SUFFIX)
                    and len(name) > len(_DROPIN_FILE_SUFFIX)):
                raise AnsibleFilterError(
                    f"{src} is not a drop-in: a <unit>.d/ directory may hold only *.conf files"
                )
            if name in reserved:
                raise AnsibleFilterError(
                    f"{src} would overwrite the {name} the role writes itself; rename it"
                )
            entries.append({"src": src, "path": posixpath.join(directory, name)})
    return entries


def _tree_files(directory: str) -> list[ConfigFile]:
    """Every file under `directory`, hidden ones included, with its path relative to it."""
    entries: list[ConfigFile] = []
    for root, _dirs, files in os.walk(directory):
        for name in files:
            src = os.path.join(root, name)
            # os.walk lists a symlink to a file among the files and a symlink to a
            # directory among the dirs; only the former is a file the copy installs.
            if os.path.isfile(src):
                entries.append({"src": src, "path": os.path.relpath(src, directory)})
    return sorted(entries, key=lambda entry: entry["path"])


class FilterModule:
    """Discovery of what a source app ships."""

    def filters(self) -> dict[str, Callable[..., object]]:
        return {"source_tree": source_tree}
