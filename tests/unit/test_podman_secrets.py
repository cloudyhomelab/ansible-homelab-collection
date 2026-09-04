# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``podman_secrets`` module's reconciliation, driven against a fake store.

The module keeps every podman call behind one runner callable, so the whole of its logic --
the plan, the labels it writes, the order of removes and creates, what it refuses and how it
reports a failure -- can be exercised here without podman and in milliseconds. What the fake
cannot show, that a real podman accepts these arguments, the molecule scenario covers.
"""

import hashlib
import json

import pytest

from conftest import COLLECTION, MODULE_DIR, ansible_doc, load_plugin

mod = load_plugin(MODULE_DIR / "podman_secrets.py", "systemd_app_module_")

APP = mod.LABEL_APP
DIGEST = mod.LABEL_DIGEST


def sha(value, algorithm="sha256"):
    return "%s:%s" % (algorithm, hashlib.new(algorithm, value.encode()).hexdigest())


class FakePodman:
    """Just enough of `podman secret` to drive the module: ls, inspect, rm, create.

    `secrets` maps name -> (value, labels). Every call is recorded in `calls` as
    (argv, stdin), so a test can assert what reached podman and what did not.
    """

    def __init__(self, secrets=None, fail=None):
        self.secrets = dict(secrets or {})
        self.calls = []
        # (subcommand, name) -> (rc, stderr): a call to make fail.
        self.fail = fail or {}

    def __call__(self, argv, stdin):
        self.calls.append((list(argv), stdin))
        assert argv[0] == "podman" and argv[1] == "secret"
        sub, args = argv[2], argv[3:]
        name = args[-1] if sub != "create" else args[-2]
        if (sub, name) in self.fail or (sub, None) in self.fail:
            rc, err = self.fail.get((sub, name)) or self.fail[(sub, None)]
            return rc, "", err
        if sub == "ls":
            return 0, "".join(n + "\n" for n in sorted(self.secrets)), ""
        if sub == "inspect":
            return 0, json.dumps([
                {"ID": "x", "Spec": {"Name": n, "Driver": {"Name": "file"}, "Labels": self.secrets[n][1]}}
                for n in args
            ]), ""
        if sub == "rm":
            if name not in self.secrets:
                return 125, "", "Error: no secret with name or id %r" % name
            del self.secrets[name]
            return 0, name + "\n", ""
        if sub == "create":
            assert args[-1] == "-", "the value must come on stdin"
            if name in self.secrets:
                return 125, "", "Error: %s: secret name in use" % name
            labels = dict(a.split("=", 1) for a in args[1:-2:2])
            assert args[0:-2:2] == ["--label"] * len(labels)
            self.secrets[name] = (stdin.decode(), labels)
            return 0, "id\n", ""
        raise AssertionError("unexpected podman call: %r" % argv)

    def writes(self):
        """The rm and create calls, in order, as ('rm'|'create', name)."""
        return [(a[2], a[-1] if a[2] == "rm" else a[-2]) for a, _ in self.calls if a[2] in ("rm", "create")]


def owned(name, value, app="myapp"):
    return (value, {APP: app, DIGEST: sha(value)})


def run(podman, secrets=None, state="present", adopt=(), check_mode=False, app="myapp"):
    store = mod.Store(podman)
    return mod.reconcile(store, app, dict(secrets or {}), list(adopt), state, check_mode)


def test_an_empty_store_gets_every_declared_secret_with_both_labels():
    podman = FakePodman()
    result = run(podman, {"myapp-token": "t0k", "myapp-key": "k3y"})

    assert result["changed"] is True
    assert result["stored"] == ["myapp-key", "myapp-token"]
    assert result["removed"] == [] and result["unchanged"] == []
    assert podman.secrets["myapp-token"] == ("t0k", {APP: "myapp", DIGEST: sha("t0k")})
    assert podman.secrets["myapp-key"][1][APP] == "myapp"


def test_the_value_travels_on_stdin_and_never_in_argv():
    podman = FakePodman()
    run(podman, {"myapp-token": "s3cret-value"})

    creates = [(argv, stdin) for argv, stdin in podman.calls if argv[2] == "create"]
    assert len(creates) == 1
    argv, stdin = creates[0]
    assert stdin == b"s3cret-value"
    assert "s3cret-value" not in " ".join(argv)


def test_a_matching_secret_is_left_alone():
    podman = FakePodman({"myapp-token": owned("myapp-token", "t0k")})
    result = run(podman, {"myapp-token": "t0k"})

    assert result["changed"] is False
    assert result["unchanged"] == ["myapp-token"]
    assert podman.writes() == []


def test_a_changed_value_is_removed_then_created():
    podman = FakePodman({"myapp-token": owned("myapp-token", "old")})
    result = run(podman, {"myapp-token": "new"})

    assert result["changed"] is True
    assert result["stored"] == ["myapp-token"]
    # A rotation is not a drop: the name is still the app's afterwards.
    assert result["removed"] == []
    assert podman.writes() == [("rm", "myapp-token"), ("create", "myapp-token")]
    assert podman.secrets["myapp-token"][0] == "new"


def test_a_secret_removed_behind_the_role_s_back_comes_back():
    podman = FakePodman({"myapp-key": owned("myapp-key", "k")})
    result = run(podman, {"myapp-key": "k", "myapp-token": "t"})

    assert result["stored"] == ["myapp-token"]
    assert result["unchanged"] == ["myapp-key"]
    assert podman.writes() == [("create", "myapp-token")]


def test_a_name_the_app_stopped_declaring_is_removed():
    podman = FakePodman({
        "myapp-old": owned("myapp-old", "o"),
        "myapp-token": owned("myapp-token", "t"),
    })
    result = run(podman, {"myapp-token": "t"})

    assert result["changed"] is True
    assert result["removed"] == ["myapp-old"]
    assert podman.writes() == [("rm", "myapp-old")]
    assert "myapp-old" not in podman.secrets


def test_another_app_s_secrets_are_never_touched():
    podman = FakePodman({"other-token": owned("other-token", "x", app="other")})
    result = run(podman, {"myapp-token": "t"})

    assert result["removed"] == []
    assert "other-token" in podman.secrets


def test_a_declared_name_owned_by_another_app_is_refused_before_anything_changes():
    podman = FakePodman({"shared-token": owned("shared-token", "x", app="other")})
    with pytest.raises(mod.PodmanSecretsError) as exc:
        run(podman, {"shared-token": "mine", "myapp-key": "k"})

    assert "shared-token" in exc.value.msg and "other" in exc.value.msg
    assert exc.value.fields["names"] == ["shared-token"]
    # Refused as a whole: the key that could have been stored was not.
    assert podman.writes() == []


def test_an_unlabelled_secret_with_a_declared_name_is_adopted_by_recreating_it():
    podman = FakePodman({"myapp-token": ("unknown", {})})
    result = run(podman, {"myapp-token": "t"})

    assert result["stored"] == ["myapp-token"]
    assert podman.writes() == [("rm", "myapp-token"), ("create", "myapp-token")]
    assert podman.secrets["myapp-token"][1] == {APP: "myapp", DIGEST: sha("t")}


def test_absent_removes_what_the_app_owns_and_nothing_else():
    podman = FakePodman({
        "myapp-token": owned("myapp-token", "t"),
        "myapp-key": owned("myapp-key", "k"),
        "other-token": owned("other-token", "x", app="other"),
        "loose": ("l", {}),
    })
    result = run(podman, state="absent", secrets={"ignored": "on absent"})

    assert result["changed"] is True
    assert result["removed"] == ["myapp-key", "myapp-token"]
    assert result["stored"] == []
    assert set(podman.secrets) == {"other-token", "loose"}


def test_absent_also_removes_adopted_unlabelled_names():
    podman = FakePodman({"legacy-token": ("l", {}), "loose": ("x", {})})
    result = run(podman, state="absent", adopt=["legacy-token", "never-stored"])

    assert result["removed"] == ["legacy-token"]
    assert set(podman.secrets) == {"loose"}


def test_absent_refuses_to_adopt_another_app_s_secret():
    podman = FakePodman({"other-token": owned("other-token", "x", app="other")})
    with pytest.raises(mod.PodmanSecretsError) as exc:
        run(podman, state="absent", adopt=["other-token"])

    assert "other-token" in exc.value.msg and "other" in exc.value.msg
    assert podman.writes() == []


def test_absent_on_an_app_with_nothing_stored_changes_nothing():
    podman = FakePodman({"other-token": owned("other-token", "x", app="other")})
    result = run(podman, state="absent")

    assert result["changed"] is False
    assert result["removed"] == []


def test_check_mode_reports_the_plan_and_writes_nothing():
    podman = FakePodman({
        "myapp-old": owned("myapp-old", "o"),
        "myapp-token": owned("myapp-token", "old"),
    })
    result = run(podman, {"myapp-token": "new", "myapp-key": "k"}, check_mode=True)

    assert result["changed"] is True
    assert result["stored"] == ["myapp-key", "myapp-token"]
    assert result["removed"] == ["myapp-old"]
    assert podman.writes() == []
    # Reads still happen in check mode, or the plan could not be computed.
    assert [a[2] for a, _ in podman.calls] == ["ls", "inspect"]


def test_the_diff_names_what_the_app_owns_before_and_after_and_no_value():
    podman = FakePodman({"myapp-old": owned("myapp-old", "o")})
    result = run(podman, {"myapp-token": "s3cret"})

    assert result["diff"] == {"before": "myapp-old\n", "after": "myapp-token\n"}
    assert "s3cret" not in json.dumps(result)


@pytest.mark.parametrize("name", [".hidden", "has space", "bad/slash", ""])
def test_a_name_podman_would_refuse_is_refused_first(name):
    podman = FakePodman()
    with pytest.raises(mod.PodmanSecretsError) as exc:
        run(podman, {name: "v", "myapp-ok": "v"})

    assert exc.value.fields["names"] == [name]
    assert podman.calls == []


@pytest.mark.parametrize("value", [None, ""])
def test_an_empty_value_is_refused_by_name_only(value):
    podman = FakePodman()
    with pytest.raises(mod.PodmanSecretsError) as exc:
        run(podman, {"myapp-empty": value, "myapp-ok": "v"})

    assert exc.value.fields["names"] == ["myapp-empty"]
    assert podman.calls == []


def test_non_string_values_are_stored_stringified():
    podman = FakePodman()
    run(podman, {"myapp-port": 5432, "myapp-flag": True})

    assert podman.secrets["myapp-port"][0] == "5432"
    assert podman.secrets["myapp-flag"][0] == "True"


def test_a_failing_create_is_reported_with_name_rc_and_stderr_but_no_value():
    podman = FakePodman(fail={("create", "myapp-token"): (125, "Error: store is read-only")})
    with pytest.raises(mod.PodmanSecretsError) as exc:
        run(podman, {"myapp-token": "s3cret"})

    assert "myapp-token" in exc.value.msg
    assert exc.value.fields["rc"] == 125
    assert "store is read-only" in exc.value.fields["stderr"]
    assert "s3cret" not in exc.value.msg and "s3cret" not in json.dumps(exc.value.fields)


def test_a_failing_listing_is_reported_and_nothing_is_written():
    podman = FakePodman(fail={("ls", None): (125, "Error: cannot connect")})
    with pytest.raises(mod.PodmanSecretsError) as exc:
        run(podman, {"myapp-token": "t"})

    assert "podman secret ls" in exc.value.msg and "cannot connect" in exc.value.msg
    assert podman.writes() == []


def test_a_secret_inspected_without_labels_is_treated_as_unlabelled():
    # Older podman leaves Labels null rather than {}; either reads as "no owner".
    class NullLabels(FakePodman):
        def __call__(self, argv, stdin):
            rc, out, err = super().__call__(argv, stdin)
            if argv[2] == "inspect":
                out = out.replace('"Labels": {}', '"Labels": null')
            return rc, out, err

    podman = NullLabels({"myapp-token": ("v", {})})
    result = run(podman, {"myapp-token": "v"})

    assert result["stored"] == ["myapp-token"]


def test_the_digest_names_its_algorithm_in_oci_form():
    # `sha256:<hex>`, so an operator can check it with sha256sum and a later algorithm can
    # be told apart from this one.
    assert mod.digest("t0k") == "sha256:" + hashlib.sha256(b"t0k").hexdigest()


def test_a_secret_recorded_under_an_older_algorithm_is_verified_with_it_and_left_alone():
    # Changing the default algorithm must not rotate every secret: the label says which
    # algorithm it was written with, and the value is checked under that one.
    podman = FakePodman({"myapp-token": ("t0k", {APP: "myapp", DIGEST: sha("t0k", "sha512")})})
    result = run(podman, {"myapp-token": "t0k"})

    assert result["changed"] is False
    assert podman.writes() == []


def test_a_changed_value_under_an_older_algorithm_moves_to_the_current_one():
    podman = FakePodman({"myapp-token": ("old", {APP: "myapp", DIGEST: sha("old", "sha512")})})
    run(podman, {"myapp-token": "new"})

    assert podman.secrets["myapp-token"][1][DIGEST] == sha("new")


@pytest.mark.parametrize("recorded", ["", "deadbeef", "nosuchalgo:deadbeef", "sha256:wrong"])
def test_a_digest_that_cannot_be_verified_reads_as_changed(recorded):
    podman = FakePodman({"myapp-token": ("t0k", {APP: "myapp", DIGEST: recorded})})
    result = run(podman, {"myapp-token": "t0k"})

    assert result["stored"] == ["myapp-token"]
    assert podman.writes() == [("rm", "myapp-token"), ("create", "myapp-token")]


def test_ansible_doc_renders_the_module(collection_path):
    result = ansible_doc(collection_path, "module", "--json", f"{COLLECTION}.podman_secrets")
    assert result.returncode == 0, result.stderr
    doc = json.loads(result.stdout)[f"{COLLECTION}.podman_secrets"]["doc"]
    assert doc["short_description"]
    assert set(doc["options"]) == {"app", "secrets", "adopt", "state", "executable"}
