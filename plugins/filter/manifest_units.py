# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``manifest_units`` filter. Runs on the controller; touches no managed host."""

from __future__ import annotations

import posixpath


DOCUMENTATION = r"""
name: manifest_units
short_description: The systemd units a recorded install manifest implies
version_added: 1.0.0
author:
  - binarycodes (@binarycodes)
deprecated:
  removed_in: 2.0.0
  why: >-
    The systemd_app role reads, prunes and removes its install manifest through the
    binarycodes.homelab.install_manifest module, on the host, which returns the units the
    record implies by these same rules; nothing computes them on the controller any more.
  alternative: The C(units) return value of the M(binarycodes.homelab.install_manifest) module.
description:
  - Works out which systemd units an app is running, from the paths its last deploy
    recorded, so a decommission can stop them without being told their names.
  - What this exists for is teardown. An app's unit names are an input to the role
    (O(systemd_app_enable_units)), and a caller who does not repeat that input when setting
    C(state=absent) would otherwise have its unit files deleted while its containers keep
    running - the generated service gone from systemd's view, its C(ExecStopPost) never
    fired, and the container orphaned. The manifest is on the host and needs no input, so
    it can answer the question the caller did not.
  - A Quadlet source file is not a unit; systemd's generator makes one from it, and the
    name it makes is not always the file's own. C(.container) and C(.kube) become
    C(<name>.service), C(.pod) becomes C(<name>-pod.service).
  - Only the Quadlet kinds that run something are mapped. C(.volume), C(.network),
    C(.image) and C(.build) create a resource rather than run a container, and the role
    deliberately leaves those resources behind on teardown - a podman volume outlives the
    unit that declared it, and Caddy's certificates live in one. Naming their units here
    would suggest a teardown that this role does not do.
  - Paths are matched against the two install directories exactly, one segment deep. A
    path anywhere else is ignored rather than refused; a manifest is validated where it is
    read, and this filter is only asked what to stop.
  - The result is sorted and deduplicated, so a caller can compare or merge it without
    caring what order the manifest happened to list its paths in.
positional: system_dir, unit_dir
options:
  _input:
    description:
      - Paths recorded in the app's install manifest.
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
  description: Unit names, sorted and deduplicated. Empty when the manifest implies none.
  type: list
  elements: str
"""

EXAMPLES = r"""
- name: Stop what the app is running, whether or not the caller named it
  ansible.builtin.systemd:
    name: "{{ item }}"
    state: stopped
  loop: >-
    {{ recorded_paths
       | binarycodes.homelab.manifest_units('/etc/containers/systemd', '/etc/systemd/system') }}
  # A manifest listing /etc/containers/systemd/app.container and
  # /etc/systemd/system/app-extra.service yields ['app-extra.service', 'app.service'].
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


def _unit_for(name, suffixes):
    """The unit `name` implies, or None when this filter does not map it."""
    for suffix, unit_suffix in suffixes.items():
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)] + unit_suffix
    return None


def manifest_units(paths, system_dir, unit_dir):
    """The systemd units a recorded install manifest implies."""
    units = set()

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
    """What a decommission has to stop, read from the host rather than from the caller."""

    def filters(self):
        return {"manifest_units": manifest_units}
