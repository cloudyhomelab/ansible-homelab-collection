# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Input checks for the files the systemd_app role generates.

Both filters return one human-readable string per problem and never raise, so a run
reports everything wrong at once rather than the first thing it meets. What they guard is
interpolation: these values are written into a Caddy site block and a Quadlet rather than
passed to a module, so a stray character does not fail the task that writes them -- it
breaks every route the proxy imports, or turns the rest of a unit line into a different
directive.

Matching is done with re.fullmatch rather than a '$'-anchored re.match, because '$' also
matches just before a trailing newline: a hostname of "example.com\\n" would pass a '$'
pattern and then break the Caddy site block it composes. fullmatch has no such edge, so
the patterns below carry no end anchor at all.

Filters run on the controller, so nothing here touches a managed host.
"""

from __future__ import annotations

import re


# A podman secret name, and equally a container name: what `podman secret create` and
# `ContainerName=` both accept.
_PODMAN_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# The left side of an Environment= assignment, so what a shell would accept as a variable.
_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# A DNS hostname of at least two labels, optionally wildcarded. Two labels because the
# value becomes a Caddy site that will try to get a public certificate for itself, and no
# CA issues one for a single label -- so a bare name is a typo, caught here rather than in
# a certificate loop.
_HOSTNAME_RE = re.compile(
    r"(\*\.)?"                                       # optional wildcard label
    r"([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?\.)+"   # one or more dotted labels
    r"[A-Za-z]([A-Za-z0-9-]*[A-Za-z0-9])?"           # final label, starting with a letter
)
_HOSTNAME_MAX = 253

# A control character has no representation in a unit file: a newline ends the line and
# turns whatever follows into a further directive, which quoting cannot rescue. Refused
# here; the same pattern in systemd.py is the backstop at render time.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def route_problems(domain, upstream=None, port=None):
    """Why this app cannot be routed, one string per problem; empty means it can.

    These three compose a Caddy site block, and the Caddyfile imports every app's snippet,
    so a value carrying a brace, a comment character or a newline does not merely break
    this route -- it stops Caddy loading any of them.
    """
    problems = []

    domain = "" if domain is None else str(domain)
    if not _HOSTNAME_RE.fullmatch(domain):
        problems.append(
            f"systemd_app_domain {domain!r} is not a hostname of at least two labels, "
            "optionally wildcarded as '*.example.com'"
        )
    elif len(domain) > _HOSTNAME_MAX:
        problems.append(
            f"systemd_app_domain is {len(domain)} characters, over the {_HOSTNAME_MAX} maximum"
        )

    upstream = "" if upstream is None else str(upstream)
    if not _PODMAN_NAME_RE.fullmatch(upstream):
        problems.append(
            f"systemd_app_upstream {upstream!r} is not a container name (letters, digits, "
            "dot, dash or underscore, not starting with a dot)"
        )

    try:
        port_number = int(port)
    except (TypeError, ValueError):
        port_number = None
    if port_number is None or not 1 <= port_number <= 65535:
        problems.append(f"systemd_app_port {port!r} is not a port in 1-65535")

    return problems


def container_problems(env, description="", volumes=None, publish_ports=None,
                       container_options=None, service_options=None):
    """Why this app's Quadlet cannot be rendered, one string per problem.

    Values are not checked for spaces, quotes or percent signs: the template quotes and
    escapes those, so they are legal input. Only what cannot survive a unit file at all is
    refused.
    """
    problems = []

    for key, value in (env or {}).items():
        if not _ENV_KEY_RE.fullmatch(str(key)):
            problems.append(
                f"systemd_app_env key {str(key)!r} is not a legal variable name (letters, "
                "digits and underscore, no leading digit)"
            )
        # Reported by key, not by value: a failure message is no place for either, and the
        # key is what the caller has to go and fix.
        if isinstance(value, str) and _CONTROL_RE.search(value):
            problems.append(
                f"the value of systemd_app_env key {str(key)!r} holds a control character"
            )

    if description is not None and _CONTROL_RE.search(str(description)):
        problems.append("systemd_app_description holds a control character")

    raw = (
        ("systemd_app_volumes", volumes),
        ("systemd_app_publish_ports", publish_ports),
        ("systemd_app_container_options", container_options),
        ("systemd_app_service_options", service_options),
    )
    for name, lines in raw:
        for line in lines or []:
            if _CONTROL_RE.search(str(line)):
                problems.append(
                    f"{name} entry {str(line)!r} holds a control character; each entry is "
                    "one Quadlet line, so use a further entry rather than a newline"
                )

    return problems


class FilterModule:
    """Filters for the systemd_app role's input checks."""

    def filters(self):
        return {
            "route_problems": route_problems,
            "container_problems": container_problems,
        }
