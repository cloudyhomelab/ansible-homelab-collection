# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``secret_digests`` filter. Runs on the controller; touches no managed host."""

from __future__ import annotations

import hashlib


DOCUMENTATION = r"""
name: secret_digests
short_description: SHA-256 of each podman secret value, keyed by secret name
version_added: 1.0.0
author:
  - binarycodes (@binarycodes)
description:
  - Returns the SHA-256 hex digest of every value in the input mapping, under the same keys.
  - Podman offers no version-independent way to read a stored secret back, so a converge
    cannot compare a declared value against what the host holds. A digest recorded alongside
    the app is what tells the next run which values actually changed - without it, every
    converge would have to recreate the secrets and restart the app to be sure.
  - The recorded key set doubles as the record of which secrets an app owns, which is how a
    later deploy drops one the app has stopped declaring, and how a decommission knows what
    to remove without the encrypted file.
  - Values are stringified before hashing, so a non-string scalar digests consistently.
options:
  _input:
    description: Podman secret names mapped to their values.
    type: dict
    required: true
"""

RETURN = r"""
_value:
  description: The same keys, each mapped to the SHA-256 hex digest of its value.
  type: dict
"""

EXAMPLES = r"""
- name: Record a digest per secret, to compare against on the next run
  ansible.builtin.set_fact:
    digests: "{{ {'app-token': 's3cret'} | binarycodes.homelab.secret_digests }}"
    # Produces one sha256 hex digest per name.
"""


def secret_digests(values):
    """SHA-256 of each secret's value, keyed by secret name."""
    return {
        str(name): hashlib.sha256(str(value).encode("utf-8")).hexdigest()
        for name, value in (values or {}).items()
    }


class FilterModule:
    """Digest bookkeeping for podman secrets."""

    def filters(self):
        return {"secret_digests": secret_digests}
