# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``app_problems`` filter. Runs on the controller; touches no managed host."""

from __future__ import annotations

import re


DOCUMENTATION = r"""
name: app_problems
short_description: Why an app cannot be deployed or decommissioned as named, one string per problem
version_added: 1.1.0
author:
  - binarycodes (@binarycodes)
description:
  - Checks the inputs that select what the role does and where - the app's name, its kind
    and state, and the two inputs each kind requires of the other - plus the directories an
    app asks to have created under its home.
  - The name composes every install path, a C(ContainerName=) line, a route snippet's
    filename and, on C(absent), a recursive delete of the app's home. So it must be one plain
    path segment. Matched with C(re.fullmatch) rather than a C($)-anchored pattern, because
    C($) also matches just before a trailing newline, and none of the files the name is
    written into would fail on one.
  - The conditional requirements live here because a role argument spec can only mark an
    option required outright, and C(systemd_app_image) is required only for an C(inline) app
    being deployed, C(systemd_app_apps_dir) only for a C(source) one.
  - A data directory is created as root with a caller-supplied owner and removed with the
    app's home on C(absent), so it may not be absolute or climb with C(..); relative and
    C(..)-free is what makes reaching another app's tree impossible, rather than a prefix
    check on a path the caller composed.
  - Never raises, and returns one string per problem rather than stopping at the first, so a
    typo at a call site is reported in full and fixed in one pass.
positional: kind, state, image, apps_dir, data_dirs
options:
  _input:
    description: The app's name, C(systemd_app_name).
    type: str
    required: true
  kind:
    description: C(source) or C(inline).
    type: str
    default: ''
  state:
    description: C(present) or C(absent).
    type: str
    default: present
  image:
    description: The image reference, required when O(kind=inline) and O(state=present).
    type: str
    default: ''
  apps_dir:
    description: The controller directory holding the app definitions, required when O(kind=source).
    type: str
    default: ''
  data_dirs:
    description: Entries of C(systemd_app_data_dirs), each a mapping with a C(path).
    type: list
    elements: dict
    default: []
"""

RETURN = r"""
_value:
  description: One human-readable string per problem. Empty when the app can be acted on.
  type: list
  elements: str
"""

EXAMPLES = r"""
- name: Refuse a call site before anything is composed from it
  ansible.builtin.assert:
    that:
      - app_name | binarycodes.homelab.app_problems(kind=app_kind, state='present', image=app_image) | length == 0
    # An 'inline' app with no image produces: ["systemd_app_image is required for an 'inline' app ..."]
"""


# One plain path segment, and what `podman secret create` and `ContainerName=` accept.
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

_KINDS = ("source", "inline")
_STATES = ("present", "absent")

# An empty segment is a doubled or trailing slash; the other two are how a path climbs.
_FORBIDDEN_SEGMENTS = frozenset(["", ".", ".."])


def app_problems(name, kind="", state="present", image="", apps_dir="", data_dirs=None):
    """Why this app cannot be deployed or decommissioned as named, one string per problem."""
    problems = []

    name = "" if name is None else str(name)
    if not _NAME_RE.fullmatch(name):
        problems.append(
            f"systemd_app_name {name!r} is not one plain path segment (letters, digits, dot, "
            "dash or underscore, not starting with a dot)"
        )

    kind = "" if kind is None else str(kind)
    if kind not in _KINDS:
        problems.append(f"systemd_app_kind {kind!r} is not one of {', '.join(map(repr, _KINDS))}")

    state = "" if state is None else str(state)
    if state not in _STATES:
        problems.append(f"systemd_app_state {state!r} is not one of {', '.join(map(repr, _STATES))}")

    # 'inline' renders from an image, so present requires one; absent works from the host.
    if kind == "inline" and state == "present" and not _present(image):
        problems.append(
            "systemd_app_image is required for an 'inline' app being deployed (state=present)"
        )

    # 'source' installs from a directory under apps_dir, which has no default.
    if kind == "source" and not _present(apps_dir):
        problems.append(
            "systemd_app_apps_dir is required for a 'source' app: the directory the app's "
            "own directory sits in, normally set once as a play variable"
        )

    for entry in data_dirs or []:
        path = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(path, str) or not path:
            problems.append(f"systemd_app_data_dirs entry {entry!r} has no 'path' string")
        elif path.startswith("/"):
            problems.append(
                f"systemd_app_data_dirs path {path!r} is absolute; an entry names a path "
                "relative to the app's home ('data', not '/var/app/<app>/data')"
            )
        elif any(segment in _FORBIDDEN_SEGMENTS for segment in path.split("/")):
            problems.append(
                f"systemd_app_data_dirs path {path!r} has an empty, '.' or '..' segment, "
                "which could climb out of the app's home"
            )

    return problems


def _present(value):
    """Whether a required scalar was given: not None, and not empty once stringified."""
    return value is not None and str(value) != ""


class FilterModule:
    """Input checks for the app an invocation of the role acts on."""

    def filters(self):
        return {"app_problems": app_problems}
