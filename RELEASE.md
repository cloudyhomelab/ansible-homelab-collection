# Releasing `binarycodes.homelab`

Everything in a release is reversible except the last step. A version published to Ansible
Galaxy **cannot be replaced and cannot be deleted** — a mistake is not rolled back, it is
superseded, and the wrong artefact stays visible for good. That single fact is why this
procedure is longer than "tag and push", and why the machinery spends most of its effort
refusing to publish.

Read this top to bottom the first time. The checklist in [Doing the release](#doing-the-release)
is the part you come back to.

## Before you start

You need, once:

- **`antsibull-changelog`**, which writes the changelog. Match what CI installs:

  ```sh
  pipx inject ansible-core antsibull-changelog --include-apps
  ```

- **A `GALAXY_API_KEY` secret** on the GitHub repository, from your Galaxy account's
  namespace. Without it the publish step fails with a clear message rather than uploading
  nothing.
- **A `release` environment** configured in the repository's settings **with a required
  reviewer**. This is the stop button between "every gate passed" and the irreversible
  upload. Without the protection rule the job simply runs, so the guard is only as real as
  the environment — check it is still there if it has been a while.

You also need push access for a tag, and the working tree on `main`, clean and up to date.

## Choosing the version number

[Semantic versioning](https://semver.org). The collection's public API is:

- every `systemd_app_*` role variable, as documented in
  `roles/systemd_app/meta/argument_specs.yml`;
- the six filters' names, their arguments, and the shape of what they return.

So:

| Change | Bump |
| --- | --- |
| A working call site stops working — a variable renamed or removed, a default changed, a filter returning a different shape | **major** |
| A new variable, a new filter, new behaviour behind an option that defaults to today's | **minor** |
| A fix that changes nothing a call site can see | **patch** |

Two traps worth naming. Tightening an input check is a **breaking** change if any existing
call site would now be refused, however wrong that call site was. And raising the
ansible-core floor is breaking too, since a consumer on the old floor can no longer install
the collection — see [Raising the ansible-core floor](#raising-the-ansible-core-floor).

## Doing the release

### 1. Make sure every change has a changelog fragment

Fragments should already be there — one lands with each change, in the same commit. Check
what is waiting:

```sh
ls changelogs/fragments/
```

Anything missing, add now: one YAML file per change, under `bugfixes:`, `minor_changes:`,
`breaking_changes:` and so on. `changelogs/README.md` has the sections and the markup rules
(reStructuredText, so ``` ``code`` ``` — not Markdown backticks — even though the output is
Markdown). A change nobody needs to read about goes under `trivial:`.

### 2. Add a release summary

One fragment for the release as a whole, not for any single change:

```yaml
# changelogs/fragments/release-summary.yml
release_summary: >-
  Adds a way to override the Caddy site block for apps behind an internal CA, and fixes a
  decommission that left dangling systemd symlinks behind.
```

This is the paragraph a consumer reads first. `antsibull-changelog release` warns if it is
missing rather than refusing, so it is easy to skip by accident.

### 3. Check the version-stamped documentation

A **new filter** needs `version_added: X.Y.Z` in its `DOCUMENTATION` block, naming the
version about to be released. All six existing filters carry one, and
`tests/unit/test_filter_docs.py` will not catch a wrong value — only a missing block.

Role options in `argument_specs.yml` do not declare `version_added` today. If you decide to
start, add it to the new option only; back-filling the rest is a separate change.

### 4. Fold the fragments into the changelog

```sh
antsibull-changelog release --version X.Y.Z
```

This does three things, all of which you commit:

1. moves every fragment's content into `changelogs/changelog.yaml` under a new `X.Y.Z:` key,
   stamped with today's date;
2. **deletes the fragments** — the permanent record is `changelog.yaml` from here on;
3. regenerates `CHANGELOG.md`.

Read the regenerated `CHANGELOG.md` before going on. The escaping is normal — `First
release\.`, `v1\.1\.0`, `<code>…</code>` — it renders correctly on GitHub, and hand-tidying
it will fail CI and be reverted by the next release. What you are looking for is wording,
missing entries, and a release summary that reads like one.

### 5. Bump the version in `galaxy.yml`

```yaml
version: X.Y.Z
```

`galaxy.yml` is the only file carrying the collection's version, and it is the one the
release workflow believes — the tag has to agree with it, not the other way round.

### 6. Run the gates

```sh
pytest tests/unit -q
ansible-lint
antsibull-changelog lint
ansible-galaxy collection build --output-path /tmp/collection-build
```

and, from a checkout laid out as `ansible_collections/binarycodes/homelab/` (both of these
refuse to run anywhere else — `CLAUDE.md` explains why):

```sh
ansible-test sanity --local
molecule test
```

`molecule test` also needs `sops` on `PATH` and takes minutes. CI runs all of it against
the tagged commit anyway, so a local run is about not burning a tag you have to abandon.

Two notes on output you can ignore. `ansible-lint` prints one
`[ERROR]: Unable to parse documentation in python file …` line per filter and then passes:
an ansible-lint/ansible-core interaction, not a fault in the files, and
`tests/unit/test_filter_docs.py` is what actually checks those docs. And `ansible-test
sanity` skips `compile` and `import` on Python versions the machine does not have
installed.

### 7. Commit everything together

```sh
git add galaxy.yml changelogs/ CHANGELOG.md
git commit -m "chore(release): X.Y.Z"
```

The version bump, the folded `changelog.yaml`, the emptied `changelogs/fragments/` and the
regenerated `CHANGELOG.md` are one commit. Splitting them leaves a commit in history that
CI would reject, and the release workflow checks the tagged commit for exactly this
consistency.

Commit messages are Conventional Commits, single-line, no body — `.githooks/commit-msg`
enforces it. Enable the hooks with `git config core.hooksPath .githooks` if this is a fresh
clone.

### 8. Rehearse (optional, recommended after a long gap)

Push the branch and run **Release** from the Actions tab against it. From a branch the run
is a rehearsal: the version comes from `galaxy.yml`, every check and every gate runs, the
collection is built, and the two steps that reach outside the runner are skipped. It also
tells you whether the version number is still free on Galaxy — often the real question.

What separates a rehearsal from a release is `github.ref_type`, not an input, so nothing
published can come from a branch.

### 9. Tag and push

```sh
git push origin main
git tag vX.Y.Z
git push origin vX.Y.Z
```

The tag is what triggers the release. Note the `v` prefix, and that only
`v[0-9]+.[0-9]+.[0-9]+` matches — a prerelease tag like `v1.0.0-rc1` triggers nothing.

### 10. Approve the publish, then check it landed

The workflow runs the checks, then the three gate workflows (which take the better part of
half an hour), then waits on the `release` environment for a reviewer. Approve it, and
watch the publish step: Galaxy accepts the tarball and imports it asynchronously, and the
import is what actually validates the collection, so the step stays for the verdict.

Afterwards:

- the version is on Galaxy at
  `https://galaxy.ansible.com/ui/repo/published/binarycodes/homelab/`;
- a GitHub release exists for the tag, with the changelog section as its body and the
  built tarball attached — byte-for-byte the file that went to Galaxy;
- `ansible-galaxy collection install binarycodes.homelab:==X.Y.Z` works from a clean
  machine.

## What the workflow checks, and why

Every one of these is a way to ship the wrong thing, and each is cheaper to catch before
the gates than to supersede afterwards:

| Check | Catches |
| --- | --- |
| tag `vX.Y.Z` equals `galaxy.yml`'s `version` | the two being edited at different moments — the classic way a collection is published under a number nobody meant |
| this version has an entry in `changelogs/changelog.yaml` | tagging before running `antsibull-changelog release`, which would publish with an empty release note |
| `CHANGELOG.md` matches what `changelog.yaml` renders to | someone hand-editing the generated file, or running `release` without committing what it rewrote |
| the version is not already on Galaxy | a duplicate, half an hour before the publish would have refused it anyway |
| CI, supported-versions and molecule, **called against the tagged commit** | a green tick on `main` being a statement about whatever `main` was then; a tag can point anywhere |

## When something goes wrong

| Situation | What to do |
| --- | --- |
| A check failed before the gates | Fix it on `main`, delete the tag locally and remotely (`git tag -d vX.Y.Z; git push --delete origin vX.Y.Z`), commit, re-tag. Nothing has been published. |
| A gate failed | Same. A tag that never published can be moved freely. |
| The publish itself failed — network, Galaxy outage, missing secret | The tag is already pushed and there is nothing to re-tag. Fix the cause, then run **Release** from the Actions tab **against the tag**, which repeats the whole thing including the gates. |
| The publish succeeded but the GitHub release step failed | Only the announcement is missing. Create the release by hand, or re-run the job — the publish step refuses a duplicate, so it cannot double-upload. |
| Published the wrong content | It cannot be fixed in place. Publish `X.Y.Z+1` with the correction, and say so in its release summary. The bad version stays visible. |
| Published a version whose number was wrong | Same answer. Do not try to reuse the number. |

If a release is abandoned after `antsibull-changelog release` has run, the fragments are
gone from the working tree. `git restore changelogs/ CHANGELOG.md` before the release commit,
or revert that commit, brings them back — the fragments are in git history, which is the
reason step 7 commits them as one unit.

## Special cases

### Raising the ansible-core floor

Three places state the floor and must move together, or CI tests a promise the metadata
does not make:

1. `meta/runtime.yml` — `requires_ansible`, which is what Galaxy and `ansible-galaxy`
   enforce;
2. `roles/systemd_app/meta/main.yml` — `min_ansible_version`;
3. `.github/workflows/supported-versions.yml` — the matrix row labelled
   `(declared floor)`, which is what makes the claim tested rather than asserted.

This is a breaking change for anyone on the old floor, so it is a **major** bump.

### A first release from a fresh fork or namespace

Beyond the prerequisites above: the Galaxy namespace must exist and your account must own
it, and `galaxy.yml`'s `repository`, `documentation`, `homepage` and `issues` links must
point at the repository that actually exists — Galaxy publishes them on the collection's
page without checking any of them.

### Republishing after a deleted tag

Galaxy keys on the version in `galaxy.yml`, not on the tag. Deleting and re-pushing a tag
for a version that was never published is fine and routine. Deleting a tag for a version
that *was* published changes nothing on Galaxy — the artefact is already immutable there.
