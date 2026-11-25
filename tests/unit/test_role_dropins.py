# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The role's own drop-in names are spelled once, in systemd_app_role_dropins.

An app drop-in is refused under a reserved name so it cannot overwrite the role's. That holds
only while the names the role writes and the names it reserves are the same, which nothing at
run time checks: a name renamed in one place and not the other lets an app's file silently
replace the role's.
"""

import yaml

from conftest import ROOT

ROLE = ROOT / "roles" / "systemd_app"
VARS = yaml.safe_load((ROLE / "vars" / "main.yml").read_text())
DROPINS = VARS["systemd_app_role_dropins"]
# Everything but the dict itself, so a name spelled out anywhere else in the role's variables
# is caught.
OTHER_VARS_TEXT = yaml.safe_dump({k: v for k, v in VARS.items() if k != "systemd_app_role_dropins"})
DEFAULTS_TEXT = (ROLE / "defaults" / "main.yml").read_text()


def test_every_role_dropin_is_reserved():
    assert "reserved_dropins=systemd_app_role_dropins.values() | list" in VARS["systemd_app_source_tree"]


def test_each_written_dropin_takes_its_name_from_the_dict():
    assert "systemd_app_role_dropins.private" in VARS["systemd_app_private_dropins"]
    assert "systemd_app_role_dropins.slice" in VARS["systemd_app_slice_dropins"]


def test_no_dropin_name_is_spelled_out_elsewhere_in_the_role_variables():
    for name in DROPINS.values():
        assert name not in DEFAULTS_TEXT
        assert name not in OTHER_VARS_TEXT


def test_the_names_are_drop_ins_systemd_reads():
    for name in DROPINS.values():
        assert name.endswith(".conf") and "/" not in name and not name.startswith(".")
