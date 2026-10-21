# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the 'systemd_slices' role's filters."""

import pytest

from plugins.filter.slice_validation_errors import slice_validation_errors


def unit(**fields):
    return {"name": "homelab", **fields}


def problems_for(**fields):
    return slice_validation_errors([unit(**fields)])


# --- slice_validation_errors ------------------------------------------------------------

def test_no_slices_is_valid():
    assert slice_validation_errors([]) == []


def test_a_full_slice_is_accepted():
    assert problems_for(
        description="Homelab apps",
        settings={"MemoryMax": "4G", "CPUWeight": 50, "AllowedCPUs": "0-3"},
    ) == []


@pytest.mark.parametrize("name", ["homelab", "homelab-media", "a-b-c", "x_1", "a:b", "a.b"])
def test_good_names_are_accepted(name):
    assert slice_validation_errors([{"name": name}]) == []


def test_a_name_at_exactly_the_maximum_is_accepted():
    # 255 for the unit name, '.slice' included; the check must not be off by one.
    assert slice_validation_errors([{"name": "a" * 249}]) == []


@pytest.mark.parametrize(
    "name, why",
    [
        ("../etc/x", "path traversal"),
        ("a/b", "slash would leave the unit directory"),
        ("..", "parent directory"),
        ("a..b", "'..' anywhere"),
        ("homelab.slice", "the role adds the suffix"),
        ("-", "the root slice"),
        ("-a", "leading dash"),
        ("a-", "trailing dash"),
        ("a--b", "empty nesting component"),
        ("a@b", "template marker"),
        ("a b", "space"),
        ("a\n", "trailing newline, which a '$' anchor would have allowed"),
        ("", "empty"),
        (None, "missing"),
        (5, "not a string"),
        ("a" * 250, "over the 255-character unit name maximum"),
    ],
)
def test_bad_names_are_rejected(name, why):
    problems = slice_validation_errors([{"name": name}])
    assert any(".name" in p for p in problems), why


def test_a_missing_name_is_rejected():
    assert slice_validation_errors([{"description": "x"}])


def test_a_duplicate_name_is_rejected():
    problems = slice_validation_errors([{"name": "a"}, {"name": "a"}])
    assert problems == ["systemd_slices_units[1].name 'a' is listed twice"]


@pytest.mark.parametrize("description", ["two\nlines", "cr\r", 5, ["x"]])
def test_bad_descriptions_are_rejected(description):
    assert any(".description" in p for p in problems_for(description=description))


@pytest.mark.parametrize("key", ["memoryMax", "Memory Max", "Memory=Max", "", 1, "Mem\n"])
def test_bad_setting_keys_are_rejected(key):
    assert any("key" in p for p in problems_for(settings={key: "1"}))


@pytest.mark.parametrize("value", ["4G\nCPUWeight=1", 1.5, None, ["a"], {"a": 1}])
def test_bad_setting_values_are_rejected(value):
    assert any("settings.MemoryMax" in p for p in problems_for(settings={"MemoryMax": value}))


@pytest.mark.parametrize("value", [True, False])
def test_a_boolean_setting_asks_for_quoting(value):
    assert any("quote it" in p for p in problems_for(settings={"MemoryAccounting": value}))


@pytest.mark.parametrize("settings", ["MemoryMax=4G", ["MemoryMax=4G"]])
def test_settings_that_are_not_a_dict_are_rejected(settings):
    assert any(".settings is not a dict" in p for p in problems_for(settings=settings))


def test_an_unknown_key_is_rejected():
    assert any("unknown keys ['setings']" in p for p in problems_for(setings={}))


@pytest.mark.parametrize("units", [None, "homelab", {"name": "homelab"}])
def test_a_non_list_is_rejected(units):
    assert slice_validation_errors(units) == ["systemd_slices_units is not a list"]


def test_an_entry_that_is_not_a_dict_is_rejected():
    assert slice_validation_errors(["homelab"]) == [
        "systemd_slices_units[0] is not a dict with at least a 'name'"
    ]


def test_every_problem_is_reported_in_one_pass():
    problems = slice_validation_errors([
        {"name": "a/b", "description": "x\ny", "settings": {"MemoryMax": True}},
    ])
    assert len(problems) == 3
