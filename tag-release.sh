#!/usr/bin/env bash
# Copyright (c) 2026 binarycodes
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

# Tag the release galaxy.yml names, with CHANGELOG.md's notes for that version as the
# annotated tag message, and push the tag — which is what triggers the release workflow.
# Refuses when a fragment is still waiting, when the version is already on Galaxy, when
# the tag exists, or when HEAD is not what origin/main has.
set -euo pipefail

die() { echo "refused: $*" >&2; exit 1; }

# One scalar from galaxy.yml, top level, plain or double-quoted.
galaxy_field() {
  local value
  value="$(sed -nE "s/^$1:[[:space:]]*\"?([^\"[:space:]]+)\"?[[:space:]]*\$/\1/p" galaxy.yml)"
  [ -n "$value" ] || die "galaxy.yml has no $1"
  echo "$value"
}

read_version() {
  local version
  version="$(galaxy_field version)"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "galaxy.yml version '$version' is not plain X.Y.Z"
  echo "$version"
}

check_tree_clean() {
  [ -z "$(git status --porcelain)" ] || die "working tree is not clean"
}

check_no_fragments() {
  local fragments
  fragments="$(find changelogs/fragments -type f ! -name '.gitkeep' | sort)"
  [ -z "$fragments" ] || die "fragments not yet released:"$'\n'"$fragments"
}

check_head_pushed() {
  git fetch --quiet origin
  git merge-base --is-ancestor HEAD origin/main || die "HEAD is not on origin/main; push main first"
}

check_tag_free() {
  local tag="$1"
  ! git rev-parse -q --verify "refs/tags/$tag" >/dev/null || die "tag $tag already exists locally"
  [ -z "$(git ls-remote --tags origin "refs/tags/$tag")" ] || die "tag $tag already exists on origin"
}

# Only 404 is conclusive proof the number is free; anything but 200 or 404 is an outage or
# a changed API, which must not be read either way.
check_not_on_galaxy() {
  local collection="$1" version="$2" url code
  url="https://galaxy.ansible.com/api/v3/plugin/ansible/content/published/collections/index/${collection/.//}/versions/$version/"
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "$url" || echo 000)"
  case "$code" in
    404) ;;
    200) die "$version is already on Galaxy, and a version cannot be replaced" ;;
    *)   die "Galaxy answered HTTP $code for $version; try again when it answers 404" ;;
  esac
}

# antsibull-changelog escapes Markdown punctuation and wraps names in <code>; undo that,
# drop the anchors, and keep the section between this version's heading and the next.
write_message() {
  local collection="$1" version="$2" file="$3"
  {
    echo "$collection $version"
    echo
    sed -E 's/\\(.)/\1/g; s#</?code>#`#g; /^<a id=/d' CHANGELOG.md \
      | awk -v h="## v$version" '$0 == h {p=1; next} /^## / {p=0} p'
  } > "$file"
  [ "$(wc -l < "$file")" -gt 2 ] || die "CHANGELOG.md has no section for $version"
}

# --cleanup=whitespace keeps the notes' "###" headings, which git would otherwise strip
# as comments.
create_tag() {
  local tag="$1" file="$2"
  git tag -a "$tag" --cleanup=whitespace -F "$file"
  echo "created $tag at $(git rev-parse --short HEAD):"
  echo
  # subject and body, not contents: a signed tag's contents end with the signature block
  git tag -l --format='%(contents:subject)%0a%0a%(contents:body)' "$tag" | sed 's/^/    /'
  echo
}

push_tag() {
  local tag="$1" answer
  read -r -p "push $tag to origin and start the release? [y/N] " answer
  case "$answer" in
    y|Y) git push origin "$tag" ;;
    *)   echo "not pushed. push with: git push origin $tag   (or undo with: git tag -d $tag)" ;;
  esac
}

main() {
  local collection version tag
  cd "$(git rev-parse --show-toplevel)"
  collection="$(galaxy_field namespace).$(galaxy_field name)"
  version="$(read_version)"
  tag="v$version"

  check_tree_clean
  check_no_fragments
  check_head_pushed
  check_tag_free "$tag"
  check_not_on_galaxy "$collection" "$version"

  message="$(mktemp)"
  trap 'rm -f "$message"' EXIT
  write_message "$collection" "$version" "$message"
  create_tag "$tag" "$message"
  push_tag "$tag"
}

main "$@"
