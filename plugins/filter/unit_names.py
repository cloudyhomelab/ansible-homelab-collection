# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``unit_names`` filter. Runs on the controller; touches no managed host."""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Iterable, Mapping


DOCUMENTATION = r"""
name: unit_names
short_description: The systemd units a set of installed paths implies
version_added: 1.2.0
author:
  - binarycodes (@binarycodes)
description:
  - Works out which systemd units a list of installed host paths implies, so a caller that
    knows what an app ships does not have to be told what that makes systemd run.
  - Asked in two directions. Of the paths a deploy is about to install, to learn which units
    it must write a drop-in for - answered before anything is on the host, which is why this
    is a filter and not the C(units) return of the
    M(binarycodes.homelab.install_manifest) module, computed on the host from what the
    *previous* deploy recorded. Of the paths a previous deploy recorded, to learn what a
    decommission has to stop without being told the names.
  - A Quadlet source file is not a unit; systemd's generator makes one from it, and the
    name it makes is not always the file's own. C(.container) and C(.kube) become
    C(<name>.service), C(.pod) becomes C(<name>-pod.service).
  - Only the Quadlet kinds that run something are mapped. C(.volume), C(.network),
    C(.image) and C(.build) create a resource rather than run a container, and the role
    deliberately leaves those resources behind on teardown - a podman volume outlives the
    unit that declared it, and Caddy's certificates live in one. Naming their units here
    would suggest a teardown that this role does not do.
  - Paths are matched against the two install directories exactly, one segment deep. A
    path anywhere else is ignored rather than refused, which is what lets a whole install
    list be passed in - a config file, or a drop-in already below C(<unit>.d/), contributes
    no unit rather than an error.
  - The result is sorted and deduplicated, so a caller can compare or merge it without
    caring what order the paths happened to arrive in.
positional: system_dir, unit_dir
options:
  _input:
    description:
      - Absolute host paths, either the ones a deploy installs or the ones a manifest
        recorded.
    type: list
    elements: str
    required: true
  system_dir:
    description: The Quadlet install directory, normally C(/etc/containers/systemd).
    type: str
    required: true
  unit_dir:
    description: The plain-unit install directory, normally C(/etc/systemd/system).
    type: str
    required: true
"""

RETURN = r"""
_value:
  description: Unit names, sorted and deduplicated. Empty when the paths imply none.
  type: list
  elements: str
"""

EXAMPLES = r"""
- name: Order every unit this deploy installs after the app's decrypt unit
  ansible.builtin.template:
    src: private-dropin.conf.j2
    dest: "/etc/systemd/system/{{ item }}.d/10-private.conf"
  loop: >-
    {{ installed_paths
       | binarycodes.homelab.unit_names('/etc/containers/systemd', '/etc/systemd/system') }}
  # Installing /etc/containers/systemd/app.container and
  # /etc/systemd/system/app-extra.service yields ['app-extra.service', 'app.service'].

- name: Stop what the app is running, whether or not the caller named it
  ansible.builtin.systemd:
    name: "{{ item }}"
    state: stopped
  loop: >-
    {{ recorded_paths
       | binarycodes.homelab.unit_names('/etc/containers/systemd', '/etc/systemd/system') }}
"""


# Quadlet file suffix -> the suffix systemd's generator gives the unit it produces. Only
# the kinds that run a container: see this filter's documentation for why the rest are
# left out rather than merely unimplemented.
_QUADLET_UNIT_SUFFIXES = {
    ".container": ".service",
    ".kube": ".service",
    ".pod": "-pod.service",
}

# Unit types a plain unit file may be, and that stopping means something for. A .target,
# .slice or .scope is not something an app ships and not something teardown stops.
_PLAIN_UNIT_SUFFIXES = (
    ".service",
    ".socket",
    ".timer",
    ".path",
    ".mount",
    ".automount",
)


def _unit_for(name: str, suffixes: Mapping[str, str]) -> str | None:
    """The unit `name` implies, or None when this filter does not map it."""
    for suffix, unit_suffix in suffixes.items():
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)] + unit_suffix
    return None


def unit_names(paths: Iterable[object] | None, system_dir: str, unit_dir: str) -> list[str]:
    """The systemd units a set of installed host paths implies."""
    units: set[str] = set()

    for path in paths or []:
        parent, name = posixpath.split(str(path))
        if not name:
            continue

        if parent == unit_dir:
            # A bare suffix is a name systemd has no unit for, and the manifest allowlist
            # admits it: it excludes '.' and '..' but not '.service'.
            if name.endswith(_PLAIN_UNIT_SUFFIXES) and not name.startswith("."):
                units.add(name)
        elif parent == system_dir:
            unit = _unit_for(name, _QUADLET_UNIT_SUFFIXES)
            if unit:
                units.add(unit)

    return sorted(units)


class FilterModule:
    """What a set of installed paths makes systemd run, derived rather than declared."""

    def filters(self) -> dict[str, Callable[..., object]]:
        return {"unit_names": unit_names}
