# binarycodes.homelab

An Ansible collection: the `systemd_app` role deploys Podman Quadlet apps and systemd
units, with an optional Caddy route, install-manifest reconciliation and SOPS-encrypted
podman secrets.

[CONTRIBUTING.md](CONTRIBUTING.md) before changing anything: setup, the gates, commit
messages, the changelog fragment every pull request adds, the licence header, the
conventions. [RELEASE.md](RELEASE.md) is the release procedure. [README.md](README.md) is
for people using the collection.

## Working notes

`docs/` is gitignored and never committed. `docs/fixme.md` is the burn-down list of
outstanding issues: delete each item as it is fixed, delete the file once it is empty, and
never cite it or its item numbers from a commit message, changelog fragment, code comment or
anything else tracked.
