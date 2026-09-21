# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``private_tree`` filter. Runs on the controller, where the app definitions are."""

from __future__ import annotations

import os
import posixpath
from collections.abc import Callable, Iterable
from typing import TypedDict


DOCUMENTATION = r"""
name: private_tree
short_description: What an app ships in C(private/), where it is copied, and what it decrypts to
version_added: 1.2.0
author:
  - binarycodes (@binarycodes)
description:
  - Reads one app's C(private/) directory on the controller and returns the files a deploy
    copies to the host, the absolute host path each lands at, which is what the app's install
    manifest records, and what each one decrypts to at unit start.
  - The files are copied to the host B(still encrypted). Nothing here decrypts anything and
    nothing here reads a key; C(tool) and C(out) say what the host's decrypt unit will do, so a
    mistake in the naming is a failed play rather than a failed unit.
  - A C(.age) suffix means the file is raw age, a C(.sops.<ext>) component means it is SOPS, and
    the marker is dropped from the decrypted name - C(db.env.age) becomes C(db.env) and
    C(config.sops.yaml) becomes C(config.yaml). Anything else is copied through unchanged, so an
    app may keep a public certificate beside its private key.
  - C(private/) is a tree copied with its layout, like C(config/) and unlike C(quadlet/), so
    every file under it is listed, hidden ones and nested ones included, with its path relative
    to C(private/). Only the base name decides the tool; directory names are mirrored verbatim.
  - Returns empty rather than raising when the app has no directory or no C(private/). Unlike a
    C(source) app's tree, which is the whole of what a deploy installs and whose absence would
    silently prune everything, an app with no private files is the ordinary case, and an
    C(inline) app may have no directory on the controller at all.
  - Two files decrypting to one name is reported in RV(_value.errors) rather than raised, so one
    run reports every problem at once as the role's other validation does.
  - Runs on the controller as C(fileglob) does; each entry's C(src) is a controller path and
    RV(_value.installed) are host paths.
positional: private_dir
options:
  _input:
    description: The app's directory on the controller, normally C(<systemd_app_apps_dir>/<name>).
    type: str
    required: true
  private_dir:
    description: Where the encrypted tree lands on the host, normally C(/var/app/<name>/private).
    type: str
    required: true
"""

RETURN = r"""
_value:
  description: What the app ships in C(private/) and what becomes of it. Every list is sorted.
  type: dict
  contains:
    files:
      description:
        - One entry per file, as C(src) (controller path), C(path) (relative to C(private/)),
          C(tool) (C(age), C(sops) or C(copy)) and C(out) (the decrypted path, relative to the
          app's runtime private directory).
      type: list
      elements: dict
    dir:
      description: The controller path of C(private/) when the app ships one, else none.
      type: str
    installed:
      description: The absolute host path of every file, as the install manifest records them.
      type: list
      elements: str
    errors:
      description: One message per pair of files that decrypt to the same name. Empty when fine.
      type: list
      elements: str
"""

EXAMPLES = r"""
- name: Learn what the app keeps private and where it lands
  ansible.builtin.set_fact:
    private: >-
      {{ '/srv/apps/myapp' | binarycodes.homelab.private_tree('/var/app/myapp/private') }}
  # For private/db.env.age and private/tls/server.sops.yaml:
  #   files:     [{'src': '/srv/apps/myapp/private/db.env.age', 'path': 'db.env.age',
  #                'tool': 'age', 'out': 'db.env'},
  #               {'src': '/srv/apps/myapp/private/tls/server.sops.yaml',
  #                'path': 'tls/server.sops.yaml', 'tool': 'sops', 'out': 'tls/server.yaml'}]
  #   dir:       '/srv/apps/myapp/private'
  #   installed: ['/var/app/myapp/private/db.env.age',
  #               '/var/app/myapp/private/tls/server.sops.yaml']
  #   errors:    []
"""

# --- shared with roles/systemd_app/files/helpers/homelab_decrypt_private.py -------------
#
# The helper applies these on the host at unit start and this filter applies them on the
# controller, so a collision is a failed play rather than a failed unit. Keep the two copies
# identical; one table of cases in tests/unit/conftest.py runs against both.

_AGE_SUFFIX = ".age"
_SOPS_MARKER = ".sops."


def action_for(name: str) -> tuple[str, str]:
    """What to do with one file name from private/, and what the result is called."""
    if name.endswith(_AGE_SUFFIX) and len(name) > len(_AGE_SUFFIX):
        return "age", name[: -len(_AGE_SUFFIX)]
    head, marker, tail = name.rpartition(_SOPS_MARKER)
    if marker and head and tail:
        return "sops", head + "." + tail
    return "copy", name


def decrypted_path(relpath: str) -> tuple[str, str]:
    """(tool, output path) for one path relative to private/. Directories are mirrored."""
    parent, _slash, name = relpath.rpartition("/")
    tool, out = action_for(name)
    return tool, (parent + "/" + out if parent else out)


def collisions(relpaths: Iterable[str]) -> list[str]:
    """One message per pair of inputs that decrypt to the same path, sorted."""
    seen: dict[str, str] = {}
    clashes: list[str] = []
    for relpath in sorted(relpaths):
        _tool, out = decrypted_path(relpath)
        if out in seen:
            clashes.append(
                "private/%s and private/%s both decrypt to private/%s" % (seen[out], relpath, out)
            )
        else:
            seen[out] = relpath
    return clashes


# --- the filter itself ------------------------------------------------------------------


class PrivateFile(TypedDict):
    src: str
    path: str
    tool: str
    out: str


class PrivateTree(TypedDict):
    """The filter's return; see RETURN."""

    files: list[PrivateFile]
    dir: str | None
    installed: list[str]
    errors: list[str]


def private_tree(app_dir: object, private_dir: str) -> PrivateTree:
    """What an app keeps in private/, where it is copied, and what it decrypts to."""
    source = os.path.join(str(app_dir), "private")
    if not os.path.isdir(source):
        return {"files": [], "dir": None, "installed": [], "errors": []}

    relpaths = _tree_files(source)
    files: list[PrivateFile] = []
    for relpath in relpaths:
        tool, out = decrypted_path(relpath)
        files.append(
            {"src": os.path.join(source, relpath), "path": relpath, "tool": tool, "out": out}
        )
    return {
        "files": files,
        "dir": source,
        "installed": sorted(posixpath.join(private_dir, entry["path"]) for entry in files),
        "errors": collisions(relpaths),
    }


def _tree_files(directory: str) -> list[str]:
    """Every file under `directory`, hidden ones included, as paths relative to it."""
    found: list[str] = []
    for root, _dirs, names in os.walk(directory):
        for name in names:
            path = os.path.join(root, name)
            # os.walk lists a symlink to a file among the files and one to a directory among
            # the dirs; only the former is a file the copy installs.
            if os.path.isfile(path):
                found.append(os.path.relpath(path, directory))
    return sorted(found)


class FilterModule:
    """Discovery of what an app keeps encrypted, and what it becomes on the host."""

    def filters(self) -> dict[str, Callable[..., object]]:
        return {"private_tree": private_tree}
