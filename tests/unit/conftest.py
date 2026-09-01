# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Makes the collection's filter plugins importable by the tests below.

`plugins/filter/` is not a package -- ansible loads those files itself -- and the tests run
as plain pytest rather than through `ansible-test units`, so there is no
`ansible_collections.` import path to reach them by. Each file is therefore loaded from its
path and registered under a prefixed module name: importing `secrets` by its own name would
put a module ahead of the standard library's on `sys.path`.

Discovered rather than listed, so a filter added to the collection is picked up here
without editing this file -- and so test_filter_docs.py can check the full set.
"""

import importlib.util
import pathlib
import sys

FILTER_DIR = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "filter"


def load_filter_module(path):
    """Load one plugins/filter/*.py under a prefixed module name."""
    spec = importlib.util.spec_from_file_location(f"systemd_app_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


FILTER_FILES = sorted(p for p in FILTER_DIR.glob("*.py") if not p.name.startswith("_"))

for _path in FILTER_FILES:
    load_filter_module(_path)
