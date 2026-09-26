# `systemd_app` molecule scenario

Runs the `systemd_app` role against a throwaway container with systemd as PID 1 and podman
inside it, then asserts the host state. `tests/unit/` covers the arithmetic; this covers what
only a real host shows.

## What is tested

Sequence: `destroy → create → prepare → converge → idempotence → verify → side_effect →
destroy`. `verify` before `side_effect`, because it asserts what the converge left and
`side_effect` then mutates that stage by stage.

| Premise | Asserted in | Fixture |
| --- | --- | --- |
| A `source` app's tree lands as shipped: Quadlet, plain unit, nested config | `verify` | `molsource` |
| An `inline` app renders from call-site parameters; `Environment=` quoting is `systemd_env_lines`'; no health block unless asked | `verify` | `molinline` |
| The install manifest records exactly the paths the app installed, for either kind | `verify` | `molsource`, `molinline` |
| A route snippet names the domain and `<app>:8080`; another app on the same domain is refused before anything is installed, naming the snippet that holds it | `verify`, `side_effect` | `molinline`, `molclash` |
| Declared data directories exist with the container user's ownership before podman could create them as root | `verify` | `molinline` |
| Secrets are decrypted by sops and stored under their own names, labelled with owner and digest, with no record file beside them; the Quadlet references them by name and the container receives them as variables | `verify` | `molsecret` |
| Every managed unit is active, and a plain unit is enabled at boot | `verify` | all |
| A second converge changes nothing | `idempotence` | all |
| A file dropped from the source tree is removed from the host and the manifest; the app stays up | `side_effect` | `molsource` |
| `private/` lands on the host still encrypted, decrypts into `/run/app/<app>/private` under the name its suffix promises, mirrors subdirectories, keeps a plaintext member as it is, and is 0600 in a 0700 tree | `verify` | `molsource`, `molinline` |
| Every unit an app installs gets a drop-in ordering it after that app's own decrypt instance, for either kind | `verify` | `molsource`, `molinline` |
| A call site mounts a decrypted file where its image wants it, and the container reads it | `verify` | `molinline` |
| An app whose call names no units is deployed but not started, and its decrypt instance is left inactive, so nothing is decrypted for an app that is not running | `verify` | `molquiet` |
| A private file changed under an app started by hand reaches its container: the role restarts only the decrypt, and `PartOf=` carries that to a unit the role never names | `side_effect` | `molquiet` |
| A renamed private file leaves both the host copy and the old decrypted name; dropping `private/` prunes the copies, the drop-ins and the directories they emptied, and stops the app's decrypt instance without disturbing another app's | `side_effect` | `molsource`, `molinline` |
| The shared decrypt unit and its helper survive every app being decommissioned | `side_effect` | all |
| Converting `source` → `inline` prunes what the other kind installed, disables the pruned plain unit before its file goes, and restarts from the rendered Quadlet | `side_effect` | `molsource` |
| A changed secret is removed and re-created; a dropped one leaves the store; one removed behind the role's back comes back | `side_effect` | `molsecret` |
| A secret another app owns is refused by name and owner, and left untouched | `side_effect` | `molclaim` |
| `absent` removes everything the apps owned, secrets included, stops a `source` app's container without being told its unit name, leaves no dangling `.wants` symlink, spares another app's secret, and stays green when repeated | `side_effect` | all |

Fixtures under `apps/` are `molnet` (a network unit, installed but never joined:
`Network=none` throughout keeps netavark out of every run), `molsource`, `molorphan`
(deployed with `systemd_app_enable_units`, decommissioned without) and `molquiet` (deployed
with none, so the role starts nothing for it and `side_effect.yml` starts it by hand).
`molinline`, `molsecret`, `molclaim` and `molclash` exist only as role calls; `molsecret`'s
single encrypted file and every app's encrypted `private/` files are written by `prepare.yml`.
The one committed member of a `private/` tree is `molsource/private/ca.crt`, which is
encrypted with neither tool and so is copied straight through.

`molquiet`'s file is kept out of `molecule_private_files`, which `verify.yml` reads out of
`/run`: the assertion about that app is that nothing of its is there at all.

## Running it

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install ansible-core -r requirements-dev.txt   # molecule and its podman driver, pinned
molecule test                               # Fedora
MOLECULE_DISTRO=debian MOLECULE_IMAGE=docker.io/library/debian:13 molecule test
```

- **Layout.** The collection root must sit at `ansible_collections/binarycodes/homelab/`,
  with the directory above `ansible_collections` on `ANSIBLE_COLLECTIONS_PATH`.
  `converge.yml` calls the role by FQCN and nothing installs the collection for it.
- **Platform.** `MOLECULE_DISTRO` picks `Dockerfile.<distro>.j2`, `MOLECULE_IMAGE` the base
  it builds from; unset, both mean Fedora. CI runs one row per family
  `roles/systemd_app/meta/main.yml` claims (`.github/workflows/molecule.yml`, where the
  Debian tag is pinned). The state file is per scenario: `destroy` before switching
  families, or a bare `converge` skips `create` and targets a container that does not exist.
- **sops and age** must be on PATH. `prepare.yml` encrypts the fixture secrets and private
  files with them, and copies both binaries into the target, which is where the role's own
  decrypt unit runs them — neither distribution packages sops, and one copy keeps the two
  ends on a single version. The CI workflow pins the sops release it installs and takes age
  from the runner's apt.
- **Rootful podman.** If `create` fails on the privileged container, run under `sudo`, naming
  both collection roots since root's search path holds neither:

  ```sh
  sudo env "PATH=$PATH" \
    ANSIBLE_COLLECTIONS_PATH="/path/above/ansible_collections:$HOME/.ansible/collections" \
    molecule test
  ```

- **Iterating.** `converge` is the loop; `verify` and `side-effect` assert against what it
  left, `login` opens a shell, `test --destroy=never` keeps a failed container to inspect.
  The driver rebuilds `molecule_local/<image>` on every `create`, so a Dockerfile edit is
  picked up next run; `apps/` is copied by `prepare.yml`, so an edit there needs a `prepare`.

## Notes

- **Secrets fixture.** Plaintext in `molecule.yml`, encrypted into the copied tree by
  `prepare.yml`, so nothing encrypted is versioned. `sops/age-key.txt` is a throwaway identity
  committed on purpose; `build_ignore` keeps `extensions` out of the built collection.
- **Private-files fixture.** Same argument as the secrets: plaintext in `molecule.yml`
  (`molecule_private_files`), each entry naming the tool its file name implies and what it
  decrypts to, so `verify.yml` asserts against the intent and not the rule under test.
  `prepare.yml` also plays the fleet, installing the age identity at `/etc/homelab/age.key`,
  and shares the container's mount propagation, without which a unit's `LoadCredential=`
  store never leaves the namespace systemd builds it in and the decrypt finds no identity.
- **Secret assertions** go through `filter_plugins/`: `secret_state` reads
  `podman secret inspect --showsecret` into `{name: {owner, digest, value}}`,
  `declared_secret_state` builds the same from the plaintext with the module's own `digest()`,
  and each check is one equality. `tests/unit/test_molecule_filters.py` covers them.
