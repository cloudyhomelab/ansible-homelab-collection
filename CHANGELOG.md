# Changelog

All notable changes to `binarycodes.homelab` are recorded here. The collection follows
[semantic versioning](https://semver.org): the role's variables and the filters' names and
return shapes are its public API.

## 1.0.0

First release. Extracted from the playbook repository it grew up in, with the
repository-specific parts removed.

- `systemd_app` role — deploy or decommission one app, either from a controller directory
  of Quadlet files, systemd units and config (`source`) or as a single-container Quadlet
  rendered from call-site parameters (`inline`); install-manifest reconciliation across
  both kinds; SOPS-encrypted podman secrets with digest-based rotation; an optional Caddy
  route; pre-created bind-mount directories; healthcheck-gated `podman auto-update`
  rollback.
- Filters `secret_digests`, `reconcile_secrets`, `route_problems`, `container_problems`
  and `systemd_env_lines`, callable independently of the role.
- Molecule scenario covering both kinds, idempotence, the manifest prune, a change of kind
  and a repeated decommission.
