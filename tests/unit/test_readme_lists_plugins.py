# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Both READMEs name every plugin the collection ships, and nothing it does not.

The plugins are public API and the READMEs are where a consumer first meets them, but no
gate reads a README: a filter added with full docs shipped once with no row in either table.
The root README lists every module and filter, deprecated ones included, since a consumer may
still be calling those. The role README lists only what the role calls, which is every
plugin that is not deprecated.
"""

import ast
import re

import yaml

from conftest import FILTER_FILES, MODULE_DIR, ROOT

ROOT_README = ROOT / "README.md"
ROLE_README = ROOT / "roles" / "systemd_app" / "README.md"
MODULE_FILES = sorted(p for p in MODULE_DIR.glob("*.py") if not p.name.startswith("_"))

# A table row whose first cell is a backquoted plugin name.
_ROW_RE = re.compile(r"^\| `([a-z_]+)`\s*\|", re.MULTILINE)


def documentation(path):
    """A plugin's DOCUMENTATION, read from the source rather than imported: a module imports
    ansible.module_utils, which the filters' loader in conftest does not set up."""
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "DOCUMENTATION" for t in node.targets
        ):
            assert isinstance(node.value, ast.Constant)
            return yaml.safe_load(node.value.value)
    raise AssertionError(f"{path.name} has no DOCUMENTATION")


def is_deprecated(path):
    return "deprecated" in documentation(path)


def rows_under(readme, header):
    """The plugin names in the table that follows `header`, up to the next blank line."""
    text = readme.read_text()
    start = text.index(header)
    table = text[start:].split("\n\n", 1)[0]
    return set(_ROW_RE.findall(table))


FILTERS = {p.stem for p in FILTER_FILES}
MODULES = {p.stem for p in MODULE_FILES}
CURRENT = {p.stem for p in FILTER_FILES + MODULE_FILES if not is_deprecated(p)}


def test_there_are_plugins_to_check():
    assert FILTERS and MODULES


def test_the_root_readme_lists_every_filter():
    assert rows_under(ROOT_README, "| Filter | Purpose |") == FILTERS


def test_the_root_readme_lists_every_module():
    assert rows_under(ROOT_README, "| Module | Purpose |") == MODULES


def test_the_role_readme_lists_every_plugin_the_role_calls():
    # Deprecated plugins are named in the prose below the table, not the table: the role
    # no longer calls them.
    assert rows_under(ROLE_README, "| Plugin ") == CURRENT


def test_a_deprecated_filter_is_marked_as_such_in_the_root_readme():
    text = ROOT_README.read_text()
    for path in FILTER_FILES:
        if is_deprecated(path):
            row = re.search(rf"^\| `{path.stem}`.*$", text, re.MULTILINE)
            assert row is not None, f"{path.stem} has no row in the root README"
            assert "Deprecated" in row.group(0), (
                f"{path.stem} is deprecated but its README row does not say so"
            )
