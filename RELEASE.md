# Releasing `binarycodes.homelab`

A version published to Ansible Galaxy **cannot be replaced and cannot be deleted**: a mistake
is superseded, never rolled back, and the wrong artefact stays visible for good. So three
workflows spend most of their effort refusing to publish, and the release itself is the
three steps below — two labels and two approvals, nothing typed locally and nothing merged
by hand. Everything after them is what each step does and why.

## The steps

1. **Land the release summary** through a pull request labelled
   `prepare-release-minor` (or `-major`, `-patch`, or `-X.Y.Z` for an explicit version), on
   its own or with the last change going in. **Label before merging.** The merge runs
   **Prepare release**, which comments on that PR with the release PR's address — or with
   why it refused.
   → [The release summary](#the-release-summary)
   · [Choosing the version number](#choosing-the-version-number)
   · [What Prepare release does](#what-prepare-release-does)

2. **Approve the release PR.** On its Checks run, read the brief the `Release brief` job
   wrote, then approve the `prepare` environment. The PR merges itself, **Tag release** tags
   the merge `vX.Y.Z`, and the tag starts the **Release** run.
   → [Approving the release PR](#approving-the-release-pr)
   · [What Tag release does](#what-tag-release-does)

3. **Approve the publish** on the Release run's page, after reading its summary. The run's
   last job then confirms the version installs from Galaxy.
   → [Approving the publish](#approving-the-publish)

First time here, or been a while? → [Before you start](#before-you-start). Something
failed? → [When something goes wrong](#when-something-goes-wrong).

## Before you start

Once, on the GitHub repository:

- **A `GALAXY_API_KEY` secret**, from your Galaxy account's namespace. Without it the
  publish step fails with a clear message rather than uploading nothing.
- **The GitHub App**, whose credentials are the `CLOUDYHOME_BOT_CLIENT_ID` and
  `CLOUDYHOME_BOT_PRIVATE_KEY` secrets, installed on the repository with **Contents:
  write**, **Pull requests: write** and **Issues: write** (labels live under Issues). It is
  the identity that pushes the release branch and the tag, opens the release PR and comments
  on the PR you labelled. It has to be a second identity: an event caused by the workflow's
  own `GITHUB_TOKEN` starts no workflow, so a release PR opened with it would get no Checks
  run and a tag pushed with it would start no Release.
- **Two environments, each with a required reviewer**: `prepare`, which holds the release
  PR until someone has read its brief, and `release`, which holds the publish. Each is the
  stop button before something that cannot be undone. Without the protection rule the job
  simply runs, so each guard is only as real as its environment — check both are still
  there if it has been a while.
- **Allow auto-merge** enabled in the repository's settings, with squash an allowed merge
  method, and branch protection on `main` requiring the `All gates passed` check — which is
  what auto-merge waits on. Without the setting the release PR waits to be merged by hand
  once approved, and Prepare release's comment says so.
- **The labels** `prepare-release-major`, `prepare-release-minor` and
  `prepare-release-patch`. A `prepare-release-X.Y.Z` label is created when an explicit
  version is wanted; the `release-X.Y.Z` labels are created by Prepare release itself.

Nothing needs installing on your machine and nothing needs push access: each step is a
label or an approval, and every recovery is a run of the same workflow from the Actions tab.

## Choosing the version number

[Semantic versioning](https://semver.org). The collection's public API is:

- every `systemd_app_*` role variable, as documented in
  `roles/systemd_app/meta/argument_specs.yml`;
- the filters' and modules' names, their arguments, and the shape of what they return.

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

The bump goes in the label. `prepare-release-2.0.0` names the version outright; it has to
sort above the current one, and the component it moves is the bump the fragments are held
to.

## The release summary

One fragment for the release as a whole, not for any single change:

```yaml
# changelogs/fragments/release-summary.yml
release_summary: >-
  Adds a way to override the Caddy site block for apps behind an internal CA, and fixes a
  decommission that left dangling systemd symlinks behind.
```

This is the paragraph a consumer reads first, so it is reviewed like anything else a
consumer reads: it lands on `main` through a pull request, on its own or with the last
change going into the release. Prepare release refuses to run without it.

The per-change fragments need no step: CI refuses a pull request that adds none, so every
change since the last release already carries one. `changelogs/README.md` has the sections
and the markup.

## What Prepare release does

`.github/workflows/prepare-release.yml` runs when a pull request into `main` merges carrying
a `prepare-release-<bump>` label. It works from `main`'s tip, not from the commit that
merged, since a release folds everything on `main`. It refuses — and quotes the reason in a
comment on the PR you labelled — if:

- the PR carries more than one `prepare-release-` label, or one in a shape it does not
  know;
- `galaxy.yml`'s version is not plain `X.Y.Z`, or an explicit version is not above it;
- the branch `release/X.Y.Z` already exists on `origin`: a release PR for it is open, or
  was abandoned without deleting the branch;
- no fragment is waiting, or none carries `release_summary`;
- the fragments call for a bigger bump than asked: any `breaking_changes`, `major_changes`
  or `removed_features` section means major, any `minor_changes` or `deprecated_features`
  means minor. A bigger bump than they imply is allowed;
- a filter or module carries a `version_added` that names neither a released version nor
  this one, or is new since the last release's tag and does not name this one.

That last check is not cosmetic: `antsibull-changelog` builds the New Plugins and New
Modules sections from `version_added`, so a wrong value mis-records what the release adds.
Role options in `argument_specs.yml` do not declare `version_added` today; if you decide to
start, add it to the new option only.

Then, on a new `release/X.Y.Z` branch, it:

1. runs `antsibull-changelog release --version X.Y.Z`, which folds every fragment into
   `changelogs/changelog.yaml`, **deletes the fragments**, and regenerates `CHANGELOG.md`;
2. sets `version: X.Y.Z` in `galaxy.yml`, the only file carrying the version and the one
   the Release run believes;
3. runs the gates that take seconds — the changelog lints, the `CHANGELOG.md` sync check
   and `ansible-galaxy collection build`; sanity and molecule run on the release PR;
4. commits `chore(release): X.Y.Z` — the version bump, the folded `changelog.yaml`, the
   emptied `changelogs/fragments/` and the regenerated `CHANGELOG.md` as one commit, which
   is what the Release run checks the tagged commit for — pushes the branch, creates the
   label `release-X.Y.Z` and opens the pull request with that label and the release notes
   as its body;
5. enables auto-merge (squash) on the PR, so it merges the moment `All gates passed` is
   green — which it cannot be before the `prepare` approval;
6. comments on the PR you labelled with the release PR's address and whether it merges
   itself.

**The release PR is where the notes are read.** Its body is the release notes as the
GitHub release will carry them. The `\.` and `<code>` escaping in the raw text is normal
`antsibull-changelog` output that renders correctly on GitHub; hand-tidying it in
`CHANGELOG.md` fails CI. A note that reads wrong is fixed where it was written: close the
release PR, delete its branch — `main` is untouched and every fragment still waits there —
fix the fragment through a pull request, and give that PR the `prepare-release-<bump>` label
so its merge prepares the release again. A release nobody wants is the same PR closed and
its branch deleted, and nothing more.

## Approving the release PR

The release PR's Checks run has two jobs no other PR has. **`Release brief`** makes the
cheap checks the Release run's first job makes — the changelog lints, an entry for this
version, `CHANGELOG.md` in sync with `changelog.yaml`, the version not yet on Galaxy —
builds the collection, and writes the brief to its summary: every commit since the previous
release tag with a compare link, the release notes as the GitHub release will carry them,
and the built tarball's listing. **`Approve the release`** then waits on the `prepare`
environment.

**Read the brief before approving.** A commit you did not expect or a note that reads wrong
is the moment to stop: nothing has been tagged, and the PR can be closed as described
above.

The approval is the decision to release. `All gates passed` waits on it, auto-merge waits
on `All gates passed`, the merge runs Tag release, and the tag runs Release; nobody presses
Merge. A push to the release branch starts a new Checks run, which asks again.

To stop a release once its PR is open:

- **close the PR and delete its branch.** `main` is untouched; the fragments still wait
  there for the next attempt;
- or **remove the `release-X.Y.Z` label** before the merge. The PR then merges, once
  approved, and is not tagged: the release commit lands on `main` and can be tagged later by
  running Tag release from the Actions tab.

## What Tag release does

`.github/workflows/tag-release.yml` runs when a pull request into `main` merges carrying a
`release-X.Y.Z` label. It works on the merge commit and never on wherever `main` has moved
since. It refuses if:

- the PR carries more than one `release-` label, or one that is not `release-X.Y.Z`;
- the label's version is not exactly `galaxy.yml`'s: a release branch edited after it was
  labelled, or a label on the wrong PR;
- a fragment is still waiting in `changelogs/fragments/`: a change with a fragment merged
  after the release PR was opened, so the changelog does not describe everything the tag
  would publish;
- `vX.Y.Z` already exists on `origin`;
- Galaxy already has the version, or answers anything but 404 for it.

Then it writes the tag message — `binarycodes.homelab X.Y.Z`, then the version's notes from
`changelog.yaml` with antsibull's Markdown escaping undone for reading in a terminal — tags
the merge commit `vX.Y.Z` as the App, pushes the tag and writes the message to the run's
summary. The pushed tag is what starts Release. Note the `v` prefix, and that only
`v[0-9]+.[0-9]+.[0-9]+` matches: a prerelease tag like `v1.0.0-rc1` triggers nothing.

## Approving the publish

The Release run checks the tag against `galaxy.yml` and the changelog, then runs the three
gate workflows (which take the better part of half an hour), then waits on the `release`
environment for a reviewer.

**Read the run's summary before approving.** The first job writes it while the gates are
still running, so it is on the run's page by the time the publish is waiting: the same
brief as on the release PR, written by the same action, now headed by the tag and the commit
it points at. A commit you did not expect or a note that reads wrong is the moment to
reject: nothing has been published, and a tag that never published can be deleted and
moved.

Then approve, and watch the publish step: Galaxy accepts the tarball and imports it
asynchronously, and the import is what actually validates the collection, so the step stays
for the verdict. The GitHub release is created after the upload, so a failed publish leaves
no release announcing a version that is not there.

Afterwards the run's last job, **`Verify X.Y.Z is installable`**, confirms from a runner
that has none of this checkout:

- `ansible-galaxy collection install binarycodes.homelab:==X.Y.Z` works from Galaxy,
  retrying for up to ten minutes while Galaxy's index catches up with its import;
- `ansible-doc` reads a filter and a module from the install;
- the GitHub release for the tag carries the built tarball, byte-for-byte the file that
  went to Galaxy, since one job built, uploaded and attached it.

Its summary ends the run with the Galaxy page,
`https://galaxy.ansible.com/ui/repo/published/binarycodes/homelab/`, and the install
output. A failure there is a note to look by hand, not a rollback: there is none, and the
publish is done.

## What the workflows check, and why

Every one of these is a way to ship the wrong thing, and each is made where it is cheapest:
before the branch exists, before the tag exists, before the gates, or — the last row —
after the publish, where the alternative is the first consumer finding out.

| Check | Where | Catches |
| --- | --- | --- |
| a fragment with `release_summary` is waiting | Prepare release | a release with no changes, or one whose notes have no opening paragraph — antsibull only warns |
| the fragments imply no bigger bump than the label asks | Prepare release | a breaking change shipped as a minor, a feature as a patch |
| every `version_added` names a release | Prepare release | New Plugins and New Modules sections that mis-record what the release adds |
| `release/X.Y.Z` does not exist | Prepare release | two labelled PRs merged back to back, which would be two release PRs for one set of fragments |
| the changelog lints, `CHANGELOG.md` matches what `changelog.yaml` renders to | Prepare release, Release brief, Release | a fragment that will not parse; someone hand-editing the generated file |
| the release label's version is `galaxy.yml`'s | Tag release | a release branch edited after it was labelled; a label on the wrong PR |
| no fragment is waiting | Tag release | a change merged after the release PR opened, whose note the tag would leave out |
| `vX.Y.Z` does not exist; Galaxy does not have the version | Tag release, Release brief, Release | a duplicate, before the gates would have run for half an hour |
| tag `vX.Y.Z` equals `galaxy.yml`'s `version` | Release | a tag pushed by hand on the wrong commit — the classic way a collection is published under a number nobody meant |
| this version has an entry in `changelogs/changelog.yaml` | Release brief, Release | tagging before the fold, which would publish with an empty release note |
| CI, supported-versions and molecule, **called against the tagged commit** | Release | a green tick on `main` being a statement about whatever `main` was then; a tag can point anywhere |
| the version installs from Galaxy; the release carries the tarball | Release, after the publish | an import Galaxy accepted and then dropped; an announcement without its file |

## When something goes wrong

Every recovery is a run of the same workflow from the Actions tab, or a tag pushed by hand
on the merge commit, which the Release run checks like any other. Nothing here is a script.

| Situation | What to do |
| --- | --- |
| The summary PR merged without its `prepare-release` label | Run **Prepare release** from the Actions tab with the bump. It folds from `main`'s tip; nothing was consumed. |
| Prepare release refused | Its comment on the PR quotes why. Fix it on `main` through a pull request and give that PR the `prepare-release-<bump>` label, or run Prepare release from the Actions tab once it is fixed. |
| A change with a fragment lands on `main` after the release PR opened; or two labelled PRs merged back to back and the second refused | Close the release PR and delete its branch: `main` is untouched, nothing was consumed. Then label the next PR, or run Prepare release from the Actions tab. Merged anyway, Tag release refuses on the waiting fragment — see the next row. |
| The release PR merged but was not tagged: label removed, a refusal, a failure | The release commit is on `main`, untagged. Fix the cause on `main` through a pull request. A waiting fragment is folded into the version's entry with `antsibull-changelog release --version X.Y.Z` run by hand — it warns that the version exists, folds into it and keeps the date; CI accepts the changed `changelog.yaml` in place of a fragment. Then run **Tag release** from the Actions tab, which tags `main`'s tip, or tag by hand: `git tag -a vX.Y.Z -m "binarycodes.homelab X.Y.Z" <sha> && git push origin vX.Y.Z`. |
| Auto-merge could not be enabled | Prepare release's comment says so. Enable **Allow auto-merge** in the repository's settings, then `gh pr merge --auto --squash <url>`, or press Merge once the `prepare` approval is given and the checks are green. A merge by hand runs Tag release all the same. |
| A check or a gate failed on the Release run | Nothing is published; a tag that never published can be moved. Fix it on `main` as two rows up, delete the tag (`git push --delete origin vX.Y.Z`), and run Tag release from the Actions tab. |
| The publish itself failed — network, Galaxy outage, missing secret | The tag is already pushed and there is nothing to re-tag. Fix the cause, then run **Release** from the Actions tab **against the tag**, which repeats the whole thing including the gates. |
| The publish succeeded but the GitHub release step failed | Only the announcement is missing. Create the release by hand, or re-run the job — the publish step refuses a duplicate, so it cannot double-upload. |
| `Verify X.Y.Z is installable` failed | The publish is done. Look at the Galaxy page and the release's assets by hand; Galaxy's index may simply have been slower than ten minutes, in which case re-running the job says so. |
| Published the wrong content | It cannot be fixed in place. Publish `X.Y.Z+1` with the correction, and say so in its release summary. The bad version stays visible. |
| Published a version whose number was wrong | Same answer. Do not try to reuse the number. |

Before the release PR merges, the fragments are still on `main` and nothing is lost by
closing it. After it merges they are in the release commit, which is why that commit carries
them as one unit: a revert brings them back.

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
