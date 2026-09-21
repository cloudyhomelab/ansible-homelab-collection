# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The decrypt helper, installed to the host as ``/usr/local/libexec/homelab-decrypt-private``.

Run by ``homelab-private-decrypt@<app>.service`` at unit start, never by Ansible and never on
the controller: it turns the encrypted tree the role copied to ``/var/app/<app>/private`` into
plaintext under the unit's ``RuntimeDirectory``, which is tmpfs and goes when the unit stops.

The top level defines functions and nothing else, so importing this module runs nothing. The
three pure functions below are duplicated verbatim in ``plugins/filter/private_tree.py``, which
applies them on the controller so a name collision fails the play rather than this unit; one
table of cases in ``tests/unit/conftest.py`` runs against both copies, so a rule changed in one
and not the other fails.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterable, Sequence

# --- shared with plugins/filter/private_tree.py; keep the two identical -----------------

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


# --- the rest runs on the host only -----------------------------------------------------


class DecryptError(Exception):
    """A failure to report on stderr and exit non-zero for, rather than a traceback."""


class Runner:
    """Every external command the helper runs, so the tests drive it against a fake."""

    def run(self, argv: Sequence[str], env: dict[str, str]) -> bytes:
        """The command's stdout, or DecryptError naming what failed and why."""
        try:
            proc = subprocess.Popen(
                list(argv), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
            )
        except OSError as exc:
            raise DecryptError(
                "cannot run %s: %s. Install it on this host; the private files of an app "
                "cannot be decrypted without it." % (argv[0], exc)
            )
        out, err = proc.communicate()
        if proc.returncode != 0:
            raise DecryptError(
                "%s exited %d: %s" % (argv[0], proc.returncode, err.decode("utf-8", "replace").strip())
            )
        return out


def sources(src_dir: str) -> list[str]:
    """Every file under private/, as paths relative to it, sorted. Hidden ones included."""
    found: list[str] = []
    for root, _dirs, names in os.walk(src_dir):
        for name in names:
            path = os.path.join(root, name)
            # os.walk lists a symlink to a file among the files and one to a directory among
            # the dirs; only the former is something to decrypt.
            if os.path.isfile(path):
                found.append(os.path.relpath(path, src_dir))
    return sorted(found)


def write(path: str, content: bytes) -> None:
    """Create `path` readable only by its owner, then fill it - never the other way round."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(content)


def decrypt(src_dir: str, dst_dir: str, key_file: str, runner: Runner) -> list[str]:
    """Fill `dst_dir` from `src_dir`, and clean up after itself if anything fails."""
    relpaths = sources(src_dir)
    clashes = collisions(relpaths)
    if clashes:
        raise DecryptError(
            "; ".join(clashes) + ". The role refuses this at deploy time, so %s has been "
            "changed since it was installed." % src_dir
        )

    # sops takes the identity from the environment; age is told on the command line. Both
    # inherit PATH, and nothing else: a oneshot unit's environment is not a place to leak from.
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "SOPS_AGE_KEY_FILE": key_file}

    written: list[str] = []
    try:
        for relpath in relpaths:
            tool, out = decrypted_path(relpath)
            src = os.path.join(src_dir, relpath)
            dst = os.path.join(dst_dir, out)
            parent = os.path.dirname(dst)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, mode=0o700)
            written.append(dst)
            if tool == "age":
                write(dst, runner.run(["age", "--decrypt", "-i", key_file, src], env))
            elif tool == "sops":
                write(dst, runner.run(["sops", "--decrypt", src], env))
            else:
                with open(src, "rb") as handle:
                    write(dst, handle.read())
    except Exception:
        # A half-filled private directory is worse than an empty one: the unit failing is
        # what stops the app starting against files that are not all there.
        for path in written:
            try:
                os.unlink(path)
            except OSError:
                pass
        raise
    return relpaths


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        sys.stderr.write("usage: homelab-decrypt-private <app>\n")
        return 2
    app = args[0]

    src_dir = os.environ.get("SYSTEMD_APP_PRIVATE_SRC") or "/var/app/%s/private" % app
    # systemd sets both. RUNTIME_DIRECTORY is a colon-separated list; the unit declares one.
    runtime = os.environ.get("RUNTIME_DIRECTORY", "").split(":")[0]
    credentials = os.environ.get("CREDENTIALS_DIRECTORY", "")

    try:
        if not runtime:
            raise DecryptError("RUNTIME_DIRECTORY is unset; run this from its systemd unit.")
        if not credentials:
            raise DecryptError("CREDENTIALS_DIRECTORY is unset; run this from its systemd unit.")
        key_file = os.path.join(credentials, "age-key")
        if not os.path.isfile(key_file):
            raise DecryptError("no age identity at %s; the unit's LoadCredential= found none." % key_file)
        if not os.path.isdir(src_dir):
            raise DecryptError("%s does not exist, so app %s ships no private files." % (src_dir, app))
        decrypt(src_dir, runtime, key_file, Runner())
    except DecryptError as exc:
        sys.stderr.write("homelab-decrypt-private: %s\n" % exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
