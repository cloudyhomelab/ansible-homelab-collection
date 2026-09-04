# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Makes the collection's plugins importable by the tests below.

`plugins/filter/` and `plugins/modules/` are not packages -- ansible loads those files
itself -- and the tests run as plain pytest rather than through `ansible-test units`, so
there is no `ansible_collections.` import path to reach them by. Each file is therefore
loaded from its path and registered under a prefixed module name: importing `secrets` by its
own name would put a module ahead of the standard library's on `sys.path`.

Filters are discovered rather than listed, so a filter added to the collection is picked up
here without editing this file -- and so test_filter_docs.py can check the full set. The
`collection_path` fixture is the tree `ansible-doc` resolves the collection through: it only
finds a plugin under an `ansible_collections/<ns>/<name>/` path, so the checkout is symlinked
into a throwaway tree rather than moved.
"""

import importlib.util
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FILTER_DIR = ROOT / "plugins" / "filter"
MODULE_DIR = ROOT / "plugins" / "modules"
COLLECTION = "binarycodes.homelab"


def load_filter_module(path):
    """Load one plugins/filter/*.py under a prefixed module name."""
    return load_plugin(path, "systemd_app_")


def load_plugin(path, prefix):
    """Load one plugin file by path under a prefixed module name."""
    spec = importlib.util.spec_from_file_location(f"{prefix}{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


FILTER_FILES = sorted(p for p in FILTER_DIR.glob("*.py") if not p.name.startswith("_"))

for _path in FILTER_FILES:
    load_filter_module(_path)


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
