# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Podman secret bookkeeping for the systemd_app role.

Podman offers no version-independent way to read a stored secret back, so a converge
cannot compare values with what is on the host. These two filters do the comparing
instead: one records a digest per secret, the other works out what to store and what to
drop from that record, the app's declared set, and what the store actually holds.

Filters run on the controller, so nothing here touches a managed host.
"""

from __future__ import annotations

import base64
import hashlib
import json

from ansible.errors import AnsibleFilterError


def secret_digests(values):
    """SHA-256 of each secret's value, keyed by secret name.

    Podman offers no version-independent way to read a stored secret back, so a digest is
    what tells the next converge which values actually changed.
    """
    return {
        str(name): hashlib.sha256(str(value).encode("utf-8")).hexdigest()
        for name, value in (values or {}).items()
    }


def reconcile_secrets(digests, recorded_b64="", stored=None):
    """Work out which podman secrets to store and which to drop.

    ``digests`` is this app's secrets as secret_digests returns them, ``recorded_b64``
    the digest file exactly as ``slurp`` hands it over (base64 of JSON, empty when the file
    is not there), and ``stored`` the names podman currently holds.

    Returns ``{store, drop, remove}``. A name is stored again when its value differs from
    the digest recorded for it, when it has no recorded digest at all, or when it is
    recorded yet missing from the store -- dropped by hand, or lost with a store reset,
    which the record alone cannot see. Names the app has stopped declaring are dropped, so
    a rename does not leave the old secret behind with nothing referencing it.

    ``remove`` is what to hand ``podman secret rm``: podman cannot update a stored secret
    in place on every version this may run on and ``create`` refuses an existing name, so a
    rotation is a remove followed by a create, and the same pass clears the drops.
    """
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
    """Filters for the systemd_app role's secret bookkeeping."""

    def filters(self):
        return {
            "secret_digests": secret_digests,
            "reconcile_secrets": reconcile_secrets,
        }
