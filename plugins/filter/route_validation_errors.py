# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``route_validation_errors`` filter. Runs on the controller; touches no managed host."""

from __future__ import annotations

import re
from collections.abc import Callable


DOCUMENTATION = r"""
name: route_validation_errors
short_description: Why an app cannot be routed, one string per problem
version_added: 1.1.0
author:
  - binarycodes (@binarycodes)
description:
  - Checks a domain, upstream and port before they are written into a Caddy site block.
  - Guards interpolation. These go into a config file, not a module, so a stray character
    fails nothing at write time; and the Caddyfile imports every app's snippet, so one brace,
    comment character or newline stops Caddy loading all of them.
  - Never raises; one string per problem, so a typo is fixed in one pass.
  - Called C(route_problems) from 1.0.0; that name redirects here and is removed in 2.0.0.
  - C(re.fullmatch), not a C($) anchor, which would let V(example.com\n) through.
  - At least two labels, optionally wildcarded - the site asks a CA for its own certificate
    and none issues one for a single label. Lengths checked too, 253 overall and 63 per label.
positional: upstream, port
options:
  _input:
    description: The public hostname, optionally wildcarded as V(*.example.com).
    type: str
    required: true
  upstream:
    description:
      - The upstream container name, as resolved on the shared network. Accepts what both
        C(podman secret create) and C(ContainerName=) accept.
    type: str
  port:
    description:
      - The internal upstream port. Must be a whole number in the range 1-65535, given as an
        integer or a string of ASCII digits.
      - A boolean, a float and a string with surrounding whitespace are all refused. Python's
        C(int()) would otherwise read V(true) as port 1, truncate V(8080.9) to V(8080) and
        accept V("8080\n") - and that last one composes a broken Caddy site block.
    type: int
"""

RETURN = r"""
_value:
  description: One human-readable string per problem. Empty when the app can be routed.
  type: list
  elements: str
"""

EXAMPLES = r"""
- name: Refuse a call site that would break every imported route
  ansible.builtin.assert:
    that:
      - app_domain | binarycodes.homelab.route_validation_errors(app_upstream, app_port) | length == 0
    # 'example' produces: ["systemd_app_domain 'example' is not a hostname of at least two labels, ..."]
"""


# A podman secret name, and equally a container name: what `podman secret create` and
# `ContainerName=` both accept.
_PODMAN_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# A DNS hostname of at least two labels, optionally wildcarded.
_HOSTNAME_RE = re.compile(
    r"(\*\.)?"                                       # optional wildcard label
    r"([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?\.)+"   # one or more dotted labels
    r"[A-Za-z]([A-Za-z0-9-]*[A-Za-z0-9])?"           # final label, starting with a letter
)
_HOSTNAME_MAX = 253
# DNS limits: 253 for the whole name, 63 for any one label. A name over either is not
# resolvable, so no certificate could be issued for the site it composes.
_LABEL_MAX = 63

# A port given as a string. ASCII digits only, so no sign, no surrounding whitespace, and
# none of the non-ASCII digits `str.isdigit` would accept.
_PORT_RE = re.compile(r"[0-9]+")


def _port_number(port: object) -> int | None:
    """The port as an int, or None when the value is not one.

    Not `int()`: bool is a subclass of int, so True would pass as port 1; a float would be
    truncated rather than refused; and a string would be accepted with surrounding
    whitespace, so "8080\\n" would compose a Caddy site block with a newline in it.
    """
    if isinstance(port, bool):
        return None
    if isinstance(port, int):
        return port
    if isinstance(port, str) and _PORT_RE.fullmatch(port):
        return int(port)
    return None


def route_validation_errors(domain: object, upstream: object = None,
                            port: object = None) -> list[str]:
    """Why this app cannot be routed, one string per problem; empty means it can."""
    problems: list[str] = []

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
    elif any(len(label) > _LABEL_MAX for label in domain.split(".")):
        problems.append(
            f"systemd_app_domain {domain!r} has a label over the {_LABEL_MAX}-character "
            "maximum a DNS label allows"
        )

    upstream = "" if upstream is None else str(upstream)
    if not _PODMAN_NAME_RE.fullmatch(upstream):
        problems.append(
            f"systemd_app_upstream {upstream!r} is not a container name (letters, digits, "
            "dot, dash or underscore, not starting with a dot)"
        )

    port_number = _port_number(port)
    if port_number is None or not 1 <= port_number <= 65535:
        problems.append(f"systemd_app_port {port!r} is not a port in 1-65535")

    return problems


class FilterModule:
    """Input checks for an app's Caddy route."""

    def filters(self) -> dict[str, Callable[..., object]]:
        return {"route_validation_errors": route_validation_errors}
