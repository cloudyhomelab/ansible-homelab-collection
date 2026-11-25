# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``external_package`` action plugin: its input rules, and its install driven against a
fake host that records every module call."""

import json

import pytest
import yaml

from conftest import COLLECTION, ansible_doc
from plugins.action.external_package import StepFailed, install
from plugins.module_utils.external_package import ARGUMENT_SPEC, QUERIES, validation_errors
from plugins.modules import external_package as module_doc

SHA256 = "sha256:" + "a" * 64


def package(**fields):
    return {
        "name": "sops",
        "version": "3.13.3",
        "url": "https://example.com/sops_3.13.3_amd64.deb",
        "checksum": SHA256,
        **fields,
    }


def problems_for(package_format="deb", **fields):
    return validation_errors(package(**fields), package_format)


# --- validation_errors ------------------------------------------------------------------

def test_a_full_deb_is_accepted():
    assert problems_for() == []


def test_a_full_rpm_is_accepted():
    assert problems_for("rpm", version="3.13.3-1") == []


@pytest.mark.parametrize("package_format", [None, "apk", "DEB"])
def test_an_unknown_format_is_rejected(package_format):
    assert validation_errors(package(), package_format) == [
        f"package format {package_format!r} is not one of ['deb', 'rpm']"
    ]


@pytest.mark.parametrize("name", ["sops", "g++", "libc6", "python3.13", "a-b"])
def test_good_deb_names_are_accepted(name):
    assert problems_for(name=name) == []


@pytest.mark.parametrize(
    "name, why",
    [
        ("Sops", "upper case"),
        ("s", "one character"),
        ("-sops", "an option to dpkg-query"),
        ("a/b", "slash, in what becomes a file name"),
        ("a b", "space"),
        ("sops\n", "trailing newline, which a '$' anchor would have allowed"),
        ("", "empty"),
        (None, "missing"),
        (5, "not a string"),
    ],
)
def test_bad_deb_names_are_rejected(name, why):
    assert any(p.startswith("name ") for p in problems_for(name=name)), why


@pytest.mark.parametrize("name", ["sops", "NetworkManager", "perl-Foo_Bar", "_x"])
def test_good_rpm_names_are_accepted(name):
    assert problems_for("rpm", name=name, version="1-1") == []


@pytest.mark.parametrize("name", ["-sops", "a/b", "a b", ""])
def test_bad_rpm_names_are_rejected(name):
    assert any(p.startswith("name ") for p in problems_for("rpm", name=name, version="1-1"))


@pytest.mark.parametrize("version", ["3.13.3", "1:3.13.3", "3.13.3-1", "2.0~rc1+dfsg-3"])
def test_good_deb_versions_are_accepted(version):
    assert problems_for(version=version) == []


@pytest.mark.parametrize("version", ["v3.13.3", "", "3.13 3", "3.13.3\n", 3.13, None])
def test_bad_deb_versions_are_rejected(version):
    assert any(p.startswith("version ") for p in problems_for(version=version))


@pytest.mark.parametrize("version", ["3.13.3-1", "1:3.13.3-1.fc43", "2.0~rc1-0.1"])
def test_good_rpm_versions_are_accepted(version):
    assert problems_for("rpm", version=version) == []


@pytest.mark.parametrize(
    "version, why",
    [
        ("3.13.3", "no release, which rpm reports and the plugin compares"),
        ("3.13-3-1", "two dashes"),
        ("3.13.3-", "empty release"),
        ("x:3-1", "epoch not a number"),
    ],
)
def test_bad_rpm_versions_are_rejected(version, why):
    assert any(p.startswith("version ") for p in problems_for("rpm", version=version)), why


@pytest.mark.parametrize(
    "url", ["http://h/p.deb", "https://h/p.deb", "ftp://h/p.deb", "file:///srv/p.deb"]
)
def test_good_urls_are_accepted(url):
    assert problems_for(url=url) == []


@pytest.mark.parametrize("url", ["h/p.deb", "/srv/p.deb", "https://h/a b.deb", "", None])
def test_bad_urls_are_rejected(url):
    assert any(p.startswith("url ") for p in problems_for(url=url))


@pytest.mark.parametrize(
    "checksum", [SHA256, "sha512:" + "0" * 128, "md5:" + "F" * 32, "sha1:" + "9" * 40]
)
def test_good_checksums_are_accepted(checksum):
    assert problems_for(checksum=checksum) == []


@pytest.mark.parametrize(
    "checksum, why",
    [
        ("a" * 64, "no algorithm"),
        ("sha256:" + "a" * 63, "truncated"),
        ("sha256:" + "g" * 64, "not hex"),
        ("blake2:" + "a" * 64, "not an algorithm whose length is known"),
        ("SHA256:" + "a" * 64, "upper-case algorithm, which get_url does not accept"),
        ("sha256:https://h/sums.txt", "a checksum file URL"),
        ("", "empty"),
        (None, "missing"),
    ],
)
def test_bad_checksums_are_rejected(checksum, why):
    assert any(p.startswith("checksum ") for p in problems_for(checksum=checksum)), why


def test_every_problem_is_reported_in_one_pass():
    problems = validation_errors({"name": "-x", "version": "v1", "url": "x", "checksum": "y"}, "deb")
    assert len(problems) == 4


# --- install ----------------------------------------------------------------------------

class FakeHost:
    """A host whose package database holds `installed`, and whose package installs record
    `lands` as the package's version. Every call is recorded as (module name, args)."""

    def __init__(self, package_format="deb", installed=(), lands=None, fail=None):
        self.package_format = package_format
        self.installed = list(installed)
        self.lands = lands
        self.fail = fail
        self.calls = []

    def command(self, argv):
        self.calls.append(("command", argv))
        assert argv[:-1] == QUERIES[self.package_format]
        if self.fail == "command":
            return 2, "", "database locked\n"
        if not self.installed:
            return 1, "", "no packages found matching sops\n"
        return 0, "".join(f"installed {v}\n" for v in self.installed), ""

    def module(self, name, args):
        self.calls.append((name, args))
        if self.fail == name:
            return {"failed": True, "msg": "boom"}
        if name == "ansible.builtin.tempfile":
            return {"path": "/tmp/ansible.x.external_package"}
        if name in ("ansible.builtin.apt", "ansible.builtin.dnf", "ansible.builtin.dnf5"):
            self.installed = [self.lands]
        return {"changed": True}

    def modules(self):
        return [name for name, _args in self.calls if name != "command"]


def test_a_package_at_its_version_is_left_alone():
    host = FakeHost(installed=["3.13.3"])
    result = install(host, package(), "apt", check_mode=False)
    assert result == {"name": "sops", "version": "3.13.3", "previous_versions": ["3.13.3"],
                      "changed": False}
    assert host.modules() == []


def test_a_missing_deb_is_downloaded_installed_and_its_directory_removed():
    host = FakeHost(lands="3.13.3")
    result = install(host, package(), "apt", check_mode=False)
    assert result["changed"] is True and result["previous_versions"] == []
    assert host.modules() == ["ansible.builtin.tempfile", "ansible.builtin.get_url",
                              "ansible.builtin.apt", "ansible.builtin.file"]
    calls = dict(host.calls[1:])
    package_file = "/tmp/ansible.x.external_package/sops-3.13.3.deb"
    assert calls["ansible.builtin.get_url"] == {
        "url": package()["url"], "checksum": SHA256, "dest": package_file, "mode": "0644"}
    assert calls["ansible.builtin.apt"] == {"deb": package_file, "update_cache": True}
    assert calls["ansible.builtin.file"] == {"path": "/tmp/ansible.x.external_package", "state": "absent"}


@pytest.mark.parametrize("pkg_mgr", ["dnf", "dnf5"])
def test_an_rpm_is_installed_by_the_hosts_dnf_without_a_signature_check(pkg_mgr):
    host = FakeHost("rpm", lands="3.13.3-1")
    install(host, package(version="3.13.3-1"), pkg_mgr, check_mode=False)
    installer = f"ansible.builtin.{pkg_mgr}"
    assert dict(host.calls[1:])[installer] == {
        "name": "/tmp/ansible.x.external_package/sops-3.13.3-1.rpm",
        "state": "present", "disable_gpg_check": True}


def test_an_older_version_is_upgraded_and_reported():
    host = FakeHost(installed=["3.12.0"], lands="3.13.3")
    result = install(host, package(), "apt", check_mode=False)
    assert result["changed"] is True and result["previous_versions"] == ["3.12.0"]


def test_one_of_several_installed_instances_at_the_version_is_enough():
    host = FakeHost("rpm", installed=["3.12.0-1", "3.13.3-1"])
    assert install(host, package(version="3.13.3-1"), "dnf5", check_mode=False)["changed"] is False


def test_check_mode_reports_the_install_and_touches_nothing():
    host = FakeHost(installed=["3.12.0"])
    result = install(host, package(), "apt", check_mode=True)
    assert result["changed"] is True and "would be installed" in result["msg"]
    assert host.modules() == []


def test_a_version_the_package_does_not_report_fails_after_cleaning_up():
    host = FakeHost(lands="1:3.13.3")
    with pytest.raises(StepFailed, match=r"reports \['1:3.13.3'\] after installing, not 3.13.3"):
        install(host, package(), "apt", check_mode=False)
    assert host.modules()[-1] == "ansible.builtin.file"


@pytest.mark.parametrize("step", ["ansible.builtin.get_url", "ansible.builtin.apt"])
def test_a_failed_step_still_removes_the_download_directory(step):
    host = FakeHost(lands="3.13.3", fail=step)
    with pytest.raises(StepFailed, match="failed: boom"):
        install(host, package(), "apt", check_mode=False)
    assert host.modules()[-1] == "ansible.builtin.file"


def test_a_failed_query_fails_before_anything_is_downloaded():
    host = FakeHost(fail="command")
    with pytest.raises(StepFailed, match=r"\(rc 2\): database locked"):
        install(host, package(), "apt", check_mode=False)
    assert host.modules() == []


def test_a_failed_cleanup_fails_the_task():
    host = FakeHost(lands="3.13.3", fail="ansible.builtin.file")
    with pytest.raises(StepFailed, match="Removing the download directory"):
        install(host, package(), "apt", check_mode=False)


# --- docs -------------------------------------------------------------------------------

def test_the_documented_options_are_the_argument_spec():
    documented = yaml.safe_load(module_doc.DOCUMENTATION)["options"]
    assert {k: (v["type"], v["required"]) for k, v in documented.items()} == {
        k: (v["type"], v["required"]) for k, v in ARGUMENT_SPEC.items()}


def test_ansible_doc_renders_the_module(collection_path):
    result = ansible_doc(collection_path, "module", "--json", f"{COLLECTION}.external_package")
    assert result.returncode == 0, result.stderr
    doc = json.loads(result.stdout)[f"{COLLECTION}.external_package"]["doc"]
    assert set(doc["options"]) == {"name", "version", "url", "checksum"}
