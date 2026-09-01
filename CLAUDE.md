# binarycodes.homelab

An Ansible collection: the `systemd_app` role deploys Podman Quadlet apps and systemd
units, with an optional Caddy route, install-manifest reconciliation and SOPS-encrypted
podman secrets.

## Licensing

The collection is **GPL-3.0-or-later**. `LICENSE` is the verbatim GPLv3 text and is not
edited.

Every source file carries this header — `.py`, `.yml`, and `.j2`:

```
# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
```

**Add it to every new source file.** Placement rules, which are not cosmetic:

- **YAML** — after the `---` document marker, then a blank line before the file's own
  comments, so the licence block and the file's explanation stay distinct.
- **Python** — at the very top, before the module docstring. Comments precede the
  docstring without displacing it.
- **Jinja templates** — as a `{# ... #}` block with **no blank line after it**. Ansible
  renders templates with `trim_blocks=True`, which eats the newline after `#}`; an extra
  blank line becomes a leading newline in the rendered Quadlet, Caddy snippet or manifest.
- **`Dockerfile.j2`** — plain `#` lines, which pass through Jinja into the built Dockerfile
  as comments.

Molecule fixture data under `extensions/molecule/default/apps/` carries no header: those
files are copied verbatim onto a test host. Nor does
`extensions/molecule/default/sops/age-key.txt`, which is a key rather than source — a
throwaway age identity, committed on purpose and explained in the file itself.

## Gates

All four must pass before a change lands. They are what CI runs.

```sh
pytest tests/unit -q                  # the filter plugins, as plain Python
ansible-lint                          # profile: production, no rules skipped
ansible-test sanity --local           # needs the collection at ansible_collections/binarycodes/homelab/
ansible-galaxy collection build       # catches metadata Galaxy would refuse
molecule test                         # the role on a systemd container; same layout, its own workflow
```

`ansible-test sanity` and `molecule test` both only run when the checkout sits at
`ansible_collections/binarycodes/homelab/`: sanity imports plugins through that path, and
the molecule converge calls the role by FQCN with nothing installing the collection for it.

Locally these run against whatever ansible-core is installed. CI runs the first three, plus
a syntax check of a play that uses the role, once per supported ansible-core — the floor
`meta/runtime.yml` declares and the current release
(`.github/workflows/supported-versions.yml`). Raising the floor means changing
`meta/runtime.yml`, `roles/systemd_app/meta/main.yml` and that matrix together.

## Releasing

A pushed `vX.Y.Z` tag runs `.github/workflows/release.yml`: it checks the tag against
`galaxy.yml`'s `version`, that `CHANGELOG.md` has a `## X.Y.Z` section and that the version
is not already on Galaxy, then calls the three gate workflows against the tagged commit and
publishes. The publish waits behind the `release` environment, which needs a required
reviewer configured to be a real stop — a Galaxy version cannot be replaced or deleted.

So a release is: bump `version` in `galaxy.yml`, write the `CHANGELOG.md` section, commit,
tag, push the tag. Needs the `GALAXY_API_KEY` secret. Prerelease tags (`v1.0.0-rc1`) do not
match the trigger.

## Conventions

- **Computation goes in `plugins/filter/`, with pytest cases** — not Jinja chains in YAML.
  The six filters are public API, named for what they compute.
- **One filter per file, named after the filter**, carrying its own `DOCUMENTATION`,
  `RETURN` and `EXAMPLES`. `ansible-doc` addresses a filter by name, so a file holding two
  could only document one of them. Rationale that spans filters is repeated in each one's
  docs rather than kept in a shared module docstring, where `ansible-doc` cannot reach it.
  `tests/unit/test_filter_docs.py` enforces all of this — nothing in `ansible-lint` or
  `ansible-test sanity` checks filter docs, so an undocumented filter would ship silently.
- **Comments explain why, not what.** The existing ones are the standard to match.
- **Every role variable is prefixed `systemd_app_`** (ansible-lint's
  `var-naming[no-role-prefix]`); see `roles/systemd_app/meta/argument_specs.yml`.
- **Outstanding known issues live in `docs/fixme.md`**, a burn-down list: delete each item
  as it is fixed, and delete the file once it is empty.
