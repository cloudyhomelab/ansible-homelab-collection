# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Rendering values into systemd unit directives for the systemd_app role.

systemd splits Environment= on whitespace, reads '%' as a specifier, and needs a backslash
and a quote escaped inside double quotes. Quoting and escaping are one rule, not two, so
they live together here rather than half in a template -- removing the quotes there would
silently break the escaping.

Filters run on the controller, so nothing here touches a managed host.
"""

from __future__ import annotations

import re

from ansible.errors import AnsibleFilterError


# A control character has no representation in a unit file at any quoting level. The role
# refuses one long before this point (container_problems in validation.py); this copy is
# the backstop for a caller that renders without validating first.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def systemd_env_lines(env):
    """``Environment=`` lines for a Quadlet, quoted and escaped.

    systemd splits ``Environment=`` on whitespace, so a bare value with a space would set
    the variable to its first word and read the rest as further assignments; inside double
    quotes a backslash and a quote need escaping; and '%' opens a specifier unless doubled.
    Quoting and escaping are one rule, not two, so they live together here rather than half
    in a template -- removing the quotes there would silently break the escaping.

    Sorted, so the rendered unit does not change when a call site reorders its env, which
    would otherwise restart the container for nothing.
    """
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
    """Filters for rendering systemd unit directives."""

    def filters(self):
        return {
            "systemd_env_lines": systemd_env_lines,
        }
