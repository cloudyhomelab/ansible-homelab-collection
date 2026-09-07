# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``app_validation_errors`` filter. Runs on the controller; touches no managed host."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable


DOCUMENTATION = r"""
name: app_validation_errors
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
    being deployed, C(systemd_app_apps_dir) only for a C(source) one being deployed. A
    decommission of either kind works from the host alone and needs neither.
  - A C(source) app being deployed must also have its directory under O(apps_dir), checked on
    the controller where that directory is. Deploying without one would install nothing and
    still report success, and on an app already on the host would prune every file the last
    deploy installed. Not checked for C(absent), which works from the host alone and has to
    keep working once the tree is gone.
  - A data directory is created as root with a caller-supplied owner and removed with the
    app's home on C(absent), so it may not be absolute or climb with C(..); relative and
    C(..)-free is what makes reaching another app's tree impossible, rather than a prefix
    check on a path the caller composed.
  - A secret's name is passed to C(podman secret create) as root and written into a Quadlet's
    C(Secret=) line, so it has the same shape as the app's name. For an C(inline) app the
    role also derives the variable the container sees from it - upper-cased, dashes as
    underscores - so the name is further limited to letters, digits and dashes; an
    underscore or a dot would either not spell a variable or collide with a dashed name.
    Checked by name only, so no value can reach a failure message.
  - Never raises, and returns one string per problem rather than stopping at the first, so a
    typo at a call site is reported in full and fixed in one pass.
positional: kind, state, image, apps_dir, data_dirs, secret_names
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
    description: The controller directory holding the app definitions, required when O(kind=source) and O(state=present).
    type: str
    default: ''
  data_dirs:
    description: Entries of C(systemd_app_data_dirs), each a mapping with a C(path).
    type: list
    elements: dict
    default: []
  secret_names:
    description: The keys of the app's C(secrets.sops.yaml), never its values.
    type: list
    elements: str
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
      - app_name | binarycodes.homelab.app_validation_errors(kind=app_kind, state='present', image=app_image) | length == 0
    # An 'inline' app with no image produces: ["systemd_app_image is required for an 'inline' app ..."]
"""


# One plain path segment, and what `podman secret create` and `ContainerName=` accept.
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

_KINDS = ("source", "inline")
_STATES = ("present", "absent")

# An empty segment is a doubled or trailing slash; the other two are how a path climbs.
_FORBIDDEN_SEGMENTS = frozenset(["", ".", ".."])

# A secret name that, upper-cased with dashes as underscores, spells a legal environment
# variable and cannot collide with another name doing the same.
_INLINE_SECRET_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]*")


def app_validation_errors(name: object, kind: object = "", state: object = "present",
                          image: object = "", apps_dir: object = "",
                          data_dirs: Iterable[object] | None = None,
                          secret_names: Iterable[object] | None = None) -> list[str]:
    """Why this app cannot be deployed or decommissioned as named, one string per problem."""
    problems: list[str] = []

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

    # 'source' installs from a directory under apps_dir, which has no default; a decommission
    # reads nothing from the controller, and has to keep working once the tree is gone.
    if kind == "source" and state == "present" and not _present(apps_dir):
        problems.append(
            "systemd_app_apps_dir is required to deploy a 'source' app: the directory the "
            "app's own directory sits in, normally set once as a play variable"
        )
    elif kind == "source" and state == "present" and _NAME_RE.fullmatch(name):
        app_dir = os.path.join(str(apps_dir), name)
        if not os.path.isdir(app_dir):
            problems.append(
                f"'source'-kind app {name} has no directory at {app_dir}; deploying it would "
                "install nothing and still report success, and on an app already on the host "
                "it would remove every file the last deploy installed. Check "
                "systemd_app_apps_dir: it is normally set once as a play variable, and a "
                "value composed from playbook_dir moves when the playbook does"
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

    for secret in secret_names or []:
        secret = str(secret)
        if not _NAME_RE.fullmatch(secret):
            problems.append(
                f"secrets.sops.yaml key {secret!r} is not a podman secret name (letters, digits, "
                "dot, dash or underscore, not starting with a dot)"
            )
        elif kind == "inline" and not _INLINE_SECRET_RE.fullmatch(secret):
            problems.append(
                f"secrets.sops.yaml key {secret!r} cannot name the variable an 'inline' app's "
                "container sees: upper-cased with dashes as underscores, so letters, digits "
                "and dashes only, starting with a letter"
            )

    return problems


def _present(value: object) -> bool:
    """Whether a required scalar was given: not None, and not empty once stringified."""
    return value is not None and str(value) != ""


class FilterModule:
    """Input checks for the app an invocation of the role acts on."""

    def filters(self) -> dict[str, Callable[..., object]]:
        return {"app_validation_errors": app_validation_errors}
