# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Every name the role registers or sets is either reset per app or exempt for a stated reason.

The role runs once per app in one play, and registers and facts persist for the play, so a
stale one can point one app's delete or restart at another app. main.yml resets them in a
written-out list. This walks the task files and holds that list to the names actually used,
so a new register is a deliberate choice: reset it, or write down here why it need not be.
"""

import yaml

from conftest import ROOT

TASKS = sorted((ROOT / "roles" / "systemd_app" / "tasks").glob("*.yml"))
RESET_TASK = "Reset install path facts"

# Names that are safe without a reset, and why. A reason of the form "the loop yields a fresh
# result" or "the task always runs" is only true until the task gains a `when`, which is the
# point of writing it down where a reviewer sees it change.
EXEMPT = {
    "systemd_app_config_dir": "its stat has no `when`, so every present run refreshes it before the two tasks that read it",
    "systemd_app_stop_result": "read only by its own task's failed_when",
    "systemd_app_enable_result": "read only by its own task's failed_when",
}


def tasks_in(items):
    """Every task in a task list, descending into block/rescue/always."""
    for item in items or []:
        yield item
        for key in ("block", "rescue", "always"):
            yield from tasks_in(item.get(key))


def set_fact_keys(task):
    for key in ("ansible.builtin.set_fact", "set_fact"):
        if key in task:
            return set(task[key])
    return set()


def load(path):
    return list(tasks_in(yaml.safe_load(path.read_text())))


ALL_TASKS = {path.name: load(path) for path in TASKS}


def reset_names():
    task = next(t for t in ALL_TASKS["main.yml"] if t.get("name", "").startswith(RESET_TASK))
    return set_fact_keys(task)


def used_names():
    names = set()
    for tasks in ALL_TASKS.values():
        for task in tasks:
            if task.get("name", "").startswith(RESET_TASK):
                continue
            if "register" in task:
                names.add(task["register"])
            names |= set_fact_keys(task)
    return names


def test_the_reset_task_is_where_the_role_says_it_is():
    assert reset_names(), "the reset task in main.yml was not found or sets nothing"


def test_every_register_and_fact_is_reset_or_deliberately_exempt():
    unaccounted = used_names() - reset_names() - set(EXEMPT)
    assert not unaccounted, (
        f"registered or set but neither reset in main.yml nor exempt here: {sorted(unaccounted)}"
    )


def test_an_exemption_names_something_the_role_still_uses():
    stale = set(EXEMPT) - used_names()
    assert not stale, f"exempt but no longer registered or set anywhere: {sorted(stale)}"


def test_a_name_is_not_both_reset_and_exempt():
    assert not set(EXEMPT) & reset_names()


def test_nothing_reset_is_unused():
    # A reset for a name no task registers is a leftover, and hides a rename.
    assert not reset_names() - used_names()
