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

Run it from the collection root: molecule finds `extensions/molecule/` by itself once
`galaxy.yml` is there, and installs the collection before the converge, which is how the
role and its filters resolve by FQCN.

If `create` fails on the privileged container, the outer podman needs to be rootful —
`sudo env "PATH=$PATH" ANSIBLE_COLLECTIONS_PATH="$HOME/.ansible/collections" molecule test`,
the collection path being what a plain `sudo` would otherwise look for under `/root`.

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
