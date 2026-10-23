# external_packages

Installs deb or rpm package files that no repository the host uses carries, such as a
project's release downloads: each is downloaded, verified against a pinned checksum,
installed and deleted again.

## Requirements

**A privileged play.** Packages are installed as root, so the caller sets `become: true` on
the play or on the role entry.

**On the target host:** apt, or dnf (`dnf5` included). The format follows the host's package
manager: debs on apt, rpms on dnf. Any other package manager fails the role, unless the list
is empty. Fedora 43 and Debian 13 are what the molecule scenario converges and what
`meta/main.yml` claims.

## Role parameters

| Variable                 | Default | Purpose                         |
| ------------------------ | ------- | ------------------------------- |
| `external_packages_list` | `[]`    | The packages to install, below. |

Each entry, every key required:

| Key        | Purpose |
| ---------- | ------- |
| `name`     | The package's name, as the package manager reports it. |
| `version`  | The version the package file carries: `[epoch:]upstream[-revision]` for a deb, `[epoch:]version-release` for an rpm. It decides whether the package is installed already, so it has to match what the package manager reports exactly; read it from the file once (below). |
| `url`      | Where to download the file from: http, https, ftp or file. Which file suits which host is up to the caller. |
| `checksum` | `<algorithm>:<hex digest>` of the file, with the algorithm one of `md5`, `sha1`, `sha224`, `sha256`, `sha384`, `sha512`. |

The version a file carries, when its name or the release tag does not say:

```sh
dpkg-deb -f grype_0.121.0_linux_amd64.deb Version                                       # 0.121.0
rpm -qp --qf '%|EPOCH?{%{EPOCH}:}:{}|%{VERSION}-%{RELEASE}\n' grype_0.121.0_linux_amd64.rpm  # 0.121.0-1
```

## What it does

With an empty list, nothing. Otherwise:

1. Checks the host's package manager and every entry before downloading anything: a name
   that is not one in the host's format, a version that is not, a URL with no supported
   scheme, a checksum that is missing, malformed or the wrong length, a duplicate, an unknown
   key. One run reports every problem.
2. For each entry, asks `dpkg-query` or `rpm` for the installed version. A package already at
   its version is left alone and nothing is downloaded.
3. Otherwise downloads the file into a fresh temporary directory, where `get_url` refuses it
   unless the checksum matches; installs it with `apt` (updating the lists, for its
   dependencies) or `dnf`; and removes the directory, also when the install failed.
4. Reads the installed version again and fails if it is not the entry's. A version that does
   not match the package's own would otherwise download and reinstall on every run.

**Check mode** reads the installed versions and reports each package it would install as
changed, without downloading anything.

**Signatures.** An rpm is installed with dnf's signature check off, since a release's rpm is
unsigned or signed with a key the host has not imported. The checksum is what the file is
trusted by, so pin it from a source you trust.

**Upgrades and downgrades.** Raising an entry's version installs the newer file over the old
one. Lowering it fails: neither `apt` nor `dnf` downgrades unless told to, and this role does
not tell them.

**Removal is not reconciled.** A package whose entry is dropped stays installed. Remove it
with `ansible.builtin.package` and `state: absent`.

## Example

```yaml
- hosts: all
  become: true
  vars:
    grype_version: 0.121.0
  roles:
    - role: binarycodes.homelab.external_packages
      external_packages_list:
        - name: grype
          version: "{{ grype_version }}"
          url: "https://github.com/anchore/grype/releases/download/v{{ grype_version }}/grype_{{ grype_version }}_linux_amd64.deb"
          checksum: sha256:3a23dcb0dfe903131eabb4af9e8977ea2a47e2bbbec5aa15b60026ca5467d46d
```

## Where the logic lives

| Plugin                      | Kind   | Used for                                          |
| --------------------------- | ------ | ------------------------------------------------- |
| `package_validation_errors` | filter | Checking every entry of `external_packages_list`. |

[Semantic versioning](https://semver.org): the role's variables and the plugins' names and
return shapes are the public API. Each release is in [CHANGELOG.md](CHANGELOG.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

GPL-3.0-or-later — see [LICENSE](LICENSE).
