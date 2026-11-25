# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``external_package`` action plugin. Runs on the controller and drives builtin modules on
the managed host; see the module of the same name for its documentation."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from typing import Any, Protocol

from ansible.errors import AnsibleActionFail
from ansible.plugins.action import ActionBase

from ..module_utils.external_package import (
    ARGUMENT_SPEC,
    FORMATS,
    QUERIES,
    installed_versions,
    validation_errors,
)

# 1 is "not installed" to both dpkg-query and rpm.
_QUERY_RCS = (0, 1)

# dnf5 is its own module: executing a module directly bypasses the dnf action plugin that
# picks between the two.
_INSTALLERS = {"apt": "ansible.builtin.apt", "dnf": "ansible.builtin.dnf", "dnf5": "ansible.builtin.dnf5"}


class StepFailed(Exception):
    """A step on the host failed; the message says which and why."""


class Host(Protocol):
    """What the install needs of the managed host. The action plugin is the real one; the unit
    tests pass a fake."""

    def module(self, name: str, args: dict[str, object]) -> dict[str, Any]: ...

    def command(self, argv: list[str]) -> tuple[int, str, str]: ...


def _query(host: Host, package_format: str, name: str) -> list[str]:
    rc, stdout, stderr = host.command(QUERIES[package_format] + [name])
    if rc not in _QUERY_RCS:
        raise StepFailed(f"Reading the installed version of {name} failed (rc {rc}): {stderr.strip()}")
    return installed_versions(stdout)


def _run(host: Host, step: str, name: str, args: dict[str, object]) -> dict[str, Any]:
    result = host.module(name, args)
    if result.get("failed"):
        raise StepFailed(f"{step} failed: {result.get('msg', result)}")
    return result


def install(host: Host, args: Mapping[str, str], pkg_mgr: str, check_mode: bool) -> dict[str, Any]:
    """Install one package file unless it is installed at its version. Raises StepFailed."""
    package_format = FORMATS[pkg_mgr]
    name = args["name"]
    version = args["version"]
    before = _query(host, package_format, name)
    result: dict[str, Any] = {"name": name, "version": version, "previous_versions": before}
    if version in before:
        return {**result, "changed": False}
    if check_mode:
        return {**result, "changed": True, "msg": f"{name} {version} would be installed."}

    # A fresh directory from tempfile, so no other local user can plant a symlink at the path first.
    download_dir = _run(host, f"Creating a download directory for {name}", "ansible.builtin.tempfile",
                        {"state": "directory", "suffix": ".external_package"})["path"]
    try:
        # dnf reads name-version-release from the file name to refuse a downgrade.
        package_file = f"{download_dir}/{name}-{version}.{package_format}"
        _run(host, f"Downloading {name}", "ansible.builtin.get_url",
             {"url": args["url"], "checksum": args["checksum"], "dest": package_file, "mode": "0644"})
        if package_format == "deb":
            installer_args: dict[str, object] = {"deb": package_file, "update_cache": True}
        else:
            installer_args = {"name": package_file, "state": "present", "disable_gpg_check": True}
        _run(host, f"Installing {name}", _INSTALLERS[pkg_mgr], installer_args)
        after = _query(host, package_format, name)
    except StepFailed:
        host.module("ansible.builtin.file", {"path": download_dir, "state": "absent"})
        raise
    _run(host, f"Removing the download directory of {name}", "ansible.builtin.file",
         {"path": download_dir, "state": "absent"})

    if version not in after:
        raise StepFailed(
            f"{name} reports {after} after installing, not {version}. Set version to the one "
            "the package file carries; the module's documentation says how to read it."
        )
    return {**result, "changed": True}


class ActionModule(ActionBase):  # type: ignore[misc]  # ansible-core ships no types

    TRANSFERS_FILES = False

    def run(self, tmp: str | None = None, task_vars: dict[str, Any] | None = None) -> dict[str, Any]:
        self._supports_check_mode = True
        self._supports_async = False
        result: dict[str, Any] = super().run(tmp, task_vars)
        task_vars = task_vars or {}

        _validation, args = self.validate_argument_spec(argument_spec=ARGUMENT_SPEC)
        try:
            pkg_mgr = self._pkg_mgr(task_vars, result)
            if pkg_mgr not in FORMATS:
                raise AnsibleActionFail(
                    f"external_package installs package files with {', '.join(FORMATS)}; this "
                    f"host's package manager is {pkg_mgr}."
                )
            problems = validation_errors(args, FORMATS[pkg_mgr])
            if problems:
                raise AnsibleActionFail(f"external_package cannot install {args['name']!r} as given: "
                                        f"{'; '.join(problems)}.")
            result.update(install(_TaskHost(self, task_vars), args, pkg_mgr, bool(self._task.check_mode)))
        except StepFailed as e:
            result.update(failed=True, msg=str(e))
        finally:
            self._remove_tmp_path(self._connection._shell.tmpdir)
        return result

    def _pkg_mgr(self, task_vars: dict[str, Any], result: dict[str, Any]) -> str:
        """The host's package manager, from its facts or, failing that, gathered."""
        if self._task.delegate_to:
            host_vars = task_vars.get("hostvars", {}).get(self._task.delegate_to, {})
        else:
            host_vars = task_vars
        known = host_vars.get("ansible_facts", {}).get("pkg_mgr")
        if known:
            return str(known)

        facts = self._execute_module(
            module_name="ansible.builtin.setup",
            module_args={"filter": "ansible_pkg_mgr", "gather_subset": "!all"},
            task_vars=task_vars,
        )
        if facts.get("failed"):
            raise AnsibleActionFail(f"Gathering the package manager fact failed: {facts.get('msg')}",
                                    result=facts)
        gathered = str(facts.get("ansible_facts", {}).get("ansible_pkg_mgr", "unknown"))
        # Kept, so a loop gathers once; but only for the host the facts were gathered on.
        if not self._task.delegate_to or self._task.delegate_facts:
            result["ansible_facts"] = {"pkg_mgr": gathered}
        return gathered


class _TaskHost:
    """The managed host, as the task's connection reaches it."""

    def __init__(self, action: ActionModule, task_vars: dict[str, Any]) -> None:
        self._action = action
        self._task_vars = task_vars

    def module(self, name: str, args: dict[str, object]) -> dict[str, Any]:
        result: dict[str, Any] = self._action._execute_module(
            module_name=name, module_args=args, task_vars=self._task_vars)
        return result

    # A raw command over the connection, which runs in check mode too: the installed version is
    # what check mode reports from.
    def command(self, argv: list[str]) -> tuple[int, str, str]:
        command = shlex.join(argv)
        executed = self._action._low_level_execute_command(command, sudoable=True)
        return executed["rc"], executed["stdout"], executed["stderr"]
