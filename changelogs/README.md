# Changelog fragments

`CHANGELOG.md` at the repository root is **generated**. Do not edit it — an edit is lost
the next time anyone runs `antsibull-changelog`, and CI fails the moment the file stops
matching `changelogs/changelog.yaml`.

What you edit instead is a *fragment*: a small YAML file in `fragments/`, added in the same
commit as the change it describes. That is the whole point of the arrangement — a changelog
line lands while the reason for it is still in front of you, rather than being reconstructed
from `git log` at release time.

Every pull request adds one, however small the change: `trivial:` exists for a change nobody
needs to read about, and writing that line is the decision, made where the change is. CI
(`Changelog fragment` in `.github/workflows/checks.yml`) fails a pull request that adds
none. The one exception is the release PR, which folds every fragment away and bumps
`galaxy.yml` instead.

## Adding one

Name the file after the change (a branch name or issue number is fine; the name never
appears anywhere) and give it one or more sections:

```yaml
# changelogs/fragments/disable-units-on-absent.yml
bugfixes:
  - The ``systemd_app`` role now disables a plain systemd unit before removing its unit
    file on ``state=absent``, so a decommission no longer leaves a dangling
    ``.wants`` symlink behind.
```

The sections, in the order they render:

| Section | For |
| --- | --- |
| `release_summary` | One paragraph introducing the whole release. At most one per release. |
| `major_changes` | Changes a consumer must read before upgrading. |
| `minor_changes` | New parameters, new filters, behaviour that gained an option. |
| `breaking_changes` | Anything that makes a working call site stop working. |
| `deprecated_features` | Still works, will not forever. |
| `removed_features` | Previously deprecated, now gone. |
| `security_fixes` | Say what was exposed and to whom. |
| `bugfixes` | What was wrong, and what it now does instead. |
| `known_issues` | Shipped with a limitation worth naming. |
| `trivial` | Renders nowhere. For a change that genuinely needs no changelog line. |

Every section but `release_summary` is a list. Write entries as full sentences, in the past
or present tense but consistently within one entry, and name the thing that changed the way
a consumer names it — `systemd_app_health_cmd`, not "the health option".

**Markup is reStructuredText**, even though the output is Markdown: use ``` ``double
backquotes`` ``` for anything code-shaped. Plain Markdown backticks are escaped into
literal backticks in the rendered file. The generated Markdown escapes ordinary punctuation
too (`First release\.`), which looks odd in the raw file and renders correctly on GitHub —
that is antsibull-changelog's normal output, not damage.

## Checking it

```sh
antsibull-changelog lint                                    # the fragments
antsibull-changelog lint-changelog-yaml changelogs/changelog.yaml
antsibull-changelog generate && git diff --exit-code CHANGELOG.md   # the file is in sync
```

CI runs all three, and `ansible-test sanity` validates `changelog.yaml` as well.

## At release time

`antsibull-changelog release --version X.Y.Z` folds every fragment into
`changelogs/changelog.yaml`, deletes the fragments (`keep_fragments: false`) and
regenerates `CHANGELOG.md`. The Prepare release workflow runs it and commits all of it
together with the `galaxy.yml` version bump as the release PR; the Release run reads the
version's notes straight out of `changelog.yaml` for the GitHub release body, so a version
with no entry there fails the release rather than publishing with an empty note.
`RELEASE.md` at the repository root is the procedure.
