#!/usr/bin/env bash
# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

# Prepare the release commit and open the pull request that carries it: fold the waiting
# fragments into the changelog for the next version, bump galaxy.yml to match, run the
# gates that take seconds, commit, push. Once that PR is on main, tag-release.sh does the
# rest. Refuses when nothing is waiting, when the release summary is missing, when the
# fragments call for a bigger bump than asked, and when a plugin's version_added names a
# version that was never released and is not this one.
set -euo pipefail

usage() {
  echo "usage: $0 --bump major|minor|patch" >&2
  echo "       $0 X.Y.Z" >&2
  exit 2
}

die() { echo "refused: $*" >&2; exit 1; }

# One scalar from galaxy.yml, top level, plain or double-quoted.
galaxy_field() {
  local value
  value="$(sed -nE "s/^$1:[[:space:]]*\"?([^\"[:space:]]+)\"?[[:space:]]*\$/\1/p" galaxy.yml)"
  [ -n "$value" ] || die "galaxy.yml has no $1"
  echo "$value"
}

is_version() { [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; }

read_version() {
  local version
  version="$(galaxy_field version)"
  is_version "$version" || die "galaxy.yml version '$version' is not plain X.Y.Z"
  echo "$version"
}

next_version() {
  local major minor patch
  IFS=. read -r major minor patch <<< "$1"
  case "$2" in
    major) echo "$((major + 1)).0.0" ;;
    minor) echo "$major.$((minor + 1)).0" ;;
    patch) echo "$major.$minor.$((patch + 1))" ;;
    *) usage ;;
  esac
}

# Which component an explicit version moves, so it can be held to the same floor as --bump.
bump_between() {
  local from to
  IFS=. read -r -a from <<< "$1"
  IFS=. read -r -a to <<< "$2"
  if   [ "${from[0]}" != "${to[0]}" ]; then echo major
  elif [ "${from[1]}" != "${to[1]}" ]; then echo minor
  else echo patch
  fi
}

rank() { case "$1" in major) echo 3 ;; minor) echo 2 ;; patch) echo 1 ;; esac; }

check_tools() {
  local tool
  for tool in antsibull-changelog ansible-galaxy gh; do
    command -v "$tool" >/dev/null || die "$tool is not on PATH"
  done
  gh auth status >/dev/null 2>&1 || die "gh is not logged in; run: gh auth login"
}

check_on_current_main() {
  [ "$(git branch --show-current)" = main ] || die "not on main"
  [ -z "$(git status --porcelain)" ] || die "working tree is not clean"
  git fetch --quiet origin
  [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] \
    || die "main is not what origin/main has; run: git pull --ff-only"
}

list_fragments() {
  find changelogs/fragments -type f \( -name '*.yml' -o -name '*.yaml' \) | sort
}

# Sections that stop a call site working mean major; ones that add mean minor. Section keys
# sit at column 0, so a grep is the parse; a fragment malformed enough to fool it fails
# `antsibull-changelog lint` below.
implied_bump() {
  if   grep -qE '^(breaking_changes|major_changes|removed_features):' "$@"; then echo major
  elif grep -qE '^(minor_changes|deprecated_features):' "$@"; then echo minor
  else echo patch
  fi
}

check_fragments() {
  local bump="$1" implied; shift
  [ $# -gt 0 ] || die "no fragments waiting in changelogs/fragments/; nothing to release"
  # `antsibull-changelog release` only warns about this, which is how it gets skipped.
  grep -qE '^release_summary:' "$@" || die "no fragment carries release_summary; land one first"
  implied="$(implied_bump "$@")"
  [ "$(rank "$bump")" -ge "$(rank "$implied")" ] \
    || die "the fragments call for a $implied bump, not $bump:"$'\n'"$(grep -lE '^(breaking_changes|major_changes|removed_features|minor_changes|deprecated_features):' "$@")"
}

# antsibull-changelog builds the New Plugins and New Modules sections from version_added, so
# a value that is neither released nor this release mis-records what the release adds.
check_version_added() {
  local current="$1" new="$2" released file value
  released="$(sed -nE 's/^  ([0-9]+\.[0-9]+\.[0-9]+):$/\1/p' changelogs/changelog.yaml)"
  for file in plugins/filter/*.py plugins/modules/*.py; do
    while read -r value; do
      [ "$value" = "$new" ] || grep -qFx "$value" <<< "$released" \
        || die "$file: version_added $value is neither a released version nor $new"
    done < <(sed -nE "s/^[[:space:]]*version_added:[[:space:]]*['\"]?([^'\"[:space:]]+)['\"]?.*/\1/p" "$file")
  done
  git rev-parse -q --verify "refs/tags/v$current" >/dev/null || return 0
  while read -r file; do
    grep -qE "^version_added:[[:space:]]*['\"]?${new}['\"]?[[:space:]]*\$" "$file" \
      || die "$file is new since v$current but does not carry version_added: $new"
  done < <(git diff --name-only --diff-filter=A "v$current" HEAD -- 'plugins/filter/*.py' 'plugins/modules/*.py')
}

fold_changelog() {
  antsibull-changelog release --version "$1"
  sed -i -E "s/^version:[[:space:]]*.*/version: $1/" galaxy.yml
  grep -qx "version: $1" galaxy.yml || die "could not set version: $1 in galaxy.yml"
}

# The gates that take seconds, as ci.yml runs them; sanity and molecule wait for the PR.
# The sync check compares against what `release` just wrote, since HEAD predates it.
run_fast_gates() {
  antsibull-changelog lint
  antsibull-changelog lint-changelog-yaml changelogs/changelog.yaml
  cp CHANGELOG.md "$1/CHANGELOG.released.md"
  antsibull-changelog generate
  cmp -s CHANGELOG.md "$1/CHANGELOG.released.md" || die "CHANGELOG.md is not what changelog.yaml renders to"
  ansible-galaxy collection build --output-path "$1" >/dev/null </dev/null
}

# The release branch exists from the first edit on, so a refusal or a failure in the middle
# never leaves main dirty; what it leaves is the branch, and this says how to drop it.
cleanup() {
  local status=$?
  rm -rf "$tmp"
  if [ "$status" -ne 0 ] && [ -n "$branch" ]; then
    echo "left on $branch with the changes so far. undo with:" >&2
    echo "    git restore galaxy.yml changelogs CHANGELOG.md && git switch main && git branch -D $branch" >&2
  fi
}

open_pr() {
  local version="$1" notes="$2" answer
  echo
  echo "release notes for $version, as the PR body will carry them:"
  echo
  # Shown unescaped for reading; the PR body keeps antsibull-changelog's Markdown as is.
  sed -E 's/\\(.)/\1/g; s#</?code>#`#g; /^<a id=/d' "$notes" | cat -s | sed 's/^/    /'
  echo
  read -r -p "commit chore(release): $version, push release/$version and open the pull request? [y/N] " answer
  case "$answer" in
    y|Y)
      git add galaxy.yml changelogs CHANGELOG.md
      git commit -q -m "chore(release): $version"
      git push -q -u origin "release/$version"
      gh pr create --title "chore(release): $version" --body-file "$notes"
      ;;
    *)
      echo "left uncommitted on release/$version. commit with:"
      echo "    git add galaxy.yml changelogs CHANGELOG.md && git commit -m 'chore(release): $version'"
      echo "or undo with:"
      echo "    git restore galaxy.yml changelogs CHANGELOG.md && git switch main && git branch -D release/$version"
      ;;
  esac
}

tmp=""
branch=""

main() {
  local bump current new
  case "${1-}" in
    --bump) [ $# -eq 2 ] || usage; bump="$2" ;;
    "") usage ;;
    -*) usage ;;
    *) [ $# -eq 1 ] || usage; is_version "$1" || usage; new="$1" ;;
  esac

  cd "$(git rev-parse --show-toplevel)"
  check_tools
  check_on_current_main
  current="$(read_version)"
  if [ -n "${new-}" ]; then
    if [ "$new" = "$current" ] || [ "$(printf '%s\n%s\n' "$current" "$new" | sort -V | tail -1)" != "$new" ]; then
      die "$new is not above the current version $current"
    fi
    bump="$(bump_between "$current" "$new")"
  else
    new="$(next_version "$current" "$bump")"
  fi

  mapfile -t fragments < <(list_fragments)
  check_fragments "$bump" "${fragments[@]}"
  check_version_added "$current" "$new"

  tmp="$(mktemp -d)"
  trap cleanup EXIT
  branch="release/$new"
  git switch -q -c "$branch"
  fold_changelog "$new"
  run_fast_gates "$tmp"
  antsibull-changelog generate "$new" --only-latest --output "$tmp/notes.md"
  open_pr "$new" "$tmp/notes.md"
}

main "$@"
