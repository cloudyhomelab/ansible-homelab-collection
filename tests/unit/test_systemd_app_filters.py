# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the 'systemd_app' role's filters.

These cases are the reason the logic moved out of Jinja: each one used to need a generated
playbook and an ansible-playbook run to check.
"""

import base64
import hashlib
import inspect
import json
import pathlib
import re

import pytest
from ansible.errors import AnsibleFilterError

# Registered by conftest.py, which loads them from plugins/filter/ by path -- one module
# per filter, each named systemd_app_<filter name>.
from systemd_app_container_problems import container_problems
from systemd_app_reconcile_secrets import reconcile_secrets
from systemd_app_route_problems import route_problems
from systemd_app_secret_digests import secret_digests
from systemd_app_systemd_env_lines import systemd_env_lines


def digest(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def recorded(mapping):
    """A digest record as slurp hands it over."""
    return base64.b64encode(json.dumps(mapping).encode("utf-8")).decode("ascii")


# --- secret_digests --------------------------------------------------------------------

def test_digests_are_sha256_of_the_string_value():
    assert secret_digests({"a": "one"}) == {"a": digest("one")}


def test_digests_are_stable_across_calls():
    values = {"a": "one", "b": "two"}
    assert secret_digests(values) == secret_digests(values)


@pytest.mark.parametrize("value", ["plain", "with spaces", "100%", 8080, True, ""])
def test_digests_accept_any_scalar(value):
    assert secret_digests({"k": value}) == {"k": digest(value)}


def test_digests_of_nothing_is_empty():
    assert secret_digests(None) == {}
    assert secret_digests({}) == {}


# --- reconcile_secrets -----------------------------------------------------------------

VALUES = {"app-a": "one", "app-b": "two"}
DIGESTS = secret_digests(VALUES)
RECORD = recorded(DIGESTS)


@pytest.mark.parametrize(
    "label, digests, record, stored, expect_store, expect_drop",
    [
        ("steady state", DIGESTS, RECORD, ["app-a", "app-b"], [], []),
        # The bug this filter's third input exists for: the record still matches, so
        # nothing would be re-stored, and the app references a name podman has lost.
        ("store cleared", DIGESTS, RECORD, [], ["app-a", "app-b"], []),
        ("one removed by hand", DIGESTS, RECORD, ["app-a"], ["app-b"], []),
        ("rotated value", secret_digests({"app-a": "NEW", "app-b": "two"}), RECORD,
         ["app-a", "app-b"], ["app-a"], []),
        ("name new to the file", secret_digests({**VALUES, "app-c": "three"}), RECORD,
         ["app-a", "app-b"], ["app-c"], []),
        ("name dropped from the file", secret_digests({"app-a": "one"}), RECORD,
         ["app-a", "app-b"], [], ["app-b"]),
        ("first deploy, no record", DIGESTS, "", [], ["app-a", "app-b"], []),
        ("app with no secrets", {}, "", [], [], []),
        ("another app's secret in the store", DIGESTS, RECORD,
         ["app-a", "app-b", "other-x"], [], []),
        ("rotation and drift together", secret_digests({"app-a": "NEW", "app-b": "two"}),
         RECORD, ["app-a"], ["app-a", "app-b"], []),
        ("renamed: old dropped, new stored", secret_digests({"app-a": "one", "app-c": "two"}),
         RECORD, ["app-a", "app-b"], ["app-c"], ["app-b"]),
    ],
)
def test_reconcile(label, digests, record, stored, expect_store, expect_drop):
    got = reconcile_secrets(digests, record, stored)
    assert got["store"] == expect_store, label
    assert got["drop"] == expect_drop, label


def test_remove_is_the_union_of_store_and_drop():
    # A rotation is rm-then-create, and the same pass clears the drops.
    got = reconcile_secrets(
        secret_digests({"app-a": "NEW", "app-c": "three"}), RECORD, ["app-a", "app-b"]
    )
    assert got["store"] == ["app-a", "app-c"]
    assert got["drop"] == ["app-b"]
    assert got["remove"] == ["app-a", "app-b", "app-c"]


def test_reconcile_output_is_sorted():
    digests = secret_digests({"z": "1", "a": "1", "m": "1"})
    assert reconcile_secrets(digests, "", [])["store"] == ["a", "m", "z"]


@pytest.mark.parametrize("record", ["", None])
def test_missing_record_stores_everything(record):
    assert reconcile_secrets(DIGESTS, record, [])["store"] == ["app-a", "app-b"]


def test_empty_record_file_parses_and_stores_everything():
    # An empty record is a readable record of nothing, not a corrupt one: it means this app
    # has stored no secret yet, so every name needs storing even though the store has them.
    got = reconcile_secrets(DIGESTS, recorded({}), ["app-a", "app-b"])
    assert got["store"] == ["app-a", "app-b"]
    assert got["drop"] == []


def test_blank_record_file_parses_as_no_record():
    # A zero-byte file, as a half-written deploy could leave behind.
    assert reconcile_secrets(DIGESTS, base64.b64encode(b"  \n").decode("ascii"))["store"] \
        == ["app-a", "app-b"]


@pytest.mark.parametrize(
    "content",
    [
        base64.b64encode(b"not json at all").decode("ascii"),
        base64.b64encode(b'["a", "list"]').decode("ascii"),
    ],
)
def test_corrupt_record_is_refused_rather_than_ignored(content):
    # Silently reading a corrupt record as empty would re-store every secret and restart
    # the app; a refusal says what to do instead.
    with pytest.raises(AnsibleFilterError):
        reconcile_secrets(DIGESTS, content, [])


# --- route_problems --------------------------------------------------------------------

@pytest.mark.parametrize(
    "domain",
    [
        "app.example.com",
        "a.b.c.example.co.uk",
        "*.example.com",
        "x-y.example.com",
        "e1.example.com",
    ],
)
def test_good_domains_are_accepted(domain):
    assert route_problems(domain, "myapp", 8080) == []


def test_a_label_at_exactly_the_dns_maximum_is_accepted():
    # 63 is legal; the check must not be off by one.
    assert route_problems("a" * 63 + ".example.com", "myapp", 8080) == []


@pytest.mark.parametrize(
    "domain, why",
    [
        ("example.com {", "brace would open a second site block"),
        ("example.com\n", "trailing newline, which a '$' anchor would have allowed"),
        ("example.com\nevil.com {", "a whole extra site block"),
        ("example.com #c", "comment character"),
        ('example.com"', "quote"),
        ("example", "single label cannot get a certificate"),
        ("-bad.example.com", "label starts with a hyphen"),
        ("bad-.example.com", "label ends with a hyphen"),
        ("example..com", "empty label"),
        ("exa mple.com", "space"),
        ("*.*.example.com", "only one wildcard label is allowed"),
        ("", "empty"),
        (None, "undefined"),
        (".".join(["a" * 60] * 5) + ".com", "over 253 characters"),
        ("a" * 64 + ".example.com", "label over the 63-character DNS maximum"),
        ("*." + "a" * 64 + ".example.com", "wildcarded, label still over 63 characters"),
    ],
)
def test_bad_domains_are_rejected(domain, why):
    problems = route_problems(domain, "myapp", 8080)
    assert any("systemd_app_domain" in p for p in problems), why


@pytest.mark.parametrize("upstream", ["myapp", "my.app", "a_b", "x-1.2_3"])
def test_good_upstreams_are_accepted(upstream):
    assert route_problems("x.example.com", upstream, 8080) == []


@pytest.mark.parametrize("upstream", ["my app", "-myapp", ".myapp", "myapp/x", "", None])
def test_bad_upstreams_are_rejected(upstream):
    problems = route_problems("x.example.com", upstream, 8080)
    assert any("systemd_app_upstream" in p for p in problems)


@pytest.mark.parametrize("port", [1, 80, 8080, 65535, "8080"])
def test_good_ports_are_accepted(port):
    assert route_problems("x.example.com", "myapp", port) == []


@pytest.mark.parametrize("port", [0, -1, 65536, 70000, "http", "", None, "80 80"])
def test_bad_ports_are_rejected(port):
    problems = route_problems("x.example.com", "myapp", port)
    assert any("systemd_app_port" in p for p in problems)


@pytest.mark.parametrize(
    "port, why",
    [
        (True, "bool is an int subclass, so int() would read it as port 1"),
        (False, "as above, port 0"),
        (8080.9, "int() would truncate it rather than refuse it"),
        (8080.0, "a port is a whole number, even when the float is integral"),
        ("8080\n", "int() accepts a trailing newline, which would break the site block"),
        (" 8080", "int() accepts leading whitespace"),
        ("8080 ", "int() accepts trailing whitespace"),
        ("+8080", "int() accepts a sign"),
        ("\u0668\u0660\u0668\u0660", "str.isdigit accepts non-ASCII digits; int() converts them"),
    ],
)
def test_ports_int_would_wrongly_accept_are_rejected(port, why):
    # Each of these passes a bare int(), which is why the filter does not use one.
    problems = route_problems("x.example.com", "myapp", port)
    assert any("systemd_app_port" in p for p in problems), why


@pytest.mark.parametrize("port", ["8080", "1", "65535"])
def test_a_port_given_as_a_string_of_digits_is_accepted(port):
    # YAML and a call site both hand ports over as strings often enough to allow it.
    assert route_problems("x.example.com", "myapp", port) == []


def test_every_problem_is_reported_at_once():
    # One run should tell the caller everything wrong, not just the first thing.
    assert len(route_problems("bad {", "-bad", 0)) == 3


# --- container_problems ----------------------------------------------------------------

@pytest.mark.parametrize(
    "value",
    ["native", "-Xmx512m -Xms256m", "100%", 'say "hi"', r"C:\path", "a=b=c", "", 8080, True],
)
def test_values_the_template_can_escape_are_accepted(value):
    assert container_problems({"KEY": value}) == []


@pytest.mark.parametrize("key", ["FOO", "FOO_BAR", "_FOO", "F1", "a"])
def test_good_env_keys_are_accepted(key):
    assert container_problems({key: "x"}) == []


@pytest.mark.parametrize("key", ["FOO-BAR", "1FOO", "FOO BAR", "FOO.BAR", "", "FOO="])
def test_bad_env_keys_are_rejected(key):
    problems = container_problems({key: "x"})
    assert any("not a legal variable name" in p for p in problems)


@pytest.mark.parametrize("value", ["a\nExecStartPre=/bin/x", "a\tb", "a\x00b", "a\x7f"])
def test_control_characters_in_values_are_rejected(value):
    problems = container_problems({"FOO": value})
    assert any("FOO" in p and "control character" in p for p in problems)


def test_no_secret_value_appears_in_a_problem_message():
    problems = container_problems({"TOKEN": "s3cret-\nvalue"})
    assert problems
    assert not any("s3cret" in p for p in problems)


def test_description_control_character_is_rejected():
    problems = container_problems({}, "app\nExecStopPost=/bin/x")
    assert any("systemd_app_description" in p for p in problems)


def test_clean_description_is_accepted():
    assert container_problems({}, "Myapp web app") == []


@pytest.mark.parametrize(
    "kwargs, param",
    [
        ({"volumes": ["/a:/b\nUser=0"]}, "systemd_app_volumes"),
        ({"publish_ports": ["443:443\nUser=0"]}, "systemd_app_publish_ports"),
        ({"container_options": ["Foo=1\nBar=2"]}, "systemd_app_container_options"),
        ({"service_options": ["Foo=1\nBar=2"]}, "systemd_app_service_options"),
    ],
)
def test_multiline_raw_entries_are_rejected_and_named(kwargs, param):
    problems = container_problems({}, "d", **kwargs)
    assert any(p.startswith(param) for p in problems)


def test_clean_raw_entries_are_accepted():
    assert container_problems(
        {}, "d",
        volumes=["/var/app/x/data:/app/data", "certs.volume:/data"],
        publish_ports=["443:443", "443:443/udp"],
        container_options=["HealthCmd=wget -qO /dev/null http://127.0.0.1:8080/"],
        service_options=["TimeoutStopSec=30"],
    ) == []


def test_nothing_configured_is_no_problem():
    assert container_problems(None) == []
    assert container_problems({}) == []


@pytest.mark.parametrize(
    "kwargs, param",
    [
        ({"image": "img:latest\nPodmanArgs=--privileged"}, "systemd_app_image"),
        ({"network": "web.network\nUser=0"}, "systemd_app_network"),
        ({"health_cmd": "true\nNotify=healthy"}, "systemd_app_health_cmd"),
        ({"health_cmd": "true", "health_interval": "15s\nUser=0"},
         "systemd_app_health_interval"),
        ({"health_cmd": "true", "health_retries": "3\nUser=0"},
         "systemd_app_health_retries"),
        ({"health_cmd": "true", "health_start_period": "60s\nUser=0"},
         "systemd_app_health_start_period"),
        ({"health_cmd": "true", "start_timeout": "180\nUser=0"},
         "systemd_app_start_timeout"),
    ],
)
def test_control_characters_in_rendered_scalars_are_rejected(kwargs, param):
    """Each of these is one directive, so a newline in it writes a further directive."""
    problems = container_problems({}, **kwargs)
    assert problems == [f"{param} holds a control character"]


def test_health_values_are_not_checked_without_a_probe():
    """No health command means no health block, so nothing to break — and nothing to fix."""
    assert container_problems(
        {},
        health_interval="15s\nUser=0",
        health_retries="3\nUser=0",
        health_start_period="60s\nUser=0",
        start_timeout="180\nUser=0",
    ) == []


def test_the_role_defaults_of_every_rendered_scalar_are_accepted():
    assert container_problems(
        {},
        image="docker.io/org/app:latest",
        network="web.network",
        health_cmd="wget -qO /dev/null http://127.0.0.1:8080/ || exit 1",
        health_interval="15s",
        health_retries=3,
        health_start_period="60s",
        start_timeout=180,
    ) == []


def test_every_scalar_the_inline_template_interpolates_reaches_the_filter():
    """The filter's contract: a new interpolation in the template without a check here.

    Matches ``{{ systemd_app_* }}`` outside a Jinja statement, which is what lands in the
    unit verbatim. Loop variables and anything the template computes are not interpolated
    call-site input, so they are not the filter's to guard.
    """
    template = (
        pathlib.Path(__file__).resolve().parents[2]
        / "roles" / "systemd_app" / "templates" / "inline.container.j2"
    ).read_text()
    interpolated = set(re.findall(r"\{\{\s*(systemd_app_[a-z_]+)", template))

    checked = {f"systemd_app_{name}" for name in inspect.signature(
        container_problems).parameters} | {
        # Also a filename and a container name, so it is checked by the role's name rule
        # before any of this runs.
        "systemd_app_name",
        # Their own filter, called by the template itself.
        "systemd_app_env",
        # Read from the app's SOPS file after this assert has run, so it cannot be here.
        "systemd_app_secret_values",
    }
    assert interpolated - checked == set()


# --- the fleet as it actually stands ---------------------------------------------------

def test_an_ordinary_call_site_validates():
    """What a routed app actually passes. Tightening a rule must not fail this."""
    assert route_problems("app.example.com", "myapp", 8080) == []
    assert route_problems("*.example.com", "myapp", 443) == []
    assert container_problems(
        {"FORWARD_HEADERS_STRATEGY": "native"}, "Myapp web app"
    ) == []
    assert container_problems({}, None) == []


# --- systemd_env_lines -----------------------------------------------------------------

def test_plain_value_is_quoted():
    assert systemd_env_lines({"A": "native"}) == ['Environment="A=native"']


@pytest.mark.parametrize(
    "value, rendered",
    [
        # A bare value with a space would set A to '-Xmx512m' and read '-Xms256m' as a
        # further assignment; inside quotes it survives whole.
        ("-Xmx512m -Xms256m", 'Environment="A=-Xmx512m -Xms256m"'),
        # A lone '%' opens a systemd specifier.
        ("grow by 100%", 'Environment="A=grow by 100%%"'),
        ('say "hi"', 'Environment="A=say \\"hi\\""'),
        (r"C:\path\to", 'Environment="A=C:\\\\path\\\\to"'),
        ("a=b=c", 'Environment="A=a=b=c"'),
        ("", 'Environment="A="'),
    ],
)
def test_values_are_escaped_for_a_quoted_directive(value, rendered):
    assert systemd_env_lines({"A": value}) == [rendered]


def test_backslash_is_escaped_before_the_escapes_we_add():
    # Wrong order would turn '\"' into '\\"' and break the closing quote.
    assert systemd_env_lines({"A": '\\"'}) == ['Environment="A=\\\\\\""']


@pytest.mark.parametrize("value, rendered", [(8080, "8080"), (True, "True")])
def test_non_string_values_are_stringified(value, rendered):
    assert systemd_env_lines({"A": value}) == [f'Environment="A={rendered}"']


def test_lines_are_sorted_so_reordering_a_call_site_changes_nothing():
    a = systemd_env_lines({"Z": "1", "A": "2", "M": "3"})
    b = systemd_env_lines({"A": "2", "M": "3", "Z": "1"})
    assert a == b == ['Environment="A=2"', 'Environment="M=3"', 'Environment="Z=1"']


def test_nothing_configured_renders_nothing():
    assert systemd_env_lines(None) == []
    assert systemd_env_lines({}) == []


@pytest.mark.parametrize("env", [{"A": "x\nExecStartPre=/bin/x"}, {"A\n": "x"}, {"A": "x\x00"}])
def test_control_characters_are_refused_as_a_backstop(env):
    # container_problems rejects these first; this guards against the two drifting.
    with pytest.raises(AnsibleFilterError):
        systemd_env_lines(env)
