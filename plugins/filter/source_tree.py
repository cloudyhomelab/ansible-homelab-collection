# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``source_tree`` filter. Runs on the controller, where the app definitions are."""

from __future__ import annotations

import os
import posixpath
from collections.abc import Callable
from typing import TypedDict

from ansible.errors import AnsibleFilterError


DOCUMENTATION = r"""
name: source_tree
short_description: What a C(source) app ships, and the host paths it installs to
version_added: 1.1.0
author:
  - binarycodes (@binarycodes)
description:
  - Reads one app's directory on the controller and returns the files a C(source) deploy
    installs, grouped as the role installs them, together with the absolute host paths they
    land at, which is what the app's install manifest records.
  - C(quadlet/) and C(unit/) are flat and installed by basename, so only their regular files
    are listed, hidden ones excluded, as a C(*) glob would. C(config/) is a tree copied with
    its layout, so every file under it is listed, hidden ones and nested ones included, with
    its path relative to C(config/).
  - A symlink to a file counts as a file in every group, because the copy that installs the
    tree follows symlinks and puts a regular file on the host, and the manifest has to list
    every file the copy installs or the next deploy prunes something it should have kept.
  - Raises when the directory does not exist. A glob returns nothing for a missing path, and
    a deploy that read nothing would install nothing, report success, and prune every path
    the last deploy recorded.
  - Runs on the controller as C(fileglob) does; the paths in RV(_value.quadlet_files),
    RV(_value.unit_files) and each RV(_value.config_files) entry's C(src) are controller
    paths, and RV(_value.installed) are host paths.
positional: system_dir, unit_dir, config_dir
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
  # For quadlet/myapp.container, unit/myapp-extra.service and config/nested/app.conf:
  #   quadlet_files: ['/srv/apps/myapp/quadlet/myapp.container']
  #   unit_files:    ['/srv/apps/myapp/unit/myapp-extra.service']
  #   config_files:  [{'src': '/srv/apps/myapp/config/nested/app.conf', 'path': 'nested/app.conf'}]
  #   config_dir:    '/srv/apps/myapp/config'
  #   installed:     ['/etc/containers/systemd/myapp.container',
  #                   '/etc/systemd/system/myapp-extra.service',
  #                   '/var/app/myapp/config/nested/app.conf']
"""


class ConfigFile(TypedDict):
    src: str
    path: str


class SourceTree(TypedDict):
    """The filter's return; see RETURN."""

    quadlet_files: list[str]
    unit_files: list[str]
    config_files: list[ConfigFile]
    config_dir: str | None
    installed: list[str]


def source_tree(app_dir: object, system_dir: str, unit_dir: str, config_dir: str) -> SourceTree:
    """What a source app ships, grouped as it is installed, with the host paths it lands at."""
    app_dir = str(app_dir)
    if not os.path.isdir(app_dir):
        raise AnsibleFilterError(
            f"{app_dir} is not a directory, so there is no 'source' app to install from; "
            "check systemd_app_apps_dir and systemd_app_name"
        )

    quadlet_files = _flat_files(os.path.join(app_dir, "quadlet"))
    unit_files = _flat_files(os.path.join(app_dir, "unit"))
    config_source = os.path.join(app_dir, "config")
    has_config = os.path.isdir(config_source)
    config_files = _tree_files(config_source) if has_config else []

    installed = sorted(
        [posixpath.join(system_dir, os.path.basename(path)) for path in quadlet_files]
        + [posixpath.join(unit_dir, os.path.basename(path)) for path in unit_files]
        + [posixpath.join(config_dir, entry["path"]) for entry in config_files]
    )
    return {
        "quadlet_files": quadlet_files,
        "unit_files": unit_files,
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
