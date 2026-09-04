# `binarycodes.homelab`

Deploys Podman [Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
apps and plain systemd units on a host, with an optional Caddy route, install-manifest
reconciliation so a renamed or deleted file does not linger, and SOPS-encrypted podman
secrets.

## Contents

| Role | Purpose |
| ---- | ------- |
| [`systemd_app`](roles/systemd_app/README.md) | Deploy or decommission one app — Quadlet files, systemd units and a config tree from a directory (`source`), or a single-container Quadlet rendered from call-site parameters (`inline`) — plus its route. |

| Filter | Purpose |
| ------ | ------- |
| `secret_digests` | SHA-256 per podman secret value, the record a converge compares against. |
| `reconcile_secrets` | Which podman secrets to store and which to drop, given the declared set, the recorded digests and what the store holds. |
| `route_problems` | Check a domain, upstream and port before they are written into a Caddy site block. |
| `container_problems` | Check what would be interpolated into a rendered Quadlet. |
| `systemd_env_lines` | Quote and escape a dict into `Environment=` lines. |
| `manifest_units` | The systemd units a recorded install manifest implies, so a teardown need not be told them. |

The filters are public API, not role internals: they are named for what they compute, and
callable by anyone who installs the collection.

## Install

```yaml
# requirements.yml
collections:
  - name: binarycodes.homelab
    version: ">=1.0.0"
```

```sh
ansible-galaxy collection install -r requirements.yml
```

`community.sops` comes with it — the role calls `community.sops.load_vars` for any app that
ships a `secrets.sops.yaml`, and decrypting one also needs the `sops` binary and a key on
the controller.

## Use

The role runs once per app, in a play that is privileged: it writes root-owned files,
calls `podman` against the root store and drives system units. Rootful podman 4.4 or newer
(5.0 for an app with a healthcheck), and systemd, are the host's side of the contract —
in practice Fedora, or Debian 13 and later, which are the platforms it is tested on.

```yaml
- hosts: all
  become: true

  vars:
    # Fleet-wide, shared by every app in the play.
    systemd_app_apps_dir: "{{ playbook_dir }}/../apps"
    systemd_app_network: web.network
    systemd_app_caddy_confd: /var/app/reverse_proxy/config/conf.d

  roles:
    - role: binarycodes.homelab.systemd_app
      systemd_app_kind: inline
      systemd_app_name: myapi
      systemd_app_image: docker.io/org/myapi:latest
      systemd_app_domain: api.example.com
      systemd_app_health_cmd: "wget -qO /dev/null http://127.0.0.1:8080/ || exit 1"
```

Every parameter, both kinds, the manifest, routing, secrets and what makes a change take
effect: [`roles/systemd_app/README.md`](roles/systemd_app/README.md).

## Tests

```sh
pip install -r requirements-dev.txt   # the tools, pinned; into the env that holds ansible-core
pytest tests/unit -q     # the filters, as Python
ansible-lint             # roles, playbooks and the molecule scenario
antsibull-changelog lint # the changelog fragments
molecule test            # the role against a systemd container (see extensions/molecule/default/)
```

`molecule test` also needs `sops` on PATH and a checkout laid out as
`ansible_collections/binarycodes/homelab/`; the scenario's README says why.

## Versioning and changes

[Semantic versioning](https://semver.org): the role's variables and the filters' names and
return shapes are the collection's public API. What changed in each release is in
[CHANGELOG.md](CHANGELOG.md), which is generated from changelog fragments — see
[`changelogs/README.md`](changelogs/README.md) before editing either, and
[RELEASE.md](RELEASE.md) for how a release is cut.

## Licence

GPL-3.0-or-later — see [LICENSE](LICENSE).
