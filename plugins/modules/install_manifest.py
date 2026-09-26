#!/usr/bin/python
# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``install_manifest`` module. Runs on the managed host, where the record is."""

from __future__ import annotations

DOCUMENTATION = r"""
module: install_manifest
short_description: Reconcile the files an app installed against the record of its last deploy
version_added: 1.1.0
author:
  - binarycodes (@binarycodes)
description:
  - Keeps one app's install manifest - the absolute host paths its last deploy installed, one
    per line. Prunes what the record names and O(installed) does not, then records
    O(installed). On O(state=absent) removes everything recorded, and the record.
  - Acted on as root, so every line is checked first. A line must be one segment directly
    inside O(system_dir) or O(unit_dir), a C(<unit>.d/<name>.conf) drop-in inside
    O(unit_dir), or nested under O(config_dir) or O(private_dir) with no empty, C(.) or C(..)
    segment - and must be a regular file, a symlink or missing. A directory is refused;
    nothing is ever removed recursively. One illegal line refuses the whole record.
    O(installed) is held to the same rules.
  - A directory a prune empties is removed too, upwards until a directory still holds
    something. O(system_dir) and O(unit_dir) are the host's and are never removed; O(config_dir)
    and O(private_dir) hold nothing but what the app ships, so an emptied one goes and the walk
    ends there. C(rmdir) refuses a non-empty directory, so a C(<unit>.d/) with a hand-written
    override survives by construction rather than by a check.
  - The record is written last, so a failure part-way leaves the older, wider record standing.
  - Supports check mode and diff mode.
options:
  path:
    description: The record. Its directory must exist.
    type: path
    required: true
  installed:
    description:
      - Every absolute host path this deploy installed. A recorded path not listed is pruned.
        Ignored when O(state=absent).
    type: list
    elements: str
    default: []
  system_dir:
    description: The Quadlet install directory, normally C(/etc/containers/systemd).
    type: path
    required: true
  unit_dir:
    description: The plain-unit install directory, normally C(/etc/systemd/system).
    type: path
    required: true
  config_dir:
    description: The app's own deployed config tree, normally C(/var/app/<app>/config).
    type: path
    required: true
  private_dir:
    description:
      - The app's encrypted private files, normally C(/var/app/<app>/private). Recorded and
        pruned like O(config_dir), but reported separately - a change there is acted on by
        the app's decrypt unit, not the app.
    type: path
    required: true
    version_added: 1.2.0
  state:
    description:
      - C(present) prunes and records. C(absent) removes everything recorded and the record.
    type: str
    choices: [present, absent]
    default: present
extends_documentation_fragment:
  - ansible.builtin.files
"""

EXAMPLES = r"""
- name: Prune what the last deploy installed and this one did not, then record this one
  binarycodes.homelab.install_manifest:
    path: /var/app/myapp/.install-manifest
    installed:
      - /etc/containers/systemd/myapp.container
      - /etc/systemd/system/myapp.service.d/10-private.conf
      - /var/app/myapp/config/app.conf
      - /var/app/myapp/private/db.env.age
    system_dir: /etc/containers/systemd
    unit_dir: /etc/systemd/system
    config_dir: /var/app/myapp/config
    private_dir: /var/app/myapp/private
    mode: "0644"
  register: myapp_manifest

- name: Learn what the app is running, without removing anything yet
  binarycodes.homelab.install_manifest:
    path: /var/app/myapp/.install-manifest
    system_dir: /etc/containers/systemd
    unit_dir: /etc/systemd/system
    config_dir: /var/app/myapp/config
    private_dir: /var/app/myapp/private
    state: absent
  check_mode: true
  register: myapp_manifest

- name: Stop them
  ansible.builtin.systemd:
    name: "{{ item }}"
    state: stopped
  loop: "{{ myapp_manifest.units }}"

- name: Remove everything the app installed, and the record
  binarycodes.homelab.install_manifest:
    path: /var/app/myapp/.install-manifest
    system_dir: /etc/containers/systemd
    unit_dir: /etc/systemd/system
    config_dir: /var/app/myapp/config
    private_dir: /var/app/myapp/private
    state: absent
"""

RETURN = r"""
pruned:
  description: Recorded paths that were removed.
  type: list
  elements: str
  returned: always
recorded:
  description: What the record holds after this call. Empty on absent.
  type: list
  elements: str
  returned: always
config_changed:
  description: Whether any pruned path was under O(config_dir) - a change a running app still reads.
  type: bool
  returned: always
private_changed:
  description:
    - Whether any pruned path was under O(private_dir). Answered by restarting the app's
      decrypt unit, which owns the runtime copy, not by reloading the app.
  type: bool
  returned: always
  version_added: 1.2.0
pruned_dirs:
  description: Directories removed because the prune left them empty.
  type: list
  elements: str
  returned: always
  version_added: 1.2.0
pruned_units:
  description:
    - The units the pruned paths implied, mapped as RV(units) is, so a deploy can disable a
      unit while its file still exists - in check mode, before the call that removes it. On
      O(state=absent), every unit recorded.
  type: list
  elements: str
  returned: always
units:
  description:
    - The units the record implied when read, so a decommission needs no unit names.
      C(.container) and C(.kube) map to C(<name>.service), C(.pod) to C(<name>-pod.service);
      C(.volume), C(.network), C(.image) and C(.build) run nothing and are left out. A plain
      unit file under O(unit_dir) is its own name.
  type: list
  elements: str
  returned: always
"""

import errno
import os
import stat
import tempfile
from collections.abc import Iterable, Mapping
from typing import Any, Literal, TypedDict

from ansible.module_utils.basic import AnsibleModule

# Quadlet file suffix -> the suffix systemd's generator gives the unit it produces.
_QUADLET_UNIT_SUFFIXES = {
    ".container": ".service",
    ".kube": ".service",
    ".pod": "-pod.service",
}

# Unit types a plain unit file may be, and that stopping means something for. A .target,
# .slice or .scope is not something an app ships and not something teardown stops.
_PLAIN_UNIT_SUFFIXES = (
    ".service",
    ".socket",
    ".timer",
    ".path",
    ".mount",
    ".automount",
)

# A drop-in lives in <unit>.d/ beside its unit: two segments, the second a .conf file.
_DROPIN_DIR_SUFFIX = ".d"
_DROPIN_FILE_SUFFIX = ".conf"

# An empty segment is a doubled or trailing slash; the other two climb out of the root.
_FORBIDDEN_SEGMENTS = frozenset(["", ".", ".."])

# What sits at a path, as `Files.kind` reports it.
Kind = Literal["file", "link", "missing", "other"]


class Diff(TypedDict):
    before: str
    after: str


class Result(TypedDict):
    """The module's return, less the fields AnsibleModule adds; see RETURN."""

    changed: bool
    pruned: list[str]
    recorded: list[str]
    config_changed: bool
    private_changed: bool
    pruned_dirs: list[str]
    pruned_units: list[str]
    units: list[str]
    diff: Diff


class InstallManifestError(Exception):
    """A failure the module reports with fail_json; carries the fields to report."""

    def __init__(self, msg: str, **fields: Any) -> None:
        super().__init__(msg)
        self.msg = msg
        self.fields = fields


class Files:
    """Everything the module does to the host, so `reconcile` can be driven against a tmp dir."""

    def read_lines(self, path: str) -> list[str] | None:
        """The record's non-blank lines, or None when there is no record."""
        try:
            with open(path, "rb") as handle:
                text = handle.read().decode("utf-8")
        except IOError as exc:
            if exc.errno == errno.ENOENT:
                return None
            raise
        return [line.strip() for line in text.splitlines() if line.strip()]

    def kind(self, path: str) -> Kind:
        """What sits at `path`: 'file', 'link', 'missing' or 'other' (a directory, a device...)."""
        try:
            mode = os.lstat(path).st_mode
        except OSError:
            return "missing"
        if stat.S_ISREG(mode):
            return "file"
        if stat.S_ISLNK(mode):
            return "link"
        return "other"

    def unlink(self, path: str) -> None:
        """Remove one file or symlink; a path already gone is not an error."""
        try:
            os.unlink(path)
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise

    def rmdir(self, path: str) -> bool:
        """Remove `path` if it is an empty directory. Every refusal means leave it alone, so
        ENOTEMPTY, ENOENT and ENOTDIR are not told apart."""
        try:
            os.rmdir(path)
        except OSError:
            return False
        return True

    def write(self, path: str, text: str) -> None:
        """Replace the record atomically; a replaced one keeps its mode, a new one is 0644."""
        existing = self.kind(path)
        directory = os.path.dirname(path) or "."
        fd, tmp = tempfile.mkstemp(prefix=".install-manifest.", dir=directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(text.encode("utf-8"))
                # Before the rename: an empty record reads as "this app installed nothing",
                # leaving every installed file with none naming it.
                handle.flush()
                os.fsync(handle.fileno())
            if existing == "file":
                os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
            else:
                os.chmod(tmp, 0o644)
            os.rename(tmp, path)
            self._sync_dir(directory)
        finally:
            # Gone after a successful rename; still here after a failure part-way.
            if os.path.lexists(tmp):
                os.unlink(tmp)

    def _sync_dir(self, path: str) -> None:
        """Persist the rename. It has already succeeded, so a refusal must not fail the run."""
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)


def _under(path: str, root: str) -> list[str] | None:
    """The segments of `path` below `root`, or None when it does not sit under it."""
    prefix = root.rstrip("/") + "/"
    if not path.startswith(prefix):
        return None
    return path[len(prefix):].split("/")


def _is_dropin(segments: list[str]) -> bool:
    """Whether these segments below the unit directory spell a unit's drop-in file."""
    if len(segments) != 2:
        return False
    directory, name = segments
    if not directory.endswith(_DROPIN_DIR_SUFFIX):
        return False
    unit = directory[: -len(_DROPIN_DIR_SUFFIX)]
    if unit.startswith(".") or not unit.endswith(_PLAIN_UNIT_SUFFIXES):
        return False
    return name.endswith(_DROPIN_FILE_SUFFIX) and len(name) > len(_DROPIN_FILE_SUFFIX)


def check_shape(path: str, system_dir: str, unit_dir: str, config_dir: str,
                private_dir: str) -> str | None:
    """Why `path` may not be recorded, or None when it is one of the legal shapes."""
    if not path.startswith("/"):
        return "is not an absolute path"
    for root in (system_dir, unit_dir):
        segments = _under(path, root)
        if segments is not None:
            if len(segments) == 1 and segments[0] not in _FORBIDDEN_SEGMENTS:
                return None
            # Unit directory only: a drop-in under system_dir would override nothing.
            if root == unit_dir and _is_dropin(segments):
                return None
            if root == unit_dir:
                return ("is neither a single path segment directly inside %s nor a "
                        "<unit>.d/<name>.conf drop-in below it" % root)
            return "is not a single path segment directly inside %s" % root
    for root in (config_dir, private_dir):
        segments = _under(path, root)
        if segments is not None:
            if any(segment in _FORBIDDEN_SEGMENTS for segment in segments):
                return "has an empty, '.' or '..' segment below %s" % root
            return None
    return "is outside %s, %s, %s and %s" % (system_dir, unit_dir, config_dir, private_dir)


def validate(paths: Iterable[str], files: Files, system_dir: str, unit_dir: str, config_dir: str,
             private_dir: str, what: str) -> None:
    """Refuse the whole list if any path is the wrong shape or names something not a file."""
    refused: list[str] = []
    for path in paths:
        why = check_shape(path, system_dir, unit_dir, config_dir, private_dir)
        if why is None and files.kind(path) == "other":
            why = "names a directory or something else that is not a regular file or symlink"
        if why is not None:
            refused.append("%s %s" % (path, why))
    if refused:
        raise InstallManifestError(
            "%s names paths this module will not act on: %s. Refusing to remove anything - "
            "the record may be corrupt or tampered with." % (what, "; ".join(refused)),
            refused=refused,
        )


def _unit_for(name: str, suffixes: Mapping[str, str]) -> str | None:
    """The unit `name` implies, or None when this module does not map it."""
    for suffix, unit_suffix in suffixes.items():
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)] + unit_suffix
    return None


def units_of(paths: Iterable[str], system_dir: str, unit_dir: str) -> list[str]:
    """The systemd units a set of recorded paths implies, sorted and deduplicated."""
    units: set[str] = set()
    for path in paths:
        parent, name = os.path.split(path)
        if parent == unit_dir.rstrip("/"):
            # A bare suffix is a name systemd has no unit for, and the shape check admits it:
            # it excludes '.' and '..' but not '.service'.
            if name.endswith(_PLAIN_UNIT_SUFFIXES) and not name.startswith("."):
                units.add(name)
        elif parent == system_dir.rstrip("/"):
            unit = _unit_for(name, _QUADLET_UNIT_SUFFIXES)
            if unit:
                units.add(unit)
    return sorted(units)


def _text(paths: Iterable[str]) -> str:
    return "".join(path + "\n" for path in paths)


def prune_dirs(files: Files, pruned: Iterable[str], stops: Iterable[str],
               removable: Iterable[str]) -> list[str]:
    """Remove the directories a prune emptied, deepest first. A `stops` root is the host's and
    is never removed; a `removable` root is the app's own, so an emptied one goes and the walk
    ends there rather than climbing into the app's home. `rmdir` refusing a non-empty directory
    is what stops the walk otherwise, so no check of its own is needed."""
    keep = set(root.rstrip("/") for root in stops)
    ends = set(root.rstrip("/") for root in removable)
    removed: list[str] = []
    parents = sorted(set(os.path.dirname(entry) for entry in pruned), key=len, reverse=True)
    for parent in parents:
        directory = parent
        while directory and directory != "/" and directory not in keep:
            if not files.rmdir(directory):
                break
            removed.append(directory)
            if directory in ends:
                break
            directory = os.path.dirname(directory)
    return sorted(set(removed))


def reconcile(files: Files, path: str, installed: Iterable[object], system_dir: str, unit_dir: str,
              config_dir: str, private_dir: str, state: str, check_mode: bool) -> Result:
    """Prune, record or remove, and describe what was done, in the module's return shape."""
    recorded_before = files.read_lines(path)
    had_record = recorded_before is not None
    recorded_before = sorted(set(recorded_before or []))
    validate(recorded_before, files, system_dir, unit_dir, config_dir, private_dir,
             "the record %s" % path)

    if state == "present":
        recorded_after = sorted(set(str(entry) for entry in installed))
        validate(recorded_after, files, system_dir, unit_dir, config_dir, private_dir,
                 "'installed'")
    else:
        recorded_after = []

    keep = set(recorded_after)
    pruned = [entry for entry in recorded_before if entry not in keep]
    record_changes = recorded_after != recorded_before or (state == "absent" and had_record)

    pruned_dirs: list[str] = []
    if not check_mode:
        # Removals first and the record last: a failure between the two leaves the older,
        # wider record in place, so nothing is forgotten.
        for entry in pruned:
            files.unlink(entry)
        # Before the record, like the unlinks: an empty directory left behind is harmless.
        pruned_dirs = prune_dirs(files, pruned, (system_dir, unit_dir), (config_dir, private_dir))
        if state == "present":
            if record_changes:
                files.write(path, _text(recorded_after))
        elif had_record:
            files.unlink(path)

    config_prefix = config_dir.rstrip("/") + "/"
    private_prefix = private_dir.rstrip("/") + "/"
    return {
        "changed": bool(pruned) or record_changes,
        "pruned": pruned,
        "recorded": recorded_after,
        "config_changed": any(entry.startswith(config_prefix) for entry in pruned),
        "private_changed": any(entry.startswith(private_prefix) for entry in pruned),
        "pruned_dirs": pruned_dirs,
        "pruned_units": units_of(pruned, system_dir, unit_dir),
        "units": units_of(recorded_before, system_dir, unit_dir),
        "diff": {"before": _text(recorded_before), "after": _text(recorded_after)},
    }


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            path=dict(type="path", required=True),
            installed=dict(type="list", elements="str", default=[]),
            system_dir=dict(type="path", required=True),
            unit_dir=dict(type="path", required=True),
            config_dir=dict(type="path", required=True),
            private_dir=dict(type="path", required=True),
            state=dict(type="str", choices=["present", "absent"], default="present"),
        ),
        add_file_common_args=True,
        supports_check_mode=True,
    )
    params = module.params

    try:
        result = reconcile(
            Files(), params["path"], params["installed"], params["system_dir"],
            params["unit_dir"], params["config_dir"], params["private_dir"], params["state"],
            module.check_mode,
        )
    except InstallManifestError as exc:
        module.fail_json(msg=exc.msg, **exc.fields)
    except (IOError, OSError) as exc:
        module.fail_json(msg="%s: %s" % (getattr(exc, "filename", None) or params["path"], exc))

    # Ownership and mode come from the same options ansible.builtin.file takes, applied to
    # the record once it exists. Only then: in check mode a first deploy has no record yet.
    if params["state"] == "present" and os.path.lexists(params["path"]):
        file_args = module.load_file_common_arguments(params)
        result["changed"] = module.set_fs_attributes_if_different(file_args, result["changed"])
    module.exit_json(**result)


if __name__ == "__main__":
    main()
