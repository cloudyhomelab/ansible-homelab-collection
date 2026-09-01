# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``route_problems`` filter. Runs on the controller; touches no managed host."""

from __future__ import annotations

import re


DOCUMENTATION = r"""
name: route_problems
short_description: Why an app cannot be routed, one string per problem
version_added: 1.0.0
author:
  - binarycodes (@binarycodes)
description:
  - Checks a domain, upstream and port before they are written into a Caddy site block.
  - What this guards is interpolation. The three values are written into a config file rather
    than passed to a module, so a stray character does not fail the task that writes them.
    The Caddyfile imports every app's snippet, so a value carrying a brace, a comment
    character or a newline does not merely break this route - it stops Caddy loading any of
    them, taking every other app's route down with one bad domain.
  - Never raises, and returns one string per problem rather than stopping at the first, so a
    typo at a call site is reported in full and fixed in one pass.
  - Matching uses C(re.fullmatch) rather than a C($)-anchored C(re.match), because C($) also
    matches just before a trailing newline - a hostname of V(example.com\n) would pass a C($)
    pattern and then break the site block it composes.
  - The domain must be a hostname of at least two labels, optionally wildcarded. Two labels
    because the value becomes a site that will try to get a public certificate for itself,
    and no CA issues one for a single label - so a bare name is a typo, caught here rather
    than in a certificate loop.
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
    description: The internal upstream port. Must be in the range 1-65535.
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
      - app_domain | binarycodes.homelab.route_problems(app_upstream, app_port) | length == 0
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


def route_problems(domain, upstream=None, port=None):
    """Why this app cannot be routed, one string per problem; empty means it can."""
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


class FilterModule:
    """Input checks for an app's Caddy route."""

    def filters(self):
        return {"route_problems": route_problems}
