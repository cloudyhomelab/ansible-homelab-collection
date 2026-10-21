# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``slice_validation_errors`` filter. Runs on the controller; touches no managed host."""

from __future__ import annotations

import re
from collections.abc import Callable


DOCUMENTATION = r"""
name: slice_validation_errors
short_description: Why a list of slice units cannot be installed, one string per problem
version_added: 1.3.0
author:
  - binarycodes (@binarycodes)
description:
  - Checks each slice's name, description and settings before they are composed into a unit
    path and a unit file.
  - Guards interpolation. The name becomes a path under the unit directory, written as root, so
    a V(/) or V(..) would write somewhere else; a newline in a description or setting would add
    a directive of its own.
  - Never raises; one string per problem, so a typo is fixed in one pass.
  - C(re.fullmatch), not a C($) anchor, which would let a trailing newline through.
options:
  _input:
    description:
      - The slices, each a dict with C(name) (required, without the C(.slice) suffix),
        C(description) (optional) and C(settings) (optional dict of C([Slice]) properties).
    type: list
    elements: dict
    required: true
"""

RETURN = r"""
_value:
  description: One human-readable string per problem. Empty when every slice can be installed.
  type: list
  elements: str
"""

EXAMPLES = r"""
- name: Refuse a slice that would be written outside the unit directory
  ansible.builtin.assert:
    that:
      - slices | binarycodes.homelab.slice_validation_errors | length == 0
    # [{'name': '../x'}] produces: ["systemd_slices_units[0].name '../x' is not a slice name, ..."]
"""


# What systemd accepts in a unit name, less '\' (an escape a hand-written name never needs)
# and '@' (a slice cannot be a template). Dashes are the nesting separator, so none at either
# end and never two together: systemd refuses those slice names outright.
_NAME_RE = re.compile(r"[A-Za-z0-9:_.]+(-[A-Za-z0-9:_.]+)*")
# systemd's UNIT_NAME_MAX is 255, suffix included.
_NAME_MAX = 255 - len(".slice")
_KEY_RE = re.compile(r"[A-Z][A-Za-z0-9]*")
_KEYS = {"name", "description", "settings"}


def _has_line_break(text: str) -> bool:
    return any(c in text for c in "\n\r")


def _unit_problems(where: str, unit: object) -> list[str]:
    if not isinstance(unit, dict):
        return [f"{where} is not a dict with at least a 'name'"]
    problems: list[str] = []

    unknown = sorted(str(k) for k in unit if k not in _KEYS)
    if unknown:
        problems.append(f"{where} has unknown keys {unknown}; expected {sorted(_KEYS)}")

    name = unit.get("name")
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name) or ".." in name:
        problems.append(
            f"{where}.name {name!r} is not a slice name (letters, digits, ':', '_', '.', and "
            "'-' between them to nest; no '..')"
        )
    elif name.endswith(".slice"):
        problems.append(f"{where}.name {name!r} carries the '.slice' suffix the role adds")
    elif len(name) > _NAME_MAX:
        problems.append(f"{where}.name is {len(name)} characters, over the {_NAME_MAX} maximum")

    description = unit.get("description")
    if description is not None and (
        not isinstance(description, str) or _has_line_break(description)
    ):
        problems.append(f"{where}.description {description!r} is not a single line of text")

    settings = unit.get("settings")
    if settings is None:
        return problems
    if not isinstance(settings, dict):
        problems.append(f"{where}.settings is not a dict of [Slice] properties")
        return problems
    for key, value in settings.items():
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            problems.append(f"{where}.settings key {key!r} is not a systemd property name")
        # A bool renders as Python's 'True'. systemd would accept that, but YAML turns an
        # unquoted 'yes' into one, so the unit file would not say what the inventory does.
        if isinstance(value, bool):
            problems.append(
                f"{where}.settings.{key} is a YAML boolean; quote it, e.g. '{key}: \"yes\"'"
            )
        elif not isinstance(value, (str, int)) or (
            isinstance(value, str) and _has_line_break(value)
        ):
            problems.append(
                f"{where}.settings.{key} {value!r} is not a single-line string or an integer"
            )
    return problems


def slice_validation_errors(units: object) -> list[str]:
    """Why these slices cannot be installed, one string per problem; empty means they can."""
    if not isinstance(units, list):
        return ["systemd_slices_units is not a list"]
    problems: list[str] = []
    seen: set[str] = set()
    for index, unit in enumerate(units):
        where = f"systemd_slices_units[{index}]"
        problems.extend(_unit_problems(where, unit))
        name = unit.get("name") if isinstance(unit, dict) else None
        if isinstance(name, str):
            if name in seen:
                problems.append(f"{where}.name {name!r} is listed twice")
            seen.add(name)
    return problems


class FilterModule:
    """Input checks for the slice units the systemd_slices role installs."""

    def filters(self) -> dict[str, Callable[..., object]]:
        return {"slice_validation_errors": slice_validation_errors}
