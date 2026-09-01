# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``systemd_env_lines`` filter. Runs on the controller; touches no managed host."""

from __future__ import annotations

import re

from ansible.errors import AnsibleFilterError


DOCUMENTATION = r"""
name: systemd_env_lines
short_description: Quote and escape a mapping into systemd C(Environment=) lines
version_added: 1.0.0
author:
  - binarycodes (@binarycodes)
description:
  - Renders a mapping as C(Environment=) directives for a systemd unit or Quadlet.
  - systemd splits C(Environment=) on whitespace, so a bare value with a space would set the
    variable to its first word and read the rest as further assignments. Inside double
    quotes a backslash and a quote need escaping, and a lone C(%) opens a specifier unless
    doubled.
  - Quoting and escaping are one rule, not two, which is why they live together here rather
    than half in a template - removing the quotes there would silently break the escaping.
  - Output is sorted by key, so the rendered unit does not change when a call site reorders
    its variables, which would otherwise restart the container for nothing.
  - A control character raises, having no representation in a unit file at any quoting level.
    The M(binarycodes.homelab.container_problems) filter refuses one long before this point;
    this is the backstop for a caller that renders without validating first, so the two
    cannot drift apart.
options:
  _input:
    description: Variable names mapped to their values. Values are stringified.
    type: dict
    required: true
"""

RETURN = r"""
_value:
  description: One quoted and escaped C(Environment=) line per key, sorted by key.
  type: list
  elements: str
"""

EXAMPLES = r"""
- name: Render environment variables into a Quadlet
  ansible.builtin.debug:
    msg: "{{ {'GREETING': 'hello world'} | binarycodes.homelab.systemd_env_lines }}"
    # Produces: ['Environment="GREETING=hello world"']
"""


_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def systemd_env_lines(env):
    """``Environment=`` lines for a Quadlet, quoted and escaped."""
    normalised = {str(key): str(value) for key, value in (env or {}).items()}
    lines = []
    for key in sorted(normalised):
        value = normalised[key]
        # Unreachable through the role, which validates before rendering (see
        # container_problems); a backstop so the two cannot drift apart.
        if _CONTROL_RE.search(key) or _CONTROL_RE.search(value):
            raise AnsibleFilterError(
                f"systemd_app_env entry {key!r} holds a control character, which cannot be "
                "written to a unit file at any quoting level"
            )
        lines.append(f'Environment="{key}={_escape_in_quotes(value)}"')
    return lines


def _escape_in_quotes(value):
    """A value as it must appear inside a double-quoted systemd directive."""
    return (
        value.replace("\\", "\\\\")   # first, or the escapes added below get doubled
        .replace('"', '\\"')
        .replace("%", "%%")             # a lone '%' would open a specifier
    )


class FilterModule:
    """Rendering of systemd unit directives."""

    def filters(self):
        return {"systemd_env_lines": systemd_env_lines}
