#!/usr/bin/python
# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``podman_secrets`` module. Runs on the managed host, where the store is."""

from __future__ import annotations

DOCUMENTATION = r"""
module: podman_secrets
short_description: Reconcile one app's podman secrets against the store
version_added: 1.1.0
author:
  - binarycodes (@binarycodes)
description:
  - Makes the rootful podman secret store hold exactly the secrets an app declares, and
    removes them again when the app goes. One call per app, on the host.
  - Ownership and change detection live on the secret itself, as labels, so the store is the
    only record. Every secret this module creates carries the app's name and a digest of its
    value in the form C(<algorithm>:<hex>), C(sha256:...) today. A declared name that is
    missing is created; one whose recorded digest differs from the declared value is removed
    and re-created, since podman cannot update a secret in place; one this app owns but no
    longer declares is removed. A secret removed by hand is simply missing and comes back on
    the next run.
  - The digest names its algorithm so the default can change without a rotation. A secret
    recorded under an older algorithm is verified with that algorithm and left alone while
    its value matches; it moves to the current one when its value next changes. Only a
    digest under an algorithm this Python cannot compute is treated as changed.
  - A declared name that exists carrying another app's label is refused, so two apps cannot
    silently trade a secret. One that exists with no ownership label at all - stored by hand,
    or by this collection before it recorded ownership - is taken to be this app's with an
    unknown value, and is removed and re-created with labels.
  - Values reach C(podman secret create) on standard input, never on the command line, because
    C(/proc/<pid>/cmdline) is world-readable for as long as the process lives.
  - Names are host-global. Prefix them with the app's name to keep two apps apart; the label
    is what this module checks, but a clear name is what an operator reading
    C(podman secret ls) sees.
  - Supports check mode, in which the plan is reported and nothing is changed, and diff mode,
    which shows the owned names before and after. No return value or diff carries a value.
notes:
  - Requires podman 4.5 or newer, where C(podman secret create) learned C(--label).
options:
  app:
    description:
      - The owning app, recorded on every secret created as the label
        C(io.binarycodes.homelab.app); the value's digest goes in
        C(io.binarycodes.homelab.digest). The store is reconciled for this app alone.
    type: str
    required: true
  secrets:
    description:
      - The secrets the app declares, name to value. Names are letters, digits, dot, dash or
        underscore, not starting with a dot - what C(podman secret create) accepts. Values are
        stringified; an empty value is refused here rather than at container start, where it
        surfaces as an app that will not boot with no explanation.
      - Ignored when O(state=absent).
    type: dict
    default: {}
  adopt:
    description:
      - Names to treat as this app's even though they carry no ownership label, for secrets
        stored before this collection recorded ownership. On O(state=present) a declared name
        in that condition is adopted without being listed here; this exists for
        O(state=absent), which would otherwise leave such secrets behind. A listed name that
        belongs to another app is refused.
    type: list
    elements: str
    default: []
  adopt_file:
    description:
      - A file on the host whose JSON object's keys are added to O(adopt), for a record of
        names kept where the secrets are rather than on the controller. Releases of this
        collection before 1.1.0 wrote one per app. A missing file adds nothing. A file that
        is not a JSON object is refused, naming the file, so a decommission stops before
        touching the store rather than leaving secrets whose names it cannot read; remove the
        file to have the decommission leave those secrets for a hand C(podman secret rm).
    type: path
  state:
    description:
      - C(present) makes the store hold the declared secrets and nothing else of this app's.
        C(absent) removes every secret this app owns.
    type: str
    choices: [present, absent]
    default: present
  executable:
    description: The podman binary to run.
    type: str
    default: podman
"""

EXAMPLES = r"""
- name: Store the app's secrets, rotating any whose value changed
  binarycodes.homelab.podman_secrets:
    app: myapp
    secrets:
      myapp-db-password: "{{ vault_db_password }}"
      myapp-api-token: "{{ vault_api_token }}"
  register: myapp_secrets

- name: Restart the app when a secret changed, since podman reads secrets at container creation
  ansible.builtin.systemd:
    name: myapp.service
    state: restarted
  when: myapp_secrets.changed

- name: Remove every secret the app owns on decommission
  binarycodes.homelab.podman_secrets:
    app: myapp
    state: absent

- name: Remove them along with the secrets a pre-1.1.0 release recorded for the app in a file
  binarycodes.homelab.podman_secrets:
    app: myapp
    state: absent
    adopt_file: /var/app/myapp/.secret-digests
"""

RETURN = r"""
stored:
  description: Names created on this run - new, rotated, adopted, or re-created after drift.
  type: list
  elements: str
  returned: always
removed:
  description: Names removed and not re-created - dropped by the app, or all of them on absent.
  type: list
  elements: str
  returned: always
unchanged:
  description: Declared names already stored with the declared value.
  type: list
  elements: str
  returned: always
"""

import errno
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Protocol, TypedDict

from ansible.module_utils.basic import AnsibleModule

LABEL_APP = "io.binarycodes.homelab.app"
LABEL_DIGEST = "io.binarycodes.homelab.digest"

# The algorithm new digests are written with. The label value names its algorithm, OCI-style
# (`sha256:<hex>`), so changing this rotates nothing: a secret recorded under the old one is
# verified with the old one until its value changes.
DIGEST_ALGORITHM = "sha256"

# What `podman secret create` accepts as a name, and what the role accepts for the same
# reason: it also has to be a filename-safe token in a Quadlet's Secret= line.
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# Enough stderr to diagnose a podman failure, not enough to flood a task result.
_STDERR_LIMIT = 300

# One stored secret's labels, as `podman secret inspect` reports them.
Labels = dict[str, str]


class Runner(Protocol):
    """What `Store` calls podman through: `module.run_command`, or a fake in the tests."""

    def __call__(self, argv: list[str], stdin: bytes | None) -> tuple[int, str, str]: ...


class Diff(TypedDict):
    before: str
    after: str


class Result(TypedDict):
    """The module's return, less the fields AnsibleModule adds; see RETURN."""

    changed: bool
    stored: list[str]
    removed: list[str]
    unchanged: list[str]
    diff: Diff


class PodmanSecretsError(Exception):
    """A failure the module reports with fail_json; carries the fields to report."""

    def __init__(self, msg: str, **fields: object) -> None:
        super().__init__(msg)
        self.msg = msg
        self.fields = fields


def digest(value: str, algorithm: str = DIGEST_ALGORITHM) -> str:
    """The value's digest as recorded on a secret: `<algorithm>:<hex>`."""
    return "%s:%s" % (algorithm, hashlib.new(algorithm, value.encode("utf-8")).hexdigest())


def matches(recorded: str | None, value: str) -> bool:
    """Whether a recorded digest is the declared value's, under whatever algorithm it names.

    A digest with no algorithm, or under one this Python cannot compute, cannot be verified
    and so reads as changed - the safe direction, costing one rotation.
    """
    algorithm, sep, hexdigest = (recorded or "").partition(":")
    if not sep or not hexdigest or algorithm not in hashlib.algorithms_available:
        return False
    return recorded == digest(value, algorithm)


class Store:
    """The podman secret store, seen through a runner so the logic can be tested without podman.

    `run(argv, stdin)` returns `(rc, stdout, stderr)`; stdin is bytes or None.
    """

    def __init__(self, run: Runner, executable: str = "podman") -> None:
        self._run = run
        self._exe = executable

    def _podman(self, args: list[str], stdin: bytes | None = None, what: str | None = None) -> str:
        argv = [self._exe, "secret"] + list(args)
        rc, out, err = self._run(argv, stdin)
        if rc != 0:
            raise PodmanSecretsError(
                "%s failed (rc %d): %s" % (what or " ".join(argv[:3]), rc, err.strip()[:_STDERR_LIMIT]),
                rc=rc, stderr=err.strip()[:_STDERR_LIMIT],
            )
        return out

    def labels(self) -> dict[str, Labels]:
        """Every stored secret's labels, by name; {} for a secret with none."""
        out = self._podman(["ls", "--format", "{{.Name}}"], what="podman secret ls")
        names = [line.strip() for line in out.splitlines() if line.strip()]
        if not names:
            return {}
        raw = self._podman(["inspect"] + names, what="podman secret inspect")
        try:
            entries = json.loads(raw)
        except ValueError as exc:
            raise PodmanSecretsError("podman secret inspect returned something other than JSON: %s" % exc)
        stored: dict[str, Labels] = {}
        for entry in entries:
            spec = entry.get("Spec") or {}
            name = spec.get("Name") or entry.get("Name")
            if name:
                stored[name] = dict(spec.get("Labels") or {})
        return stored

    def remove(self, name: str) -> None:
        self._podman(["rm", name], what="podman secret rm %s" % name)

    def create(self, name: str, value: str, app: str) -> None:
        self._podman(
            ["create",
             "--label", "%s=%s" % (LABEL_APP, app),
             "--label", "%s=%s" % (LABEL_DIGEST, digest(value)),
             name, "-"],
            stdin=value.encode("utf-8"),
            what="podman secret create %s" % name,
        )


def _normalise(secrets: Mapping[object, object]) -> dict[str, str]:
    """The declared secrets as name -> string value, refusing what podman or the app would."""
    bad_names = sorted(str(n) for n in secrets if not _NAME_RE.fullmatch(str(n)))
    if bad_names:
        raise PodmanSecretsError(
            "secret names must be letters, digits, dot, dash or underscore, not starting "
            "with a dot: %s" % ", ".join(bad_names), names=bad_names,
        )
    empty = sorted(str(n) for n, v in secrets.items() if v is None or str(v) == "")
    if empty:
        raise PodmanSecretsError(
            "a secret must have a non-empty value; these do not: %s" % ", ".join(empty),
            names=empty,
        )
    return {str(n): str(v) for n, v in secrets.items()}


def recorded_names(path: str | None) -> list[str]:
    """The keys of the JSON object at `path`; [] when there is no file.

    The record a pre-1.1.0 release kept. Read before the store is listed, so a record that
    cannot be read fails the call before podman is asked anything.
    """
    if not path:
        return []
    try:
        with open(path, "rb") as handle:
            record = json.loads(handle.read().decode("utf-8"))
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return []
        raise PodmanSecretsError("cannot read the secret record %s: %s" % (path, exc), adopt_file=path)
    except ValueError:
        record = None
    if not isinstance(record, dict):
        raise PodmanSecretsError(
            "the secret record %s is not a JSON object, so the names it held cannot be adopted. "
            "Remove it to have the decommission leave those secrets for a hand `podman secret rm`."
            % path, adopt_file=path,
        )
    return sorted(str(name) for name in record)


def plan(app: str, secrets: Mapping[str, str], adopt: list[str], state: str,
         stored: Mapping[str, Labels]) -> tuple[list[str], list[str], list[str]]:
    """What to remove and what to create, from the declared set and the store's labels.

    Returns (remove, create, unchanged): `remove` and `unchanged` are sorted name lists,
    `create` is a sorted list of names whose values come from `secrets`. A rotation appears
    in both `remove` and `create`.
    """
    owned = {n for n, labels in stored.items() if labels.get(LABEL_APP) == app}
    unlabelled = {n for n, labels in stored.items() if LABEL_APP not in labels}

    if state == "absent":
        foreign = sorted(n for n in adopt if n in stored and n not in unlabelled and n not in owned)
        if foreign:
            raise PodmanSecretsError(
                "refusing to adopt secrets owned by another app: %s"
                % ", ".join("%s (%s)" % (n, stored[n].get(LABEL_APP)) for n in foreign),
                names=foreign,
            )
        return sorted(owned | (set(adopt) & unlabelled)), [], []

    foreign = sorted(n for n in secrets if n in stored and n not in owned and n not in unlabelled)
    if foreign:
        raise PodmanSecretsError(
            "refusing to take over secrets owned by another app: %s. Rename the secret, or "
            "decommission that app first."
            % ", ".join("%s (%s)" % (n, stored[n].get(LABEL_APP)) for n in foreign),
            names=foreign,
        )

    remove: set[str] = set()
    create: set[str] = set()
    unchanged: set[str] = set()
    for name, value in secrets.items():
        if name not in stored:
            create.add(name)
        elif name in unlabelled or not matches(stored[name].get(LABEL_DIGEST), value):
            # Adopted, or rotated: podman cannot update in place, so both are rm then create.
            remove.add(name)
            create.add(name)
        else:
            unchanged.add(name)
    remove |= owned - set(secrets)
    return sorted(remove), sorted(create), sorted(unchanged)


def reconcile(store: Store, app: str, secrets: Mapping[object, object], adopt: Iterable[str],
              state: str, check_mode: bool, adopt_file: str | None = None) -> Result:
    """Apply the plan to the store and describe what was done, in the module's return shape."""
    values = _normalise(secrets) if state == "present" else {}
    adopt = sorted(set(adopt) | set(recorded_names(adopt_file)))
    stored = store.labels()
    remove, create, unchanged = plan(app, values, adopt, state, stored)

    if not check_mode:
        for name in remove:
            store.remove(name)
        for name in create:
            store.create(name, values[name], app)

    owned_before = sorted(n for n, labels in stored.items() if labels.get(LABEL_APP) == app)
    owned_after = sorted((set(owned_before) - set(remove)) | set(create))
    return {
        "changed": bool(remove or create),
        "stored": create,
        "removed": sorted(set(remove) - set(create)),
        "unchanged": unchanged,
        "diff": {"before": "\n".join(owned_before) + "\n", "after": "\n".join(owned_after) + "\n"},
    }


def _runner(module: AnsibleModule) -> Runner:
    """module.run_command, in the (argv, stdin) shape Store expects.

    binary_data, because run_command appends a newline to text data and a secret value must
    be stored byte for byte.
    """
    def run(argv: list[str], stdin: bytes | None) -> tuple[int, str, str]:
        rc, out, err = module.run_command(argv, data=stdin, binary_data=True)
        return rc, out, err
    return run


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            app=dict(type="str", required=True),
            secrets=dict(type="dict", default={}, no_log=True),
            adopt=dict(type="list", elements="str", default=[]),
            adopt_file=dict(type="path"),
            state=dict(type="str", choices=["present", "absent"], default="present"),
            executable=dict(type="str", default="podman"),
        ),
        supports_check_mode=True,
    )
    params = module.params

    store = Store(_runner(module), params["executable"])
    try:
        result = reconcile(
            store, params["app"], params["secrets"], params["adopt"], params["state"],
            module.check_mode, adopt_file=params["adopt_file"],
        )
    except PodmanSecretsError as exc:
        module.fail_json(msg=exc.msg, **exc.fields)
    module.exit_json(**result)


if __name__ == "__main__":
    main()
