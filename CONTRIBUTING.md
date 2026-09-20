# Contributing

## Setup

Check out as `ansible_collections/binarycodes/homelab/`; `ansible-test sanity` and
`molecule test` only run from that layout.

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install ansible-core -r requirements-dev.txt
git config core.hooksPath .githooks
```

## Gates

`.github/workflows/checks.yml` runs all of these on every pull request.

```sh
pytest tests/unit -q              # the filter plugins, as plain Python
mypy                              # the plugins' annotations, strict
ansible-lint                      # profile: production, no rules skipped
ansible-test sanity --local
ansible-galaxy collection build   # catches metadata Galaxy would refuse
antsibull-changelog lint
molecule test                     # the role on a systemd container; needs sops on PATH
```

`requirements-dev.txt` pins the tools; `ansible-core` is in neither requirements file, being
the thing under test.

Kept in step by hand:

- Raising the ansible-core floor means `meta/runtime.yml`,
  `roles/systemd_app/meta/main.yml` and `.github/workflows/supported-versions.yml` together.
- Claiming a platform means an entry in `roles/systemd_app/meta/main.yml`, a
  `Dockerfile.<distro>.j2` in the molecule scenario and a row in
  `.github/workflows/molecule.yml` together. Bumping the Fedora image tag means the first
  and the last.

## Commit messages

Enforced by `.githooks/commit-msg`: a
[Conventional Commits](https://www.conventionalcommits.org) subject,
`<type>[(scope)][!]: <description>`, at most 100 characters, one line, no body, no
`Co-Authored-By` trailer.

## Changelog

Every pull request adds a fragment under `changelogs/fragments/`; `trivial:` is the section
for a change nobody needs to read about. The one exception is a pull request that changes
`changelogs/changelog.yaml` itself. `CHANGELOG.md` is generated, never edited. See
[changelogs/README.md](changelogs/README.md).

## Licence header

On every new `.py`, `.yml` and `.j2`:

```
# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
```

After the `---` in YAML, above the module docstring in Python, as a `{# #}` block with no
blank line after it in Jinja (`trim_blocks=True` eats the newline), and as plain `#` lines
in `Dockerfile.j2`.

No header on the molecule fixtures under `extensions/molecule/default/apps/` (copied
verbatim to the test host), on `extensions/molecule/default/sops/age-key.txt`, or on
`CHANGELOG.md`, `changelogs/changelog.yaml` and the fragments, which are generated or
deleted at release. `changelogs/config.yaml` carries one.

## Conventions

- Computation goes in Python with pytest cases, not Jinja chains in YAML: controller-side is
  a filter in `plugins/filter/`, host-side state that is read, reconciled and written back is
  a module in `plugins/modules/`, with every external call behind one runner callable so the
  logic tests against a fake. A custom module only where no builtin does the job.
- One plugin per file, named after the plugin, with its own `DOCUMENTATION`, `RETURN` and
  `EXAMPLES`. `ansible-doc` addresses a plugin by name, so a file holding two could only
  document one; `tests/unit/test_filter_docs.py` enforces it for filters, which nothing else
  checks.
- Filters and modules are public API, named for what they compute.
- Comments explain why, not what.
- Every role variable is prefixed `systemd_app_` (ansible-lint `var-naming[no-role-prefix]`);
  see `roles/systemd_app/meta/argument_specs.yml`.

## Further

[RELEASE.md](RELEASE.md) is how a release is cut.
