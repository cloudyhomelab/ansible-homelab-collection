# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``reconcile_secrets`` filter. Runs on the controller; touches no managed host."""

from __future__ import annotations

import base64
import json

from ansible.errors import AnsibleFilterError


DOCUMENTATION = r"""
name: reconcile_secrets
short_description: Which podman secrets to store and which to drop
version_added: 1.0.0
author:
  - binarycodes (@binarycodes)
description:
  - Works out what a converge must do to the podman secret store, from three inputs - what
    the app declares now, what it last stored, and what the store actually holds.
  - Podman offers no version-independent way to read a stored secret back, which is why the
    comparison is made against recorded digests rather than against the values themselves.
  - A name is stored again when its value differs from the digest recorded for it, when it
    has no recorded digest at all, or when it is recorded yet missing from the store. That
    last case is why the store listing is needed - a secret dropped by hand, or lost with a
    store reset, leaves the record matching, so nothing would re-store it and the app would
    reference a name podman no longer knows. That surfaces as a container that will not
    start, with no hint that a deploy could have fixed it.
  - Names the app has stopped declaring are dropped, so a rename does not leave the old
    secret behind with nothing referencing it.
  - Podman cannot update a stored secret in place on every version this may run on, and
    C(create) refuses an existing name, so a rotation is a remove followed by a create.
    RV(_value.remove) is the union of rotations and drops, ready for C(podman secret rm).
  - A malformed record raises rather than being silently ignored, since treating it as empty
    would quietly re-store every secret and restart the app.
positional: recorded_b64, stored
options:
  _input:
    description: This app's secrets as the M(binarycodes.homelab.secret_digests) filter returns them.
    type: dict
    required: true
  recorded_b64:
    description:
      - The recorded digest file exactly as C(ansible.builtin.slurp) hands it over, that is
        base64 of JSON. Empty or unset when the app has stored nothing yet.
    type: str
    default: ''
  stored:
    description: The secret names podman currently holds.
    type: list
    elements: str
    default: []
"""

RETURN = r"""
_value:
  description: The three sorted name lists a converge acts on.
  type: dict
  contains:
    store:
      description: Names whose value must be written to the store.
      type: list
      elements: str
    drop:
      description: Names the app no longer declares.
      type: list
      elements: str
    remove:
      description: Names to remove first - the union of rotations and drops.
      type: list
      elements: str
"""

EXAMPLES = r"""
- name: Decide what this converge does to the secret store
  ansible.builtin.set_fact:
    plan: >-
      {{ declared | binarycodes.homelab.reconcile_secrets(recorded.content, listed.stdout_lines) }}
    # Produces: {"store": ["app-token"], "drop": ["old"], "remove": ["app-token", "old"]}
"""


def reconcile_secrets(digests, recorded_b64="", stored=None):
    """Work out which podman secrets to store and which to drop."""
    digests = digests or {}
    recorded = _recorded_digests(recorded_b64)
    stored = set(stored or [])

    changed = {name for name, digest in digests.items() if recorded.get(name) != digest}
    missing = set(digests) - stored
    store = changed | missing
    drop = set(recorded) - set(digests)

    return {"store": sorted(store), "drop": sorted(drop), "remove": sorted(store | drop)}


def _recorded_digests(recorded_b64):
    """The recorded digest file as a dict, empty when there is no file to read."""
    if not recorded_b64:
        return {}
    try:
        raw = base64.b64decode(recorded_b64).decode("utf-8").strip()
    except (ValueError, UnicodeDecodeError) as exc:
        raise AnsibleFilterError(f"recorded secret digests are not valid base64 text: {exc}")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise AnsibleFilterError(
            f"recorded secret digests are not valid JSON: {exc}. Remove the app's "
            ".secret-digests file to have the next deploy store every secret afresh."
        )
    if not isinstance(parsed, dict):
        raise AnsibleFilterError(
            f"recorded secret digests must be a JSON object, got {type(parsed).__name__}."
        )
    return parsed


class FilterModule:
    """Reconciliation of an app's declared secrets against the store."""

    def filters(self):
        return {"reconcile_secrets": reconcile_secrets}
