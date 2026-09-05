#!/usr/bin/python
# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``install_manifest`` module. Runs on the managed host, where the record is."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: install_manifest
short_description: Reconcile the files an app installed against the record of its last deploy
version_added: 1.1.0
author:
  - binarycodes (@binarycodes)
description:
  - Keeps one app's install manifest, a file listing the absolute host paths its last deploy
    installed, one per line. Prunes what that record names and O(installed) does not, then
    records O(installed). On O(state=absent), removes everything recorded and the record.
  - The record is acted on as root, so every line is checked before anything is removed. A
    line must be one segment directly inside O(system_dir) or O(unit_dir), or nested under
    O(config_dir) with no empty, C(.) or C(..) segment; and it must be a regular file, a
    symlink or missing. A directory is refused - the record is a list of files, and nothing
    on it is ever removed recursively. One illegal line refuses the whole record and removes
    nothing. O(installed) is held to the same rules before it is recorded.
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
      - /var/app/myapp/config/app.conf
    system_dir: /etc/containers/systemd
    unit_dir: /etc/systemd/system
    config_dir: /var/app/myapp/config
    mode: "0644"
  register: myapp_manifest

- name: Learn what the app is running, without removing anything yet
  binarycodes.homelab.install_manifest:
    path: /var/app/myapp/.install-manifest
    system_dir: /etc/containers/systemd
    unit_dir: /etc/systemd/system
    config_dir: /var/app/myapp/config
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
units:
  description:
    - The systemd units the record implied when read, so a decommission can stop them without
      being told the names. C(.container) and C(.kube) map to C(<name>.service), C(.pod) to
      C(<name>-pod.service); C(.volume), C(.network), C(.image) and C(.build) are not units
      that run anything and are left out. A plain C(.service), C(.socket), C(.timer),
      C(.path), C(.mount) or C(.automount) under O(unit_dir) is its own name.
  type: list
  elements: str
  returned: always
"""

import errno
import os
import stat
import tempfile

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

# Segments no recorded path may contain. An empty one is a doubled or trailing slash, and the
# other two are how a path climbs out of its root.
_FORBIDDEN_SEGMENTS = frozenset(["", ".", ".."])


class InstallManifestError(Exception):
    """A failure the module reports with fail_json; carries the fields to report."""

    def __init__(self, msg, **fields):
        super(InstallManifestError, self).__init__(msg)
        self.msg = msg
        self.fields = fields


class Files(object):
    """Everything the module does to the host, so `reconcile` can be driven against a tmp dir."""

    def read_lines(self, path):
        """The record's non-blank lines, or None when there is no record."""
        try:
            with open(path, "rb") as handle:
                text = handle.read().decode("utf-8")
        except IOError as exc:
            if exc.errno == errno.ENOENT:
                return None
            raise
        return [line.strip() for line in text.splitlines() if line.strip()]

    def kind(self, path):
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

    def unlink(self, path):
        """Remove one file or symlink; a path already gone is not an error."""
        try:
            os.unlink(path)
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise

    def write(self, path, text):
        """Replace the record atomically; a replaced one keeps its mode, a new one is 0644."""
        existing = self.kind(path)
        fd, tmp = tempfile.mkstemp(prefix=".install-manifest.", dir=os.path.dirname(path) or ".")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(text.encode("utf-8"))
            if existing == "file":
                os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
            else:
                os.chmod(tmp, 0o644)
            os.rename(tmp, path)
        finally:
            # Gone after a successful rename; still here after a failure part-way.
            if os.path.lexists(tmp):
                os.unlink(tmp)


def _under(path, root):
    """The segments of `path` below `root`, or None when it does not sit under it."""
    prefix = root.rstrip("/") + "/"
    if not path.startswith(prefix):
        return None
    return path[len(prefix):].split("/")


def check_shape(path, system_dir, unit_dir, config_dir):
    """Why `path` may not be recorded, or None when it is one of the two legal shapes."""
    if not path.startswith("/"):
        return "is not an absolute path"
    for root in (system_dir, unit_dir):
        segments = _under(path, root)
        if segments is not None:
            if len(segments) != 1 or segments[0] in _FORBIDDEN_SEGMENTS:
                return "is not a single path segment directly inside %s" % root
            return None
    segments = _under(path, config_dir)
    if segments is not None:
        if any(segment in _FORBIDDEN_SEGMENTS for segment in segments):
            return "has an empty, '.' or '..' segment below %s" % config_dir
        return None
    return "is outside %s, %s and %s" % (system_dir, unit_dir, config_dir)


def validate(paths, files, system_dir, unit_dir, config_dir, what):
    """Refuse the whole list if any path is the wrong shape or names something not a file."""
    refused = []
    for path in paths:
        why = check_shape(path, system_dir, unit_dir, config_dir)
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


def _unit_for(name, suffixes):
    """The unit `name` implies, or None when this module does not map it."""
    for suffix, unit_suffix in suffixes.items():
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)] + unit_suffix
    return None


def units_of(paths, system_dir, unit_dir):
    """The systemd units a set of recorded paths implies, sorted and deduplicated."""
    units = set()
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


def _text(paths):
    return "".join(path + "\n" for path in paths)


def reconcile(files, path, installed, system_dir, unit_dir, config_dir, state, check_mode):
    """Prune, record or remove, and describe what was done, in the module's return shape."""
    recorded_before = files.read_lines(path)
    had_record = recorded_before is not None
    recorded_before = sorted(set(recorded_before or []))
    validate(recorded_before, files, system_dir, unit_dir, config_dir, "the record %s" % path)

    if state == "present":
        recorded_after = sorted(set(str(entry) for entry in installed))
        validate(recorded_after, files, system_dir, unit_dir, config_dir, "'installed'")
    else:
        recorded_after = []

    keep = set(recorded_after)
    pruned = [entry for entry in recorded_before if entry not in keep]
    record_changes = recorded_after != recorded_before or (state == "absent" and had_record)

    if not check_mode:
        # Removals first and the record last: a failure between the two leaves the older,
        # wider record in place, so nothing is forgotten.
        for entry in pruned:
            files.unlink(entry)
        if state == "present":
            if record_changes:
                files.write(path, _text(recorded_after))
        elif had_record:
            files.unlink(path)

    config_prefix = config_dir.rstrip("/") + "/"
    return {
        "changed": bool(pruned) or record_changes,
        "pruned": pruned,
        "recorded": recorded_after,
        "config_changed": any(entry.startswith(config_prefix) for entry in pruned),
        "units": units_of(recorded_before, system_dir, unit_dir),
        "diff": {"before": _text(recorded_before), "after": _text(recorded_after)},
    }


def main():
    module = AnsibleModule(
        argument_spec=dict(
            path=dict(type="path", required=True),
            installed=dict(type="list", elements="str", default=[]),
            system_dir=dict(type="path", required=True),
            unit_dir=dict(type="path", required=True),
            config_dir=dict(type="path", required=True),
            state=dict(type="str", choices=["present", "absent"], default="present"),
        ),
        add_file_common_args=True,
        supports_check_mode=True,
    )
    params = module.params

    try:
        result = reconcile(
            Files(), params["path"], params["installed"], params["system_dir"],
            params["unit_dir"], params["config_dir"], params["state"], module.check_mode,
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
