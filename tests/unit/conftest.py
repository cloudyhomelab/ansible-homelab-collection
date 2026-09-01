# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Makes the collection's filter plugins importable by the tests below.

`plugins/filter/` is not a package -- ansible loads those files itself -- and the tests run
as plain pytest rather than through `ansible-test units`, so there is no
`ansible_collections.` import path to reach them by. Each file is therefore loaded from its
path and registered under a prefixed module name: importing them by their own names would
put a module called `secrets` ahead of the standard library's on `sys.path`.
"""

import importlib.util
import pathlib
import sys

_FILTER_DIR = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "filter"

for _name in ("secrets", "validation", "systemd"):
    _spec = importlib.util.spec_from_file_location(
        f"systemd_app_{_name}", _FILTER_DIR / f"{_name}.py"
    )
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _module
    _spec.loader.exec_module(_module)
