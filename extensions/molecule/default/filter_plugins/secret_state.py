# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The podman secret store as the scenario asserts it: read from podman, and as declared.

Loaded by Ansible from the play's own directory, so it never ships (`extensions` is in
`build_ignore`) and is not part of the collection's public API - which is why it carries no
DOCUMENTATION and the one-plugin-per-file rule does not apply. `secret_state` reads
`podman secret inspect --showsecret` output into `{name: {owner, digest, value}}`;
`declared_secret_state` builds the same shape from the values a play declares, so every
secrets assertion is one dict equality.

The digest comes from the `podman_secrets` module itself rather than being rebuilt here: the
scenario checks that the store carries what the module wrote, and a module that changed
algorithm would otherwise fail it for the wrong reason.
"""

import importlib.util
import json
import pathlib

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[4] / "plugins" / "modules" / "podman_secrets.py"
)
_spec = importlib.util.spec_from_file_location("molecule_podman_secrets", _MODULE_PATH)
podman_secrets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(podman_secrets)


def secret_state(inspect_json):
    """`{name: {owner, digest, value}}` from `podman secret inspect --showsecret` output.

    A label podman does not have, or a value it was not asked to show, reads as None
    rather than raising, so a secret created by hand without labels can be asserted on.
    """
    entries = json.loads(inspect_json) if isinstance(inspect_json, str) else inspect_json
    state = {}
    for entry in entries:
        spec = entry.get("Spec") or {}
        labels = spec.get("Labels") or {}
        state[spec["Name"]] = {
            "owner": labels.get(podman_secrets.LABEL_APP),
            "digest": labels.get(podman_secrets.LABEL_DIGEST),
            "value": entry.get("SecretData"),
        }
    return state


def declared_secret_state(secrets, app):
    """What `secret_state` must read back once `app`'s declared `secrets` are stored."""
    return {
        name: {"owner": app, "digest": podman_secrets.digest(value), "value": value}
        for name, value in secrets.items()
    }


class FilterModule(object):
    def filters(self):
        return {
            "secret_state": secret_state,
            "declared_secret_state": declared_secret_state,
        }
