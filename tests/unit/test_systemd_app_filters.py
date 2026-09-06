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
import yaml
from ansible.errors import AnsibleFilterError

# Registered by conftest.py, which loads them from plugins/filter/ by path -- one module
# per filter, each named systemd_app_<filter name>.
from systemd_app_app_validation_errors import app_validation_errors
from systemd_app_container_validation_errors import container_validation_errors
from systemd_app_manifest_units import manifest_units
from systemd_app_reconcile_secrets import reconcile_secrets
from systemd_app_route_validation_errors import route_validation_errors
from systemd_app_secret_digests import secret_digests
from systemd_app_source_tree import source_tree
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


# --- app_validation_errors ----------------------------------------------------------------------

def errors(name="myapp", **kwargs):
    kwargs.setdefault("kind", "inline")
    kwargs.setdefault("image", "docker.io/org/app:latest")
    return app_validation_errors(name, **kwargs)


@pytest.fixture(scope="module")
def apps_dir(tmp_path_factory):
    """An apps directory holding 'myapp', for the cases about a source app that exists."""
    root = tmp_path_factory.mktemp("apps")
    (root / "myapp").mkdir()
    return str(root)


def test_a_trailing_newline_in_the_name_is_refused():
    # The case the YAML `is match('^...$')` assert this filter replaced let through: `$`
    # matches before a trailing newline, and nothing the name is written into fails on one.
    assert errors("myapp\n") != []
    assert errors("myapp") == []


@pytest.mark.parametrize("name", ["myapp", "my.app", "my-app_2", "0app", "a"])
def test_good_names_are_accepted(name):
    assert errors(name) == []


@pytest.mark.parametrize("name", ["", None, ".app", "..", "my/app", "my app", "app\x00", "-app"])
def test_bad_names_are_rejected_and_named(name):
    got = errors(name)
    assert len(got) == 1 and got[0].startswith("systemd_app_name")


@pytest.mark.parametrize("kind", ["", None, "Inline", "container"])
def test_bad_kinds_are_rejected(kind):
    assert any(p.startswith("systemd_app_kind") for p in errors(kind=kind))


@pytest.mark.parametrize("state", ["", None, "removed"])
def test_bad_states_are_rejected(state):
    assert any(p.startswith("systemd_app_state") for p in errors(state=state))


@pytest.mark.parametrize("image", ["", None])
def test_an_inline_app_being_deployed_needs_an_image(image):
    assert any(p.startswith("systemd_app_image") for p in errors(image=image))


@pytest.mark.parametrize("image", ["", None])
def test_an_inline_app_being_decommissioned_needs_no_image(image):
    assert errors(state="absent", image=image) == []


def test_a_source_app_needs_no_image(apps_dir):
    assert errors(kind="source", image="", apps_dir=apps_dir) == []


@pytest.mark.parametrize("apps_dir", ["", None])
def test_a_source_app_needs_the_apps_directory(apps_dir):
    got = errors(kind="source", apps_dir=apps_dir)
    assert any(p.startswith("systemd_app_apps_dir") for p in got)
    # Either state: a decommission still looks there for the app's secrets file.
    assert errors(kind="source", state="absent", apps_dir=apps_dir) != []


def test_an_inline_app_needs_no_apps_directory():
    assert errors(apps_dir="") == []


def test_a_source_app_being_deployed_needs_its_directory(apps_dir):
    assert errors(kind="source", apps_dir=apps_dir) == []
    got = errors(name="other", kind="source", apps_dir=apps_dir)
    assert len(got) == 1 and "has no directory at" in got[0] and f"{apps_dir}/other" in got[0]


def test_a_file_where_the_app_directory_should_be_is_not_a_directory(tmp_path):
    (tmp_path / "myapp").write_text("")
    assert any("has no directory at" in p for p in errors(kind="source", apps_dir=str(tmp_path)))


def test_a_decommission_needs_no_source_directory(tmp_path):
    # It works from the manifest on the host, and has to once the tree is gone.
    assert errors(kind="source", state="absent", apps_dir=str(tmp_path)) == []


def test_a_bad_name_is_not_also_reported_as_a_missing_directory(tmp_path):
    got = errors(name="my/app", kind="source", apps_dir=str(tmp_path))
    assert len(got) == 1 and got[0].startswith("systemd_app_name")


@pytest.mark.parametrize("path", ["data", "data/db", "a.b", "..hidden"])
def test_relative_data_directories_are_accepted(path):
    assert errors(data_dirs=[{"path": path, "owner": "10001"}]) == []


@pytest.mark.parametrize(
    "entry",
    [
        {"path": "/var/app/other/data"},
        {"path": "../other/data"},
        {"path": "data/../../other"},
        {"path": "./data"},
        {"path": "data//db"},
        {"path": "data/"},
        {"path": ""},
        {"path": None},
        {"path": 3},
        {"owner": "10001"},
        "data",
    ],
)
def test_data_directories_that_could_leave_the_home_are_rejected(entry):
    got = errors(data_dirs=[entry])
    assert len(got) == 1 and got[0].startswith("systemd_app_data_dirs")


@pytest.mark.parametrize("secret", ["myapp-token", "a", "myapp-oidc-client-secret", "x1-2"])
def test_secret_names_an_inline_app_can_reference_are_accepted(secret, apps_dir):
    assert errors(secret_names=[secret]) == []
    assert errors(kind="source", apps_dir=apps_dir, secret_names=[secret]) == []


@pytest.mark.parametrize("secret", ["my.app.token", "my_app_token", "1token"])
def test_a_source_app_may_use_any_podman_secret_name(secret, apps_dir):
    assert errors(kind="source", apps_dir=apps_dir, secret_names=[secret]) == []


@pytest.mark.parametrize("secret", ["my.app.token", "my_app_token", "1token", "token-\n"])
def test_an_inline_app_needs_names_that_spell_a_variable(secret):
    got = errors(secret_names=[secret])
    assert len(got) == 1 and got[0].startswith("secrets.sops.yaml key")


@pytest.mark.parametrize("secret", ["", ".token", "my token", "my/token", "token\n"])
def test_names_podman_would_refuse_are_refused_for_either_kind(secret, apps_dir):
    for kwargs in ({}, {"kind": "source", "apps_dir": apps_dir}):
        got = errors(secret_names=[secret], **kwargs)
        assert len(got) == 1 and "podman secret name" in got[0]


def test_no_secret_value_appears_in_an_app_problem():
    # Values never reach the filter, by construction: the call site passes the keys alone.
    assert "value" not in inspect.signature(app_validation_errors).parameters


def test_every_app_problem_is_reported_at_once():
    got = app_validation_errors("bad name", kind="what", state="gone", data_dirs=[{"path": "/x"}])
    assert len(got) == 4


def test_the_role_defaults_are_no_problem():
    assert app_validation_errors("myapp", kind="inline", state="present", image="img", apps_dir="", data_dirs=[]) == []
    assert app_validation_errors("myapp", kind="source", state="absent", image="", apps_dir="/srv/apps", data_dirs=[]) == []


# --- route_validation_errors --------------------------------------------------------------------

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
    assert route_validation_errors(domain, "myapp", 8080) == []


def test_a_label_at_exactly_the_dns_maximum_is_accepted():
    # 63 is legal; the check must not be off by one.
    assert route_validation_errors("a" * 63 + ".example.com", "myapp", 8080) == []


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
    problems = route_validation_errors(domain, "myapp", 8080)
    assert any("systemd_app_domain" in p for p in problems), why


@pytest.mark.parametrize("upstream", ["myapp", "my.app", "a_b", "x-1.2_3"])
def test_good_upstreams_are_accepted(upstream):
    assert route_validation_errors("x.example.com", upstream, 8080) == []


@pytest.mark.parametrize("upstream", ["my app", "-myapp", ".myapp", "myapp/x", "", None])
def test_bad_upstreams_are_rejected(upstream):
    problems = route_validation_errors("x.example.com", upstream, 8080)
    assert any("systemd_app_upstream" in p for p in problems)


@pytest.mark.parametrize("port", [1, 80, 8080, 65535, "8080"])
def test_good_ports_are_accepted(port):
    assert route_validation_errors("x.example.com", "myapp", port) == []


@pytest.mark.parametrize("port", [0, -1, 65536, 70000, "http", "", None, "80 80"])
def test_bad_ports_are_rejected(port):
    problems = route_validation_errors("x.example.com", "myapp", port)
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
    problems = route_validation_errors("x.example.com", "myapp", port)
    assert any("systemd_app_port" in p for p in problems), why


@pytest.mark.parametrize("port", ["8080", "1", "65535"])
def test_a_port_given_as_a_string_of_digits_is_accepted(port):
    # YAML and a call site both hand ports over as strings often enough to allow it.
    assert route_validation_errors("x.example.com", "myapp", port) == []


def test_every_problem_is_reported_at_once():
    # One run should tell the caller everything wrong, not just the first thing.
    assert len(route_validation_errors("bad {", "-bad", 0)) == 3


# --- container_validation_errors ----------------------------------------------------------------

@pytest.mark.parametrize(
    "value",
    ["native", "-Xmx512m -Xms256m", "100%", 'say "hi"', r"C:\path", "a=b=c", "", 8080, True],
)
def test_values_the_template_can_escape_are_accepted(value):
    assert container_validation_errors({"KEY": value}) == []


@pytest.mark.parametrize("key", ["FOO", "FOO_BAR", "_FOO", "F1", "a"])
def test_good_env_keys_are_accepted(key):
    assert container_validation_errors({key: "x"}) == []


@pytest.mark.parametrize("key", ["FOO-BAR", "1FOO", "FOO BAR", "FOO.BAR", "", "FOO="])
def test_bad_env_keys_are_rejected(key):
    problems = container_validation_errors({key: "x"})
    assert any("not a legal variable name" in p for p in problems)


@pytest.mark.parametrize("value", ["a\nExecStartPre=/bin/x", "a\tb", "a\x00b", "a\x7f"])
def test_control_characters_in_values_are_rejected(value):
    problems = container_validation_errors({"FOO": value})
    assert any("FOO" in p and "control character" in p for p in problems)


def test_no_secret_value_appears_in_a_problem_message():
    problems = container_validation_errors({"TOKEN": "s3cret-\nvalue"})
    assert problems
    assert not any("s3cret" in p for p in problems)


def test_description_control_character_is_rejected():
    problems = container_validation_errors({}, "app\nExecStopPost=/bin/x")
    assert any("systemd_app_description" in p for p in problems)


def test_clean_description_is_accepted():
    assert container_validation_errors({}, "Myapp web app") == []


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
    problems = container_validation_errors({}, "d", **kwargs)
    assert any(p.startswith(param) for p in problems)


def test_clean_raw_entries_are_accepted():
    assert container_validation_errors(
        {}, "d",
        volumes=["/var/app/x/data:/app/data", "certs.volume:/data"],
        publish_ports=["443:443", "443:443/udp"],
        container_options=["HealthCmd=wget -qO /dev/null http://127.0.0.1:8080/"],
        service_options=["TimeoutStopSec=30"],
    ) == []


def test_nothing_configured_is_no_problem():
    assert container_validation_errors(None) == []
    assert container_validation_errors({}) == []


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
    problems = container_validation_errors({}, **kwargs)
    assert problems == [f"{param} holds a control character"]


def test_health_values_are_not_checked_without_a_probe():
    """No health command means no health block, so nothing to break — and nothing to fix."""
    assert container_validation_errors(
        {},
        health_interval="15s\nUser=0",
        health_retries="3\nUser=0",
        health_start_period="60s\nUser=0",
        start_timeout="180\nUser=0",
    ) == []


def test_the_role_defaults_of_every_rendered_scalar_are_accepted():
    assert container_validation_errors(
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
        container_validation_errors).parameters} | {
        # Also a filename and a container name, so it is checked by the role's name rule
        # before any of this runs.
        "systemd_app_name",
        # Their own filter, called by the template itself.
        "systemd_app_env",
        # Only the names reach the unit, and app_validation_errors checks those, in main.yml.
        "systemd_app_secret_values",
    }
    assert interpolated - checked == set()


# --- manifest_units --------------------------------------------------------------------

SYSTEM_DIR = "/etc/containers/systemd"
UNIT_DIR = "/etc/systemd/system"


def units(paths):
    return manifest_units(paths, SYSTEM_DIR, UNIT_DIR)


@pytest.mark.parametrize(
    "name, unit",
    [
        ("app.container", "app.service"),
        ("app.kube", "app.service"),
        ("app.pod", "app-pod.service"),
    ],
)
def test_quadlet_files_map_to_the_unit_its_generator_makes(name, unit):
    assert units([f"{SYSTEM_DIR}/{name}"]) == [unit]


@pytest.mark.parametrize("name", ["app.volume", "app.network", "app.image", "app.build"])
def test_quadlet_kinds_that_run_nothing_are_left_alone(name):
    """Their resources outlive the app by design, so naming their units would mislead."""
    assert units([f"{SYSTEM_DIR}/{name}"]) == []


@pytest.mark.parametrize(
    "name",
    ["app.service", "app.socket", "app.timer", "app.path", "app.mount", "app.automount"],
)
def test_plain_unit_files_are_their_own_unit(name):
    assert units([f"{UNIT_DIR}/{name}"]) == [name]


@pytest.mark.parametrize("name", ["app.target", "app.slice", "app.scope", "app.conf", "app"])
def test_plain_files_that_are_not_a_stoppable_unit_are_ignored(name):
    assert units([f"{UNIT_DIR}/{name}"]) == []


def test_a_config_path_names_no_unit():
    assert units(["/var/app/myapp/config/app.conf", "/var/app/myapp/config/n/deep.conf"]) == []


def test_a_path_outside_both_install_dirs_is_ignored():
    """The manifest is validated where it is read; this filter is only asked what to stop."""
    assert units(["/etc/passwd", "/etc/containers/systemd/nested/app.container"]) == []


def test_a_suffix_with_no_name_before_it_is_not_a_unit():
    assert units([f"{SYSTEM_DIR}/.container", f"{UNIT_DIR}/.service"]) == []


def test_the_result_is_sorted_and_deduplicated():
    # A '.container' and a plain '.service' of the same name compose one unit, not two.
    assert units([
        f"{UNIT_DIR}/zz-extra.service",
        f"{SYSTEM_DIR}/app.container",
        f"{UNIT_DIR}/app.service",
        f"{SYSTEM_DIR}/app.container",
    ]) == ["app.service", "zz-extra.service"]


def test_nothing_recorded_names_no_unit():
    assert units([]) == []
    assert units(None) == []


def test_the_manifest_a_source_app_records():
    """What item 7's orphan actually is: the app's container service, unnamed by the call."""
    assert units([
        f"{SYSTEM_DIR}/molsource.container",
        f"{UNIT_DIR}/molsource-extra.service",
        "/var/app/molsource/config/app.conf",
    ]) == ["molsource-extra.service", "molsource.service"]


def test_the_install_dirs_are_taken_from_the_caller():
    """Both are role variables, so a fleet that moved them must still tear down."""
    assert manifest_units(
        ["/srv/quadlet/app.container", "/srv/units/app-extra.service"],
        "/srv/quadlet",
        "/srv/units",
    ) == ["app-extra.service", "app.service"]


# --- the fleet as it actually stands ---------------------------------------------------

def test_an_ordinary_call_site_validates():
    """What a routed app actually passes. Tightening a rule must not fail this."""
    assert route_validation_errors("app.example.com", "myapp", 8080) == []
    assert route_validation_errors("*.example.com", "myapp", 443) == []
    assert container_validation_errors(
        {"FORWARD_HEADERS_STRATEGY": "native"}, "Myapp web app"
    ) == []
    assert container_validation_errors({}, None) == []


# --- source_tree -----------------------------------------------------------------------

HOME_CONFIG = "/var/app/myapp/config"


def tree(app_dir):
    return source_tree(str(app_dir), SYSTEM_DIR, UNIT_DIR, HOME_CONFIG)


def touch(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_a_full_source_app_is_grouped_as_it_is_installed(tmp_path):
    app = tmp_path / "myapp"
    touch(app / "quadlet" / "myapp.container")
    touch(app / "quadlet" / "myapp.network")
    touch(app / "unit" / "myapp-extra.service")
    touch(app / "config" / "app.conf")
    touch(app / "config" / "nested" / "deep.conf")
    got = tree(app)
    assert got["quadlet_files"] == [str(app / "quadlet" / "myapp.container"), str(app / "quadlet" / "myapp.network")]
    assert got["unit_files"] == [str(app / "unit" / "myapp-extra.service")]
    assert got["config_files"] == [
        {"src": str(app / "config" / "app.conf"), "path": "app.conf"},
        {"src": str(app / "config" / "nested" / "deep.conf"), "path": "nested/deep.conf"},
    ]
    assert got["config_dir"] == str(app / "config")
    assert got["installed"] == [
        f"{SYSTEM_DIR}/myapp.container",
        f"{SYSTEM_DIR}/myapp.network",
        f"{UNIT_DIR}/myapp-extra.service",
        f"{HOME_CONFIG}/app.conf",
        f"{HOME_CONFIG}/nested/deep.conf",
    ]


def test_an_app_shipping_only_a_quadlet_has_no_config_dir(tmp_path):
    app = tmp_path / "net"
    touch(app / "quadlet" / "web.network")
    got = tree(app)
    assert got["unit_files"] == [] and got["config_files"] == []
    assert got["config_dir"] is None
    assert got["installed"] == [f"{SYSTEM_DIR}/web.network"]


def test_an_empty_config_directory_is_still_a_config_dir(tmp_path):
    # The copy runs for it, creating the tree on the host, so the deploy must know it is there.
    app = tmp_path / "myapp"
    (app / "config").mkdir(parents=True)
    got = tree(app)
    assert got["config_dir"] == str(app / "config") and got["config_files"] == []


def test_hidden_files_are_shipped_in_config_but_not_in_the_flat_directories(tmp_path):
    # config/ is copied whole, dotfiles included; quadlet/ and unit/ were globbed with `*`,
    # which skips them, and an editor's swap file in there is not a unit.
    app = tmp_path / "myapp"
    touch(app / "quadlet" / ".myapp.container.swp")
    touch(app / "quadlet" / "myapp.container")
    touch(app / "unit" / ".hidden.service")
    touch(app / "config" / ".env")
    touch(app / "config" / ".hidden" / "deep.conf")
    got = tree(app)
    assert got["quadlet_files"] == [str(app / "quadlet" / "myapp.container")]
    assert got["unit_files"] == []
    assert [e["path"] for e in got["config_files"]] == [".env", ".hidden/deep.conf"]


def test_directories_inside_the_flat_directories_are_not_files(tmp_path):
    app = tmp_path / "myapp"
    (app / "quadlet" / "drop-in.d").mkdir(parents=True)
    touch(app / "quadlet" / "myapp.container")
    assert tree(app)["quadlet_files"] == [str(app / "quadlet" / "myapp.container")]


def test_a_symlink_to_a_file_is_a_file_the_copy_installs(tmp_path):
    # The copy follows it and puts a regular file on the host, so the manifest lists it.
    app = tmp_path / "myapp"
    target = touch(tmp_path / "shared.conf")
    (app / "config").mkdir(parents=True)
    (app / "config" / "linked.conf").symlink_to(target)
    got = tree(app)
    assert [e["path"] for e in got["config_files"]] == ["linked.conf"]
    assert got["installed"] == [f"{HOME_CONFIG}/linked.conf"]


def test_a_symlink_to_a_directory_under_config_is_not_descended(tmp_path):
    app = tmp_path / "myapp"
    touch(tmp_path / "elsewhere" / "x.conf")
    (app / "config").mkdir(parents=True)
    (app / "config" / "linked").symlink_to(tmp_path / "elsewhere")
    assert tree(app)["config_files"] == []


def test_a_missing_app_directory_raises_rather_than_shipping_nothing(tmp_path):
    with pytest.raises(AnsibleFilterError, match="is not a directory"):
        tree(tmp_path / "nowhere")


def test_the_install_dirs_are_composed_from_the_caller(tmp_path):
    app = tmp_path / "myapp"
    touch(app / "quadlet" / "a.container")
    touch(app / "unit" / "b.timer")
    touch(app / "config" / "c.conf")
    got = source_tree(str(app), "/srv/quadlet/", "/srv/units", "/srv/state/myapp/config")
    assert got["installed"] == ["/srv/quadlet/a.container", "/srv/state/myapp/config/c.conf", "/srv/units/b.timer"]


def test_the_molecule_fixture_records_what_the_scenario_expects():
    """The scenario's oracle for the source app's manifest, checked against the fixture tree
    it is written for: the two must agree or one of them is wrong."""
    scenario = pathlib.Path(__file__).resolve().parents[2] / "extensions" / "molecule" / "default"
    verify = yaml.safe_load((scenario / "verify.yml").read_text())
    expected = verify[0]["vars"]["molecule_expected_source_manifest"]
    got = source_tree(str(scenario / "apps" / "molsource"), SYSTEM_DIR, UNIT_DIR, "/var/app/molsource/config")
    assert got["installed"] == sorted(expected)


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
    # container_validation_errors rejects these first; this guards against the two drifting.
    with pytest.raises(AnsibleFilterError):
        systemd_env_lines(env)
