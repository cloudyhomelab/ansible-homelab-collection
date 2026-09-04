# `systemd_app` molecule scenario

Converges the `systemd_app` role against a throwaway container that runs systemd as PID 1
with podman inside it, then asserts the host state it produced. The role's set arithmetic
and input checks are covered by `tests/unit/` as Python; this covers what only a real
host shows — the install manifest, the prune it drives, a change of kind, and
decommissioning.

## What it exercises

| Stage | Covers |
| --- | --- |
| `converge.yml` | both kinds side by side: a `source` app shipping a Quadlet, a plain unit and a nested config tree, an `inline` app rendered from call-site parameters, an `inline` app whose whole directory is one encrypted secrets file, and a `source` app deployed with a unit name it is torn down without |
| `idempotence` | a second converge changes nothing |
| `verify.yml` | installed paths, the recorded manifest of each kind, the rendered Quadlet (including `systemd_env_lines`' quoting), both route snippets, the pre-created data directory's ownership, unit state, and the secrets path end to end — decrypted, stored under their own names carrying the owner and digest labels, no record file beside them, referenced by the Quadlet and reaching the running container as variables |
| `side_effect.yml` | a file dropped from the app's source tree is pruned and unrecorded; the same app converted `source` → `inline` prunes what the other kind installed; a secret rotated, one dropped, and one re-stored after being removed behind the role's back; a second app claiming a domain another app already routes is refused before it installs anything; an app declaring a secret another app owns is refused and the other app's secret is left untouched; `absent` removes everything the apps owned, secrets included, stops a `source` app's container without being told its unit name, and stays green when repeated |

## Running it

```sh
pip install -r requirements-dev.txt                   # once, into the environment that
                                                      # holds ansible-core; molecule and its
                                                      # podman driver are in there, pinned
molecule test                                         # Fedora
MOLECULE_DISTRO=debian MOLECULE_IMAGE=docker.io/library/debian:13 molecule test
```

One scenario, one platform per run, chosen by environment: `MOLECULE_DISTRO` picks the
`Dockerfile.<distro>.j2` the target is built from and `MOLECULE_IMAGE` the base image, and
CI runs one row per family the role's `meta/main.yml` claims (see
`.github/workflows/molecule.yml`, which is also where the Debian tag is pinned). Unset,
both fall back to Fedora. Switch families with `molecule test` or a `destroy` in between:
the scenario's state is per scenario, not per platform, so a bare `converge` after a switch
would target the container the previous family left running.

`sops` has to be on PATH as well. The scenario's fixture app ships encrypted secrets, and
`community.sops` shells out to the binary to decrypt them; without it the converge fails in
the role's `load_vars` call. See the CI workflow for the pinned release it installs.

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

The base image is pinned to a Fedora release that has actually shipped, not to `latest`
or a higher number. Rawhide is on Python 3.15, which removed `dataclasses._is_type`;
ansible-core patches that function unconditionally as module_utils is imported, so on such
a target every module fails before it runs and reports only `Module result deserialization
failed: No start of json char found`. `prepare.yml` records the target's release and
interpreter through `raw` first thing, so that failure is one line into the log rather than
an afternoon.

Two things worth knowing: the driver rebuilds `molecule_local/<image>` from the family's
Dockerfile on every `create`, so an edit to one is picked up by the next run and the layer
cache is what keeps that quick (`podman rmi` it only when that cache is what you distrust);
and the fixture apps tree is copied into molecule's ephemeral directory by `prepare.yml`,
so a hand-edit under `apps/` takes a `prepare` to reach the target.

### The secrets fixture

`molsecret` is an `inline` app whose whole app directory is one encrypted file, which is
what a real app with secrets and nothing else looks like. Two things about it are worth
knowing before changing it.

The plaintext lives in `molecule.yml`, and `prepare.yml` encrypts it into the *copied* app
tree with `community.sops.sops_encrypt`. Nothing encrypted is under version control: the
values are then readable beside the assertions that check them, and changing one needs no
ciphertext regenerated by hand.

`sops/age-key.txt` is a throwaway age identity, committed deliberately — the role's
secrets path calls sops for real, and sops will not decrypt without one. It protects
nothing, and `build_ignore` drops `extensions` wholesale so it never reaches the built
collection. `molecule.yml` points sops at it through `SOPS_AGE_KEY_FILE`, in process
environment rather than as a task parameter, because the call that needs it is the role's
own and a consumer supplies the key the same way. `prepare.yml` asserts the file is really
there, so a scenario that cannot decrypt says so before the role runs.

The scenario deliberately does not run the fixture containers on a podman network
(`Network=none`, `systemd_app_network: none`): joining one would put netavark in the
critical path of every run without covering anything the role does. Its fixture apps are
copied out of `apps/` into molecule's ephemeral directory first, so `side_effect.yml` can
drop a file from an app's source tree without editing a versioned file.
