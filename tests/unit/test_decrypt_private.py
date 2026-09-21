# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The decrypt helper, driven against a temporary directory with a fake runner.

No test has the age identity, so what is exercised is everything around the two binaries:
which tool each file goes to, what the result is called, and what is left behind on failure.
"""

import os
import stat

import pytest

from conftest import ACTION_CASES, import_helper

mod = import_helper()


class FakeRunner:
    """Stands in for age and sops; fails the one it is told to."""

    def __init__(self, fails=None):
        self.calls = []
        self.fails = fails

    def run(self, argv, env):
        self.calls.append((list(argv), dict(env)))
        if self.fails == argv[0]:
            raise mod.DecryptError("%s exited 1: no identity matched" % argv[0])
        return b"plain(%s)" % argv[-1].encode("utf-8")

    def tools(self):
        return [call[0][0] for call in self.calls]


@pytest.fixture
def tree(tmp_path):
    """src/ to decrypt from, dst/ standing in for the unit's RuntimeDirectory."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    return src, dst


def put(src, relpath, content="cipher"):
    path = src / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def mode_of(path):
    return stat.S_IMODE(os.stat(str(path)).st_mode)


# --- the rules, shared with plugins/filter/private_tree.py -------------------------------

@pytest.mark.parametrize("name, tool, out", ACTION_CASES)
def test_action_for_matches_the_shared_table(name, tool, out):
    assert mod.action_for(name) == (tool, out)


@pytest.mark.parametrize(
    "relpath, tool, out",
    [
        ("db.env.age", "age", "db.env"),
        ("tls/server.key.age", "age", "tls/server.key"),
        ("a/b/c.sops.yaml", "sops", "a/b/c.yaml"),
        ("a/b/ca.crt", "copy", "a/b/ca.crt"),
        # Only the base name decides.
        (".age/x.sops.json", "sops", ".age/x.json"),
    ],
)
def test_only_the_base_name_decides_and_directories_are_mirrored(relpath, tool, out):
    assert mod.decrypted_path(relpath) == (tool, out)


def test_two_files_decrypting_to_one_name_are_reported():
    assert mod.collisions(["db.env", "db.env.age"]) == [
        "private/db.env and private/db.env.age both decrypt to private/db.env"
    ]


def test_the_same_name_in_two_directories_is_not_a_collision():
    assert mod.collisions(["a/x.age", "b/x.age"]) == []


def test_every_collision_is_reported_not_just_the_first():
    assert len(mod.collisions(["a", "a.age", "b", "b.age"])) == 2


def test_nothing_collides_with_nothing():
    assert mod.collisions([]) == []


# --- what it does to the runtime directory -----------------------------------------------

def test_each_file_goes_to_the_tool_its_name_names(tree):
    src, dst = tree
    put(src, "db.env.age")
    put(src, "config.sops.yaml")
    put(src, "ca.crt", "public")
    runner = FakeRunner()

    assert mod.decrypt(str(src), str(dst), "/key", runner) == [
        "ca.crt", "config.sops.yaml", "db.env.age",
    ]
    assert runner.tools() == ["sops", "age"]
    assert (dst / "db.env").read_bytes().startswith(b"plain(")
    assert (dst / "config.yaml").read_bytes().startswith(b"plain(")
    # Neither suffix: copied byte for byte, not run through anything.
    assert (dst / "ca.crt").read_text() == "public"


def test_the_identity_reaches_sops_through_the_environment_and_age_on_its_command_line(tree):
    src, dst = tree
    put(src, "a.age")
    put(src, "b.sops.yaml")
    runner = FakeRunner()
    mod.decrypt(str(src), str(dst), "/creds/age-key", runner)

    by_tool = {argv[0]: (argv, env) for argv, env in runner.calls}
    assert "-i" in by_tool["age"][0] and "/creds/age-key" in by_tool["age"][0]
    assert by_tool["sops"][1]["SOPS_AGE_KEY_FILE"] == "/creds/age-key"
    # Nothing else is handed down.
    assert set(by_tool["age"][1]) == {"PATH", "SOPS_AGE_KEY_FILE"}


def test_a_nested_tree_is_mirrored(tree):
    src, dst = tree
    put(src, "tls/server.key.age")
    put(src, "tls/chain/ca.crt", "public")
    mod.decrypt(str(src), str(dst), "/key", FakeRunner())

    assert (dst / "tls" / "server.key").exists()
    assert (dst / "tls" / "chain" / "ca.crt").read_text() == "public"


def test_what_it_writes_is_readable_only_by_its_owner(tree):
    src, dst = tree
    put(src, "tls/server.key.age")
    mod.decrypt(str(src), str(dst), "/key", FakeRunner())

    assert mode_of(dst / "tls" / "server.key") == 0o600
    assert mode_of(dst / "tls") == 0o700


def test_hidden_files_are_decrypted_too(tree):
    src, dst = tree
    put(src, ".env.age")
    mod.decrypt(str(src), str(dst), "/key", FakeRunner())
    assert (dst / ".env").exists()


def test_an_empty_private_directory_decrypts_to_nothing(tree):
    src, dst = tree
    assert mod.decrypt(str(src), str(dst), "/key", FakeRunner()) == []
    assert os.listdir(str(dst)) == []


# --- failures ------------------------------------------------------------------------------

def test_a_collision_is_refused_before_anything_is_written(tree):
    src, dst = tree
    put(src, "db.env")
    put(src, "db.env.age")
    runner = FakeRunner()

    with pytest.raises(mod.DecryptError) as excinfo:
        mod.decrypt(str(src), str(dst), "/key", runner)

    assert "both decrypt to private/db.env" in str(excinfo.value)
    assert runner.calls == [] and os.listdir(str(dst)) == []


def test_a_failing_tool_leaves_no_half_filled_runtime_directory(tree):
    src, dst = tree
    put(src, "a.age")
    put(src, "b.sops.yaml")
    put(src, "c.crt")

    with pytest.raises(mod.DecryptError):
        mod.decrypt(str(src), str(dst), "/key", FakeRunner(fails="sops"))

    # Including the ones that had already succeeded: all of them or none.
    assert os.listdir(str(dst)) == []


def test_a_missing_binary_is_named_rather_than_traced():
    """A host without age or sops must say which, not raise at systemd."""
    with pytest.raises(mod.DecryptError) as excinfo:
        mod.Runner().run(["age-that-is-not-installed"], {"PATH": os.environ["PATH"]})

    assert "age-that-is-not-installed" in str(excinfo.value)
    assert "Install it" in str(excinfo.value)


def test_a_tool_that_exits_non_zero_reports_its_stderr():
    runner = mod.Runner()
    with pytest.raises(mod.DecryptError) as excinfo:
        runner.run(["sh", "-c", "echo no identity matched >&2; exit 3"], {"PATH": os.environ["PATH"]})
    assert "exited 3" in str(excinfo.value) and "no identity matched" in str(excinfo.value)


# --- the command line ------------------------------------------------------------------------

def test_it_takes_exactly_one_app_name(capsys):
    assert mod.main([]) == 2
    assert mod.main(["a", "b"]) == 2
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "env, expected",
    [
        ({}, "RUNTIME_DIRECTORY is unset"),
        ({"RUNTIME_DIRECTORY": "/run/app/x/private"}, "CREDENTIALS_DIRECTORY is unset"),
    ],
)
def test_run_outside_its_unit_it_says_so(monkeypatch, capsys, env, expected):
    for name in ("RUNTIME_DIRECTORY", "CREDENTIALS_DIRECTORY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    assert mod.main(["myapp"]) == 1
    assert expected in capsys.readouterr().err


def test_a_credentials_directory_without_the_key_is_named(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RUNTIME_DIRECTORY", str(tmp_path / "run"))
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))

    assert mod.main(["myapp"]) == 1
    assert "no age identity at" in capsys.readouterr().err
