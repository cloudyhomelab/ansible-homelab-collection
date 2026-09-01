# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``container_problems`` filter. Runs on the controller; touches no managed host."""

from __future__ import annotations

import re


DOCUMENTATION = r"""
name: container_problems
short_description: Why an app's Quadlet cannot be rendered, one string per problem
version_added: 1.0.0
author:
  - binarycodes (@binarycodes)
description:
  - Checks what would be interpolated into a rendered Quadlet unit file.
  - What this guards is interpolation. These values are written into a unit file rather than
    passed to a module, so a stray character does not fail the task that writes them. A
    newline ends the line and turns whatever follows into a further directive, which quoting
    cannot rescue, and systemd mis-parses the result without complaining.
  - Never raises, and returns one string per problem rather than stopping at the first, so a
    typo at a call site is reported in full and fixed in one pass.
  - Matching uses C(re.fullmatch) rather than a C($)-anchored C(re.match), because C($) also
    matches just before a trailing newline, which would let a trailing newline through.
  - Values are not checked for spaces, quotes or percent signs. The
    M(binarycodes.homelab.systemd_env_lines) filter quotes and escapes those, so they are
    legal input. Only what cannot survive a unit file at all is refused.
  - Problems name the offending key, never the value, so a failure message cannot carry a
    secret into a log.
positional: description, volumes, publish_ports, container_options, service_options
options:
  _input:
    description:
      - Environment variables destined for C(Environment=) lines. Keys must spell legal
        variable names - letters, digits and underscore, with no leading digit.
    type: dict
    required: true
  description:
    description: The unit description, for the C(Description=) line.
    type: str
    default: ''
  volumes:
    description: Raw C(Volume=) values, one Quadlet line per entry.
    type: list
    elements: str
    default: []
  publish_ports:
    description: Raw C(PublishPort=) values, one Quadlet line per entry.
    type: list
    elements: str
    default: []
  container_options:
    description: Raw lines appended to the C([Container]) section.
    type: list
    elements: str
    default: []
  service_options:
    description: Raw lines appended to the C([Service]) section.
    type: list
    elements: str
    default: []
"""

RETURN = r"""
_value:
  description: One human-readable string per problem. Empty when the Quadlet can be rendered.
  type: list
  elements: str
"""

EXAMPLES = r"""
- name: Refuse values that would not survive into the unit file
  ansible.builtin.assert:
    that:
      - app_env | binarycodes.homelab.container_problems(app_description, app_volumes) | length == 0
    # A newline in a volume produces: ["systemd_app_volumes entry '...' holds a control character; ..."]
"""


# The left side of an Environment= assignment, so what a shell would accept as a variable.
_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# A control character has no representation in a unit file: a newline ends the line and
# turns whatever follows into a further directive, which quoting cannot rescue. Refused
# here; the same pattern in systemd_env_lines is the backstop at render time.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def container_problems(env, description="", volumes=None, publish_ports=None,
                       container_options=None, service_options=None):
    """Why this app's Quadlet cannot be rendered, one string per problem."""
    problems = []

    for key, value in (env or {}).items():
        if not _ENV_KEY_RE.fullmatch(str(key)):
            problems.append(
                f"systemd_app_env key {str(key)!r} is not a legal variable name (letters, "
                "digits and underscore, no leading digit)"
            )
        # Reported by key, not by value: a failure message is no place for either, and the
        # key is what the caller has to go and fix.
        if isinstance(value, str) and _CONTROL_RE.search(value):
            problems.append(
                f"the value of systemd_app_env key {str(key)!r} holds a control character"
            )

    if description is not None and _CONTROL_RE.search(str(description)):
        problems.append("systemd_app_description holds a control character")

    raw = (
        ("systemd_app_volumes", volumes),
        ("systemd_app_publish_ports", publish_ports),
        ("systemd_app_container_options", container_options),
        ("systemd_app_service_options", service_options),
    )
    for name, lines in raw:
        for line in lines or []:
            if _CONTROL_RE.search(str(line)):
                problems.append(
                    f"{name} entry {str(line)!r} holds a control character; each entry is "
                    "one Quadlet line, so use a further entry rather than a newline"
                )

    return problems


class FilterModule:
    """Input checks for an app's rendered Quadlet."""

    def filters(self):
        return {"container_problems": container_problems}
