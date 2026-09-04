# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Every filter this collection ships must be documented, as `ansible-doc` reads it.

The filters are public API, so `ansible-doc -t filter binarycodes.homelab.<name>` is how a
consumer discovers them. Nothing in `ansible-lint` or `ansible-test sanity` checks a filter
plugin's docs, so an undocumented filter ships silently -- which is exactly what happened
before these tests existed. They fail if a filter has no DOCUMENTATION, if its declared
name does not match the name it registers, or if ansible-doc cannot parse what it finds.

`ansible-doc` resolves a filter only through an `ansible_collections/<ns>/<name>/` path;
conftest.py's `collection_path` fixture provides one.
"""

import json

import pytest
import yaml

from conftest import COLLECTION, FILTER_FILES, ansible_doc as _ansible_doc, load_filter_module


def ansible_doc(collection_path, *args):
    return _ansible_doc(collection_path, "filter", *args)


def registered_filters(path):
    """The filter names a plugin file registers through its FilterModule."""
    return sorted(load_filter_module(path).FilterModule().filters())


ALL_FILTERS = sorted(n for p in FILTER_FILES for n in registered_filters(p))


def test_there_are_filters_to_check():
    # Guards the tests below against silently passing on an empty set.
    assert ALL_FILTERS, "no filters discovered in plugins/filter/"


@pytest.mark.parametrize("path", FILTER_FILES, ids=lambda p: p.name)
def test_each_file_registers_exactly_one_filter(path):
    # One filter per file, so DOCUMENTATION in the file can only mean that filter.
    assert len(registered_filters(path)) == 1


@pytest.mark.parametrize("path", FILTER_FILES, ids=lambda p: p.name)
def test_documentation_names_the_filter_the_file_registers(path):
    module = load_filter_module(path)
    doc = getattr(module, "DOCUMENTATION", None)
    assert doc, f"{path.name} has no DOCUMENTATION"
    parsed = yaml.safe_load(doc)
    assert parsed["name"] == registered_filters(path)[0]
    # The name is also how ansible-doc addresses it, so it must match the file.
    assert parsed["name"] == path.stem
    assert parsed["short_description"]
    assert parsed["description"]
    assert parsed["options"]["_input"], "the piped-in value must be documented"


@pytest.mark.parametrize("path", FILTER_FILES, ids=lambda p: p.name)
def test_return_and_examples_are_present_and_parse(path):
    module = load_filter_module(path)
    assert yaml.safe_load(module.RETURN)["_value"]["description"]
    assert yaml.safe_load(module.EXAMPLES), f"{path.name} has no EXAMPLES"


def test_ansible_doc_reports_no_undocumented_filter(collection_path):
    result = ansible_doc(collection_path, "-l", COLLECTION)
    assert result.returncode == 0, result.stderr
    undocumented = [l for l in result.stdout.splitlines() if "UNDOCUMENTED" in l]
    assert not undocumented, f"ansible-doc reports undocumented filters: {undocumented}"
    listed = {l.split()[0].split(".")[-1] for l in result.stdout.splitlines() if l.strip()}
    assert listed == set(ALL_FILTERS), f"listed {sorted(listed)}, registered {ALL_FILTERS}"


@pytest.mark.parametrize("name", ALL_FILTERS)
def test_ansible_doc_renders_each_filter(collection_path, name):
    result = ansible_doc(collection_path, "--json", f"{COLLECTION}.{name}")
    assert result.returncode == 0, result.stderr
    doc = json.loads(result.stdout)[f"{COLLECTION}.{name}"]["doc"]
    assert doc["short_description"]
