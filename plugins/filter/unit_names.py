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
  - The units a list of installed host paths implies. C(.container) and C(.kube) map to
    C(<name>.service), C(.pod) to C(<name>-pod.service), a plain unit file to itself.
  - Answers for paths a deploy is about to install as well as for paths a manifest recorded,
    which the C(units) return of M(binarycodes.homelab.install_manifest) cannot - that is
    computed on the host from the previous deploy, after the call that needs the answer.
  - C(.volume), C(.network), C(.image) and C(.build) are left out. They create a resource the
    role deliberately leaves behind on teardown, so naming a unit for them would imply a
    teardown that does not happen.
  - Matched one segment deep in either install directory; anything else is ignored rather than
    refused, so a whole install list can be passed in. Sorted and deduplicated.
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


# Quadlet suffix -> the suffix its generated unit gets. Only the kinds that run a container;
# see DOCUMENTATION for why the rest are left out rather than unimplemented.
_QUADLET_UNIT_SUFFIXES = {
    ".container": ".service",
    ".kube": ".service",
    ".pod": "-pod.service",
}

# Unit types stopping means something for. A .target, .slice or .scope is neither shipped by
# an app nor stopped by teardown.
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
            # The allowlist excludes '.' and '..' but not a bare '.service'.
            if name.endswith(_PLAIN_UNIT_SUFFIXES) and not name.startswith("."):
                units.add(name)
        elif parent == system_dir:
            unit = _unit_for(name, _QUADLET_UNIT_SUFFIXES)
            if unit:
                units.add(unit)

    return sorted(units)


class FilterModule:
    """What a set of installed paths makes systemd run."""

    def filters(self) -> dict[str, Callable[..., object]]:
        return {"unit_names": unit_names}
