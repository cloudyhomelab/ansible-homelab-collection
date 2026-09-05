# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``install_manifest`` module's reconciliation, driven against a temporary directory.

The module only reads, unlinks and writes small files, so pytest's `tmp_path` is a faithful
host: every case below lays out a record and some files, calls `reconcile` the way `main`
does, and checks what is left on disk and what was reported. The refusal cases are the ones
that matter most - a corrupt or tampered record must remove nothing - so each names its rule.
"""

import json
import os

import pytest

from conftest import COLLECTION, MODULE_DIR, ansible_doc, load_plugin

mod = load_plugin(MODULE_DIR / "install_manifest.py", "systemd_app_module_")


class Host:
    """A tmp_path laid out as a host: the three roots, an app home, and a record inside it."""

    def __init__(self, root):
        self.root = root
        self.system_dir = str(root / "etc/containers/systemd")
        self.unit_dir = str(root / "etc/systemd/system")
        self.home = root / "var/app/myapp"
        self.config_dir = str(self.home / "config")
        self.manifest = str(self.home / ".install-manifest")
        for directory in (self.system_dir, self.unit_dir, self.config_dir):
            os.makedirs(directory)

    def quadlet(self, name):
        return "%s/%s" % (self.system_dir, name)

    def unit(self, name):
        return "%s/%s" % (self.unit_dir, name)

    def config(self, rel):
        return "%s/%s" % (self.config_dir, rel)

    def touch(self, *paths):
        for path in paths:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as handle:
                handle.write("x")
        return paths

    def record(self, *lines, text=None):
        with open(self.manifest, "w") as handle:
            handle.write(text if text is not None else "".join(line + "\n" for line in lines))

    def recorded(self):
        with open(self.manifest) as handle:
            return handle.read().splitlines()

    def reconcile(self, installed=(), state="present", check_mode=False):
        return mod.reconcile(
            mod.Files(), self.manifest, list(installed), self.system_dir, self.unit_dir,
            self.config_dir, state, check_mode,
        )


@pytest.fixture
def host(tmp_path):
    return Host(tmp_path)


# --- a first deploy ---------------------------------------------------------------------

def test_a_first_deploy_records_what_it_installed_and_prunes_nothing(host):
    installed = host.touch(host.quadlet("myapp.container"), host.config("app.conf"))
    result = host.reconcile(installed)

    assert result["changed"] is True
    assert result["pruned"] == [] and result["config_changed"] is False
    assert result["recorded"] == sorted(installed)
    assert host.recorded() == sorted(installed)


def test_the_record_is_sorted_and_deduplicated(host):
    a, b = host.touch(host.unit("z.service"), host.quadlet("a.container"))
    host.reconcile([a, b, a])

    assert host.recorded() == [b, a]


def test_a_new_record_is_created_world_readable(host):
    host.reconcile(host.touch(host.quadlet("myapp.container")))

    assert oct(os.stat(host.manifest).st_mode & 0o777) == "0o644"


def test_a_replaced_record_keeps_its_mode(host):
    host.record(host.quadlet("myapp.container"))
    os.chmod(host.manifest, 0o600)
    host.reconcile(host.touch(host.quadlet("other.container")))

    assert oct(os.stat(host.manifest).st_mode & 0o777) == "0o600"


def test_a_record_that_would_not_change_is_not_rewritten(host):
    installed = host.touch(host.quadlet("myapp.container"), host.config("app.conf"))
    host.record(*sorted(installed))
    before = os.stat(host.manifest)
    result = host.reconcile(installed)

    assert result["changed"] is False
    assert os.stat(host.manifest).st_ino == before.st_ino


def test_installed_paths_that_do_not_exist_yet_are_still_recorded(host):
    # A check-mode deploy has installed nothing; the record must still say what it would hold.
    result = host.reconcile([host.quadlet("myapp.container")])

    assert result["recorded"] == [host.quadlet("myapp.container")]


# --- pruning ----------------------------------------------------------------------------

def test_what_the_last_deploy_recorded_and_this_one_did_not_is_removed(host):
    kept, dropped, renamed = host.touch(
        host.config("app.conf"), host.config("nested/deep.conf"), host.unit("old.service"),
    )
    host.record(kept, dropped, renamed)
    result = host.reconcile([kept])

    assert result["pruned"] == sorted([dropped, renamed])
    assert not os.path.exists(dropped) and not os.path.exists(renamed)
    assert os.path.exists(kept)
    assert host.recorded() == [kept]


def test_a_pruned_config_file_is_a_config_change_and_a_pruned_unit_is_not(host):
    unit, config = host.touch(host.unit("old.service"), host.config("old.conf"))

    host.record(unit)
    assert host.reconcile([])["config_changed"] is False

    host.record(config)
    assert host.reconcile([])["config_changed"] is True


def test_a_recorded_path_already_gone_is_pruned_without_complaint(host):
    host.record(host.quadlet("gone.container"))
    result = host.reconcile([])

    assert result["pruned"] == [host.quadlet("gone.container")]
    assert result["changed"] is True


def test_pruning_unlinks_a_symlink_and_not_what_it_points_at(host):
    target = host.touch(str(host.root / "elsewhere/target.conf"))[0]
    link = host.config("link.conf")
    os.symlink(target, link)
    host.record(link)
    host.reconcile([])

    assert not os.path.lexists(link)
    assert os.path.exists(target)


def test_a_change_of_kind_prunes_what_the_old_kind_installed(host):
    # A 'source' app's record, reconciled by the 'inline' deploy it became.
    source = host.touch(
        host.quadlet("myapp.container"), host.unit("myapp-extra.service"), host.config("app.conf"),
    )
    host.record(*source)
    result = host.reconcile([host.quadlet("myapp.container")])

    assert result["pruned"] == [host.unit("myapp-extra.service"), host.config("app.conf")]
    assert host.recorded() == [host.quadlet("myapp.container")]
    assert os.path.exists(host.quadlet("myapp.container"))


def test_blank_lines_and_surrounding_whitespace_in_the_record_are_ignored(host):
    path = host.touch(host.quadlet("myapp.container"))[0]
    host.record(text="\n  %s  \n\n" % path)
    result = host.reconcile([])

    assert result["pruned"] == [path]


# --- absent -----------------------------------------------------------------------------

def test_absent_removes_everything_recorded_and_the_record_itself(host):
    files = host.touch(
        host.quadlet("myapp.container"), host.unit("myapp-extra.service"),
        host.config("app.conf"), host.config("nested/deep.conf"),
    )
    host.record(*files)
    result = host.reconcile([host.quadlet("myapp.container")], state="absent")

    assert result["changed"] is True
    # 'installed' is ignored on absent: the Quadlet went too.
    assert result["pruned"] == sorted(files)
    assert result["recorded"] == []
    assert not any(os.path.exists(f) for f in files)
    assert not os.path.exists(host.manifest)


def test_absent_leaves_the_directories_and_other_apps_files_alone(host):
    other = host.touch(host.quadlet("other.container"), host.config("nested/other.conf"))
    mine = host.touch(host.config("nested/mine.conf"))
    host.record(*mine)
    host.reconcile(state="absent")

    assert all(os.path.exists(f) for f in other)
    assert os.path.isdir(host.config("nested"))


def test_absent_with_no_record_changes_nothing_and_succeeds(host):
    # A repeat decommission: the first took the record with it.
    result = host.reconcile(state="absent")

    assert result["changed"] is False
    assert result["pruned"] == [] and result["units"] == []


def test_absent_with_an_empty_record_removes_the_record(host):
    host.record()
    result = host.reconcile(state="absent")

    assert result["changed"] is True
    assert not os.path.exists(host.manifest)


# --- refusals ---------------------------------------------------------------------------

def refused(host, *lines, **kwargs):
    host.record(*lines)
    with pytest.raises(mod.InstallManifestError) as exc:
        host.reconcile(**kwargs)
    return exc.value


@pytest.mark.parametrize("state", ["present", "absent"])
def test_a_recorded_path_outside_every_root_refuses_the_whole_record(host, state):
    legal = host.touch(host.quadlet("myapp.container"))[0]
    error = refused(host, legal, "/etc/passwd", state=state)

    assert "/etc/passwd" in error.msg and "outside" in error.msg
    assert error.fields["refused"] == ["/etc/passwd is outside %s, %s and %s" % (
        host.system_dir, host.unit_dir, host.config_dir)]
    # Refused as a whole: the legal line was not acted on either.
    assert os.path.exists(legal)
    assert os.path.exists(host.manifest)


@pytest.mark.parametrize("rel", [".", "..", "a/b", "", "../other.container"])
def test_an_install_dir_path_that_is_not_one_plain_segment_is_refused(host, rel):
    error = refused(host, "%s/%s" % (host.system_dir, rel))

    assert "single path segment" in error.msg


@pytest.mark.parametrize("rel", ["..", "../../other/config/x", "a/../b", "a//b", "a/", "./a", "a/./b"])
def test_a_config_path_with_an_empty_dot_or_dotdot_segment_is_refused(host, rel):
    error = refused(host, "%s/%s" % (host.config_dir, rel))

    assert "segment" in error.msg


def test_the_config_dir_itself_and_the_install_dirs_themselves_are_refused(host):
    for root in (host.system_dir, host.unit_dir, host.config_dir):
        error = refused(host, root)
        assert root in error.msg


def test_a_relative_path_is_refused(host):
    error = refused(host, "etc/containers/systemd/myapp.container")

    assert "not an absolute path" in error.msg


def test_a_recorded_path_that_is_a_directory_is_refused_and_nothing_is_removed(host):
    # The old task chain would have passed this to `file: state=absent`, which removes a
    # directory recursively - the one thing a list an attacker could edit must never do.
    os.makedirs(host.config("nested"))
    victim = host.touch(host.config("nested/keep.conf"), host.quadlet("myapp.container"))
    error = refused(host, host.config("nested"), host.quadlet("myapp.container"), state="absent")

    assert "directory" in error.msg
    assert error.fields["refused"] == [
        "%s names a directory or something else that is not a regular file or symlink" % host.config("nested")
    ]
    assert all(os.path.exists(f) for f in victim)


def test_a_symlink_to_a_directory_is_a_symlink_and_is_unlinked(host):
    os.makedirs(host.config("real"))
    link = host.config("link")
    os.symlink(host.config("real"), link)
    host.record(link)
    host.reconcile([])

    assert not os.path.lexists(link)
    assert os.path.isdir(host.config("real"))


def test_installed_is_held_to_the_same_rules_before_it_is_recorded(host):
    host.record(host.quadlet("myapp.container"))
    with pytest.raises(mod.InstallManifestError) as exc:
        host.reconcile([host.quadlet("myapp.container"), "/etc/shadow"])

    assert "'installed'" in exc.value.msg and "/etc/shadow" in exc.value.msg
    # Nothing recorded, nothing pruned.
    assert host.recorded() == [host.quadlet("myapp.container")]


def test_an_installed_path_that_is_a_directory_is_refused(host):
    os.makedirs(host.config("tree"))
    with pytest.raises(mod.InstallManifestError) as exc:
        host.reconcile([host.config("tree")])

    assert "directory" in exc.value.msg
    assert not os.path.exists(host.manifest)


# --- check mode and diff ----------------------------------------------------------------

def test_check_mode_reports_the_plan_and_touches_nothing(host):
    kept, dropped = host.touch(host.config("app.conf"), host.unit("old.service"))
    new = host.quadlet("myapp.container")
    host.record(kept, dropped)
    result = host.reconcile([kept, new], check_mode=True)

    assert result["changed"] is True
    assert result["pruned"] == [dropped]
    assert result["recorded"] == [new, kept]
    assert result["config_changed"] is False
    assert os.path.exists(dropped)
    assert host.recorded() == [kept, dropped]


def test_check_mode_absent_still_answers_what_is_running(host):
    files = host.touch(host.quadlet("myapp.container"), host.unit("myapp-extra.service"))
    host.record(*files)
    result = host.reconcile(state="absent", check_mode=True)

    assert result["units"] == ["myapp-extra.service", "myapp.service"]
    assert result["pruned"] == sorted(files)
    assert all(os.path.exists(f) for f in files)
    assert os.path.exists(host.manifest)


def test_check_mode_on_a_first_deploy_reports_a_record_it_did_not_write(host):
    result = host.reconcile([host.quadlet("myapp.container")], check_mode=True)

    assert result["changed"] is True
    assert result["diff"] == {"before": "", "after": host.quadlet("myapp.container") + "\n"}
    assert not os.path.exists(host.manifest)


def test_the_diff_is_the_record_before_and_after(host):
    old, new = host.quadlet("old.container"), host.quadlet("new.container")
    host.record(old)
    result = host.reconcile([new])

    assert result["diff"] == {"before": old + "\n", "after": new + "\n"}


# --- units ------------------------------------------------------------------------------
# The rules the deprecated manifest_units filter documents, now answered by the module.

SYSTEM_DIR = "/etc/containers/systemd"
UNIT_DIR = "/etc/systemd/system"


def units(paths):
    return mod.units_of(paths, SYSTEM_DIR, UNIT_DIR)


@pytest.mark.parametrize(
    "name, unit",
    [
        ("app.container", "app.service"),
        ("app.kube", "app.service"),
        ("app.pod", "app-pod.service"),
    ],
)
def test_quadlet_files_map_to_the_unit_its_generator_makes(name, unit):
    assert units([f"{SYSTEM_DIR}/{name}"]) == [unit]


@pytest.mark.parametrize("name", ["app.volume", "app.network", "app.image", "app.build"])
def test_quadlet_kinds_that_run_nothing_are_left_alone(name):
    """Their resources outlive the app by design, so naming their units would mislead."""
    assert units([f"{SYSTEM_DIR}/{name}"]) == []


@pytest.mark.parametrize(
    "name",
    ["app.service", "app.socket", "app.timer", "app.path", "app.mount", "app.automount"],
)
def test_plain_unit_files_are_their_own_unit(name):
    assert units([f"{UNIT_DIR}/{name}"]) == [name]


@pytest.mark.parametrize("name", ["app.target", "app.slice", "app.scope", "app.conf", "app"])
def test_plain_files_that_are_not_a_stoppable_unit_are_ignored(name):
    assert units([f"{UNIT_DIR}/{name}"]) == []


def test_a_config_path_names_no_unit():
    assert units(["/var/app/myapp/config/app.conf", "/var/app/myapp/config/n/deep.conf"]) == []


def test_a_suffix_with_no_name_before_it_is_not_a_unit():
    assert units([f"{SYSTEM_DIR}/.container", f"{UNIT_DIR}/.service"]) == []


def test_the_result_is_sorted_and_deduplicated():
    # A '.container' and a plain '.service' of the same name compose one unit, not two.
    assert units([
        f"{UNIT_DIR}/zz-extra.service",
        f"{SYSTEM_DIR}/app.container",
        f"{UNIT_DIR}/app.service",
        f"{SYSTEM_DIR}/app.container",
    ]) == ["app.service", "zz-extra.service"]


def test_units_are_those_of_the_record_as_read_so_absent_can_stop_them(host):
    files = host.touch(
        host.quadlet("molsource.container"), host.unit("molsource-extra.service"),
        host.config("app.conf"),
    )
    host.record(*files)
    result = host.reconcile(state="absent")

    assert result["units"] == ["molsource-extra.service", "molsource.service"]


def test_the_install_dirs_are_taken_from_the_caller():
    """Both are role variables, so a fleet that moved them must still tear down."""
    assert mod.units_of(
        ["/srv/quadlet/app.container", "/srv/units/app-extra.service"], "/srv/quadlet/", "/srv/units",
    ) == ["app-extra.service", "app.service"]


# --- docs -------------------------------------------------------------------------------

def test_ansible_doc_renders_the_module(collection_path):
    result = ansible_doc(collection_path, "module", "--json", f"{COLLECTION}.install_manifest")
    assert result.returncode == 0, result.stderr
    doc = json.loads(result.stdout)[f"{COLLECTION}.install_manifest"]["doc"]
    assert doc["short_description"]
    # The file-attribute options come from the `files` fragment, alongside the module's own.
    assert {"path", "installed", "system_dir", "unit_dir", "config_dir", "state", "owner", "mode"} <= set(doc["options"])
