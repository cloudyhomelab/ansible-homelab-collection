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

- **`antsibull-changelog`**, which writes the changelog, at the version CI uses. It is in
  `requirements-dev.txt` with the other tools; install that file into the environment that
  holds ansible-core.

- **The GitHub CLI, `gh`, logged in** to an account that can push a branch and open a pull
  request here. `prepare-release.sh` opens the release PR with it.
- **A `GALAXY_API_KEY` secret** on the GitHub repository, from your Galaxy account's
  namespace. Without it the publish step fails with a clear message rather than uploading
  nothing.
- **A `release` environment** configured in the repository's settings **with a required
  reviewer**. This is the stop button between "every gate passed" and the irreversible
  upload. Without the protection rule the job simply runs, so the guard is only as real as
  the environment — check it is still there if it has been a while.

You also need push access for a tag, and a clean, up-to-date `main`. `main` is protected,
so the release commit lands through a pull request like any other change, which
`prepare-release.sh` opens; only the tag is pushed directly.

## Choosing the version number

[Semantic versioning](https://semver.org). The collection's public API is:

- every `systemd_app_*` role variable, as documented in
  `roles/systemd_app/meta/argument_specs.yml`;
- the filters' names, their arguments, and the shape of what they return.

So:

| Change | Bump |
| --- | --- |
| A working call site stops working — a variable renamed or removed, a default changed, a filter returning a different shape | **major** |
| A new variable, a new filter, new behaviour behind an option that defaults to today's | **minor** |
| A fix that changes nothing a call site can see | **patch** |

Two traps worth naming, both **breaking**:

- tightening an input check, if any existing call site would now be refused, however wrong
  that call site was;
- raising the ansible-core floor, since a consumer on the old floor can no longer install
  the collection — see [Raising the ansible-core floor](#raising-the-ansible-core-floor).

## Doing the release

### 1. Write the release summary

One fragment for the release as a whole, not for any single change:

```yaml
# changelogs/fragments/release-summary.yml
release_summary: >-
  Adds a way to override the Caddy site block for apps behind an internal CA, and fixes a
  decommission that left dangling systemd symlinks behind.
```

This is the paragraph a consumer reads first, so it is reviewed like anything else a
consumer reads: it lands on `main` through a pull request, on its own or with the last
change going into the release. The per-change fragments are already there: CI refuses a
pull request that adds none, so every change since the last release carries one;
`changelogs/README.md` has the sections and the markup.

### 2. Prepare the release commit

```sh
git switch main
git pull --ff-only
./prepare-release.sh --bump minor      # or an explicit X.Y.Z
```

The bump is yours to choose, per [Choosing the version number](#choosing-the-version-number).
The script refuses if:

- you are not on a clean, up-to-date `main`;
- no fragment is waiting, or none carries `release_summary`;
- the fragments call for a bigger bump than asked: any `breaking_changes`, `major_changes`
  or `removed_features` section means major, any `minor_changes` or `deprecated_features`
  means minor. A bigger bump than they imply is allowed;
- a filter or module carries a `version_added` that names neither a released version nor
  this one, or is new since the last release and does not name this one.

That last check is not cosmetic: `antsibull-changelog` builds the New Plugins and New
Modules sections from `version_added`, so a wrong value mis-records what the release adds.
Role options in `argument_specs.yml` do not declare `version_added` today; if you decide to
start, add it to the new option only.

Then, on a new `release/X.Y.Z` branch, it:

1. runs `antsibull-changelog release --version X.Y.Z`, which folds every fragment into
   `changelogs/changelog.yaml`, **deletes the fragments**, and regenerates `CHANGELOG.md`;
2. sets `version: X.Y.Z` in `galaxy.yml`, the only file carrying the version and the one the
   release workflow believes;
3. runs the gates that take seconds — the changelog lints, the `CHANGELOG.md` sync check
   and `ansible-galaxy collection build`; sanity and molecule run on the pull request;
4. shows the release notes and asks.

**Read the notes before answering.** Wording, a missing entry, a summary that does not read
like one — this is the cheapest moment to fix any of it: answer no, edit the fragments, undo
with the command it prints and run it again. The `\.` and `<code>` escaping in the raw
`CHANGELOG.md` is normal antsibull-changelog output that renders correctly on GitHub;
hand-tidying it fails CI.

Answer yes and it commits `chore(release): X.Y.Z` — the version bump, the folded
`changelog.yaml`, the emptied `changelogs/fragments/` and the regenerated `CHANGELOG.md` as
one commit, which is what the release workflow checks the tagged commit for — pushes the
branch and opens the pull request with the notes as its body. Answer no and everything is
left uncommitted on the branch, with the commands to finish or undo by hand.

To run the slow gates locally before opening the PR, see the Gates section of `CLAUDE.md`:
`ansible-test sanity --local` and `molecule test` need the checkout at
`ansible_collections/binarycodes/homelab/`, and `molecule test` needs `sops` on `PATH`.
Two lines of output to ignore: `ansible-lint`'s one `Unable to parse documentation in python
file` per filter, which `tests/unit/test_filter_docs.py` covers, and `ansible-test sanity`
skipping `compile` and `import` on Python versions the machine lacks.

### 3. Rehearse (optional, recommended after a long gap)

Run **Release** from the Actions tab against the `release/X.Y.Z` branch. From a branch the
run is a rehearsal:

- the version comes from `galaxy.yml`;
- every check and every gate runs, including whether the version number is still free on
  Galaxy — often the real question;
- the collection is built;
- the two steps that reach outside the runner are skipped.

The run's summary shows what a release would have published: the commits since the
previous release tag and the release notes, then the built tarball's listing.

What separates a rehearsal from a release is `github.ref_type`, not an input, so nothing
published can come from a branch.

### 4. Merge the release pull request

Once its checks pass, like any other change. What gets tagged next is the merge commit on
`main`, not the commit on the branch. A change with a fragment that lands on `main` in
between is covered in [When something goes wrong](#when-something-goes-wrong).

### 5. Tag and push

```sh
git switch main
git pull --ff-only
./tag-release.sh
```

The script tags `HEAD` as `vX.Y.Z` from `galaxy.yml`'s `version`, with that version's
section of `CHANGELOG.md` as the annotated tag message, shows you the result and asks
before pushing. Answer no at the prompt to keep the local tag and push it yourself;
`git tag -d vX.Y.Z` undoes it.

It refuses if:

- the working tree is dirty;
- a fragment is still waiting in `changelogs/fragments/`;
- `HEAD` is not on `origin/main`;
- the tag already exists, locally or on `origin`;
- Galaxy already has the version.

Each is a mistake the workflow would otherwise report only after the gates.

The pushed tag is what triggers the release. Note the `v` prefix, and that only
`v[0-9]+.[0-9]+.[0-9]+` matches — a prerelease tag like `v1.0.0-rc1` triggers nothing.

### 6. Approve the publish, then check it landed

The workflow runs the checks, then the three gate workflows (which take the better part of
half an hour), then waits on the `release` environment for a reviewer.

**Read the run's summary before approving.** The first job writes it while the gates are
still running, so it is on the run's page by the time the publish is waiting:

- the tag and the commit it points at;
- every commit since the previous release tag, with a compare link;
- the release notes exactly as the GitHub release will carry them.

That summary is what you are approving. A commit you did not expect or a note that reads
wrong is the moment to reject, fix and re-tag — nothing has been published yet.

Then approve, and watch the publish step: Galaxy accepts the tarball and imports it
asynchronously, and the import is what actually validates the collection, so the step stays
for the verdict.

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
| A change with a fragment lands on `main` after the release PR, before the tag | Run `antsibull-changelog release --version X.Y.Z` again, by hand. It warns that the version exists, folds the fragment into that entry, keeps the date, and regenerates `CHANGELOG.md`. Commit the result before tagging; a fragment left behind is swept into the *next* release's entry instead. |
| The publish itself failed — network, Galaxy outage, missing secret | The tag is already pushed and there is nothing to re-tag. Fix the cause, then run **Release** from the Actions tab **against the tag**, which repeats the whole thing including the gates. |
| The publish succeeded but the GitHub release step failed | Only the announcement is missing. Create the release by hand, or re-run the job — the publish step refuses a duplicate, so it cannot double-upload. |
| Published the wrong content | It cannot be fixed in place. Publish `X.Y.Z+1` with the correction, and say so in its release summary. The bad version stays visible. |
| Published a version whose number was wrong | Same answer. Do not try to reuse the number. |

If a release is abandoned after `antsibull-changelog release` has run, the fragments are
gone from the working tree. Before the release commit, the undo command `prepare-release.sh`
prints brings them back; after it, revert the commit — the fragments are in git history,
which is the reason step 2 commits them as one unit.

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

Beyond the prerequisites above:

- the Galaxy namespace must exist and your account must own it;
- `galaxy.yml`'s `repository`, `documentation`, `homepage` and `issues` links must point at
  the repository that actually exists — Galaxy publishes them on the collection's page
  without checking any of them.

### Republishing after a deleted tag

Galaxy keys on the version in `galaxy.yml`, not on the tag. Deleting and re-pushing a tag
for a version that was never published is fine and routine. Deleting a tag for a version
that *was* published changes nothing on Galaxy — the artefact is already immutable there.
