# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The molecule scenario's own filters, which read the podman secret store for its asserts.

They live beside the plays rather than under plugins/, so nothing else loads or checks them;
a scenario that failed because of a parsing slip here would look like a role regression.
"""

import json
from typing import Any

from extensions.molecule.default.filter_plugins import secret_state as filters
from plugins.modules import podman_secrets as mod


def inspect_entry(name, value=None, labels=None):
    """One element of `podman secret inspect --showsecret` output, fields as podman names them."""
    entry: dict[str, Any] = {"ID": "abc", "Spec": {"Name": name, "Driver": {"Name": "file"}}}
    if labels is not None:
        entry["Spec"]["Labels"] = labels
    if value is not None:
        entry["SecretData"] = value
    return entry


def test_secret_state_reads_owner_digest_and_value_by_name():
    payload = json.dumps([
        inspect_entry("app-token", "t0k3n", {mod.LABEL_APP: "app", mod.LABEL_DIGEST: "sha256:aa"}),
        inspect_entry("app-extra", "more", {mod.LABEL_APP: "app", mod.LABEL_DIGEST: "sha256:bb"}),
    ])
    assert filters.secret_state(payload) == {
        "app-token": {"owner": "app", "digest": "sha256:aa", "value": "t0k3n"},
        "app-extra": {"owner": "app", "digest": "sha256:bb", "value": "more"},
    }


def test_secret_state_reads_missing_labels_and_hidden_value_as_none():
    # A secret created by hand carries no labels, and podman omits Labels entirely; without
    # --showsecret it omits SecretData too.
    payload = json.dumps([inspect_entry("stray")])
    assert filters.secret_state(payload) == {
        "stray": {"owner": None, "digest": None, "value": None},
    }


def test_secret_state_accepts_already_parsed_output():
    entries = [inspect_entry("x", "v", {mod.LABEL_APP: "a"})]
    assert filters.secret_state(entries) == {"x": {"owner": "a", "digest": None, "value": "v"}}


def test_declared_secret_state_digests_with_the_module():
    declared = filters.declared_secret_state({"app-token": "t0k3n"}, "app")
    assert declared == {
        "app-token": {"owner": "app", "digest": mod.digest("t0k3n"), "value": "t0k3n"},
    }
    assert declared["app-token"]["digest"].startswith(mod.DIGEST_ALGORITHM + ":")


def test_declared_state_is_what_a_stored_secret_reads_back_as():
    # The two halves of every secrets assertion in the scenario agree with each other when
    # the store holds exactly what the module writes.
    secrets = {"app-token": "t0k3n", "app-extra": "more"}
    payload = [
        inspect_entry(name, value, {mod.LABEL_APP: "app", mod.LABEL_DIGEST: mod.digest(value)})
        for name, value in secrets.items()
    ]
    assert filters.secret_state(payload) == filters.declared_secret_state(secrets, "app")
