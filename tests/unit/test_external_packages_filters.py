# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the 'external_packages' role's filters."""

import pytest

from plugins.filter.package_validation_errors import package_validation_errors

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
    return package_validation_errors([package(**fields)], package_format)


# --- package_validation_errors ----------------------------------------------------------

@pytest.mark.parametrize("package_format", ["deb", "rpm"])
def test_no_packages_is_valid(package_format):
    assert package_validation_errors([], package_format) == []


def test_a_full_deb_is_accepted():
    assert problems_for() == []


def test_a_full_rpm_is_accepted():
    assert problems_for("rpm", version="3.13.3-1") == []


@pytest.mark.parametrize("package_format", [None, "apk", "DEB"])
def test_an_unknown_format_is_rejected(package_format):
    assert package_validation_errors([], package_format) == [
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
    assert any(".name" in p for p in problems_for(name=name)), why


@pytest.mark.parametrize("name", ["sops", "NetworkManager", "perl-Foo_Bar", "_x"])
def test_good_rpm_names_are_accepted(name):
    assert problems_for("rpm", name=name, version="1-1") == []


@pytest.mark.parametrize("name", ["-sops", "a/b", "a b", ""])
def test_bad_rpm_names_are_rejected(name):
    assert any(".name" in p for p in problems_for("rpm", name=name, version="1-1"))


@pytest.mark.parametrize("version", ["3.13.3", "1:3.13.3", "3.13.3-1", "2.0~rc1+dfsg-3"])
def test_good_deb_versions_are_accepted(version):
    assert problems_for(version=version) == []


@pytest.mark.parametrize("version", ["v3.13.3", "", "3.13 3", "3.13.3\n", 3.13, None])
def test_bad_deb_versions_are_rejected(version):
    assert any(".version" in p for p in problems_for(version=version))


@pytest.mark.parametrize("version", ["3.13.3-1", "1:3.13.3-1.fc43", "2.0~rc1-0.1"])
def test_good_rpm_versions_are_accepted(version):
    assert problems_for("rpm", version=version) == []


@pytest.mark.parametrize(
    "version, why",
    [
        ("3.13.3", "no release, which rpm reports and the role compares"),
        ("3.13-3-1", "two dashes"),
        ("3.13.3-", "empty release"),
        ("x:3-1", "epoch not a number"),
    ],
)
def test_bad_rpm_versions_are_rejected(version, why):
    assert any(".version" in p for p in problems_for("rpm", version=version)), why


@pytest.mark.parametrize(
    "url", ["http://h/p.deb", "https://h/p.deb", "ftp://h/p.deb", "file:///srv/p.deb"]
)
def test_good_urls_are_accepted(url):
    assert problems_for(url=url) == []


@pytest.mark.parametrize("url", ["h/p.deb", "/srv/p.deb", "https://h/a b.deb", "", None])
def test_bad_urls_are_rejected(url):
    assert any(".url" in p for p in problems_for(url=url))


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
        ("blake2:" + "a" * 64, "not an algorithm the role knows the length of"),
        ("SHA256:" + "a" * 64, "upper-case algorithm, which get_url does not accept"),
        ("sha256:https://h/sums.txt", "a checksum file URL"),
        ("", "empty"),
        (None, "missing"),
    ],
)
def test_bad_checksums_are_rejected(checksum, why):
    assert any(".checksum" in p for p in problems_for(checksum=checksum)), why


def test_a_missing_key_is_reported_by_name():
    entry = package()
    del entry["checksum"]
    assert any(".checksum None" in p for p in package_validation_errors([entry], "deb"))


def test_a_duplicate_name_is_rejected():
    problems = package_validation_errors([package(), package()], "deb")
    assert problems == ["external_packages_list[1].name 'sops' is listed twice"]


def test_an_unknown_key_is_rejected():
    assert any("unknown keys ['sha256']" in p for p in problems_for(sha256="x"))


@pytest.mark.parametrize("packages", [None, "sops", {"name": "sops"}])
def test_a_non_list_is_rejected(packages):
    assert package_validation_errors(packages, "deb") == ["external_packages_list is not a list"]


def test_an_entry_that_is_not_a_dict_is_rejected():
    assert package_validation_errors(["sops"], "deb") == [
        "external_packages_list[0] is not a dict with 'name', 'version', 'url' and 'checksum'"
    ]


def test_every_problem_is_reported_in_one_pass():
    problems = package_validation_errors(
        [{"name": "-x", "version": "v1", "url": "x", "checksum": "y"}], "deb"
    )
    assert len(problems) == 4
