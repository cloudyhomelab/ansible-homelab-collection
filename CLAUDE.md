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

Nor do the changelog's generated files or its fragments. `changelogs/changelog.yaml` and
`CHANGELOG.md` are rewritten by `antsibull-changelog`, which would drop a header on the next
release; a fragment in `changelogs/fragments/` is deleted by that same release, so a
three-line header on a two-line file buys nothing. `changelogs/config.yaml` is hand-written
and carries one.

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

CI additionally lints the changelog and checks that the generated `CHANGELOG.md` still
matches `changelogs/changelog.yaml` (see Releasing); `ansible-test sanity` validates
`changelog.yaml` on its own account.

Locally these run against whatever ansible-core is installed. CI runs the first three, plus
a syntax check of a play that uses the role, once per supported ansible-core — the floor
`meta/runtime.yml` declares and the current release
(`.github/workflows/supported-versions.yml`). Raising the floor means changing
`meta/runtime.yml`, `roles/systemd_app/meta/main.yml` and that matrix together.

`molecule test` runs once per platform family `roles/systemd_app/meta/main.yml` claims
(Fedora and Debian), through `MOLECULE_DISTRO` and `MOLECULE_IMAGE` set by the matrix in
`.github/workflows/molecule.yml`; a local run without them converges Fedora. Claiming a
platform means an entry in `meta/main.yml`, a `Dockerfile.<distro>.j2` in the scenario and a
matrix row, together. The Fedora entry names the release the image is pinned to, so bumping
the image tag means bumping it in `meta/main.yml` and the matrix row as well.

## Releasing

`CHANGELOG.md` is **generated** by `antsibull-changelog` from `changelogs/changelog.yaml`,
which is in turn built from the fragments in `changelogs/fragments/`. A change that a
consumer would notice ships a fragment in the same commit; `changelogs/README.md` covers the
sections and the markup (reStructuredText, even though the output is Markdown). Never edit
`CHANGELOG.md` — CI fails when it stops matching `changelog.yaml`.

A pushed `vX.Y.Z` tag runs `.github/workflows/release.yml`: it checks the tag against
`galaxy.yml`'s `version`, lints the changelog, renders this version's notes out of
`changelog.yaml` (which fails outright on a version it has no entry for), checks the
committed `CHANGELOG.md` matches what that file renders to, and checks the version is not
already on Galaxy — then calls the three gate workflows against the tagged commit and
publishes. The publish waits behind the `release` environment, which needs a required
reviewer configured to be a real stop — a Galaxy version cannot be replaced or deleted.

So a release is, in outline: `antsibull-changelog release --version X.Y.Z`, bump `version`
in `galaxy.yml` to match, commit those together with the regenerated `CHANGELOG.md`, tag,
push the tag. **`RELEASE.md` is the procedure** — prerequisites, the checklist, what each
check catches and what to do when a step fails. Keep the steps there and not here, so the
two cannot drift.

Needs the `GALAXY_API_KEY` secret. Prerelease tags (`v1.0.0-rc1`) do not match the trigger.
The GitHub release body is rendered from `changelog.yaml` by the same command that writes
the changelog, so the two cannot disagree about where a version's notes end.

Running the workflow by hand **from a branch** is a rehearsal: the version comes from
`galaxy.yml`, every check and every gate still runs, the collection is built, and the two
steps that reach outside the runner are skipped. What separates a rehearsal from a release
is `github.ref_type`, not an input, so nothing published can come from a branch.

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
