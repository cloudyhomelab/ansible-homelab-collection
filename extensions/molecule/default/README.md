# `systemd_app` molecule scenario

Converges the `systemd_app` role against a throwaway container that runs systemd as PID 1
with podman inside it, then asserts the host state it produced. The role's set arithmetic
and input checks are covered by `tests/unit/` as Python; this covers what only a real
host shows — the install manifest, the prune it drives, a change of kind, and
decommissioning.

## What it exercises

| Stage | Covers |
| --- | --- |
| `converge.yml` | both kinds side by side: a `source` app shipping a Quadlet, a plain unit and a nested config tree, and an `inline` app rendered from call-site parameters |
| `idempotence` | a second converge changes nothing |
| `verify.yml` | installed paths, the recorded manifest of each kind, the rendered Quadlet (including `systemd_env_lines`' quoting), both route snippets, the pre-created data directory's ownership, and unit state |
| `side_effect.yml` | a file dropped from the app's source tree is pruned and unrecorded; the same app converted `source` → `inline` prunes what the other kind installed; `absent` removes everything the apps owned and stays green when repeated |

## Running it

```sh
pipx inject ansible-core molecule --include-apps      # once; --include-apps is what puts
pipx inject ansible-core 'molecule-plugins[podman]'   # molecule on PATH, and only
                                                      # molecule has an entry point
molecule test
```

Run it from the collection root, and that root has to sit at
`ansible_collections/binarycodes/homelab/` with the directory above `ansible_collections`
on `ANSIBLE_COLLECTIONS_PATH`. Molecule finds `extensions/molecule/` by itself once
`galaxy.yml` is there, but it does not install the collection under test: `converge.yml`
calls the role by FQCN, as a consuming play does, so the layout is the only thing that
makes `binarycodes.homelab.systemd_app` resolve. The same requirement `ansible-test
sanity` has, for the same reason.

```sh
ANSIBLE_COLLECTIONS_PATH=/path/above/ansible_collections molecule test
```

If `create` fails on the privileged container, the outer podman needs to be rootful. Both
collection roots have to be named across the `sudo`, root's own search path holding
neither this collection nor the `community.sops` installed under `$HOME`:

```sh
sudo env "PATH=$PATH" \
  ANSIBLE_COLLECTIONS_PATH="/path/above/ansible_collections:$HOME/.ansible/collections" \
  molecule test
```

While changing the role, `converge` is the loop to live in; `verify` and `side-effect`
assert against what it left, `login` opens a shell in the target and `destroy` removes it.
`test --destroy=never` keeps a failed container around to look at.

Two things worth knowing: an edit to `Dockerfile.j2` needs
`podman rmi molecule_local/systemd-app` first, the driver building the image only when it
is missing; and the fixture apps tree is copied into molecule's ephemeral directory by
`prepare.yml`, so a hand-edit under `apps/` takes a `prepare` to reach the target.

The scenario deliberately does not run the fixture containers on a podman network
(`Network=none`, `systemd_app_network: none`): joining one would put netavark in the
critical path of every run without covering anything the role does. Its fixture apps are
copied out of `apps/` into molecule's ephemeral directory first, so `side_effect.yml` can
drop a file from an app's source tree without editing a versioned file.
