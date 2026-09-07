# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""What the tests share: where the plugins are, and a tree `ansible-doc` can resolve them in.

The tests run as plain pytest rather than through `ansible-test units`, so there is no
`ansible_collections.` import path to reach a plugin by. They import each one through the
checkout root instead -- `plugins.filter.<name>`, `plugins.modules.<name>` -- which
pytest.ini puts on `sys.path`, and which mypy resolves to the same file, so the calls a test
makes are checked against the plugin's own signature.

Filters are discovered rather than listed, so a filter added to the collection is picked up
by test_filter_docs.py without editing this file. The `collection_path` fixture is the tree
`ansible-doc` resolves the collection through: it only finds a plugin under an
`ansible_collections/<ns>/<name>/` path, so the checkout is symlinked into a throwaway tree
rather than moved.
"""

import importlib
import os
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FILTER_DIR = ROOT / "plugins" / "filter"
MODULE_DIR = ROOT / "plugins" / "modules"
COLLECTION = "binarycodes.homelab"

FILTER_FILES = sorted(p for p in FILTER_DIR.glob("*.py") if not p.name.startswith("_"))


def import_filter(path):
    """The module of one plugins/filter/*.py, under the name every other test imports it by."""
    return importlib.import_module(f"plugins.filter.{path.stem}")


@pytest.fixture(scope="session")
def collection_path(tmp_path_factory):
    """A tree ansible-doc will resolve the collection through."""
    root = tmp_path_factory.mktemp("collections")
    link = root / "ansible_collections" / "binarycodes" / "homelab"
    link.parent.mkdir(parents=True)
    link.symlink_to(ROOT, target_is_directory=True)
    return root


def ansible_doc(collection_path, plugin_type, *args):
    env = {**os.environ, "ANSIBLE_COLLECTIONS_PATH": str(collection_path)}
    return subprocess.run(
        ["ansible-doc", "-t", plugin_type, *args],
        capture_output=True, text=True, env=env, cwd=ROOT, check=False,
    )
