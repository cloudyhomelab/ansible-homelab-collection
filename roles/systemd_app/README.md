# systemd_app

Deploys (or decommissions) a **single** app as Podman Quadlet files and systemd units,
plus an optional Caddy route. Invoke it once per app. Two required selectors drive it:

- **`systemd_app_kind`** — `source` or `inline` (see below). **Required, no default.**
- **`systemd_app_state`** — `present` (default) deploys; `absent` decommissions.

## Requirements

**A privileged play.** A role cannot set `become` for the play that includes it, so the caller
does, on the play or on the role entry:

```yaml
- hosts: all
  become: true
  roles:
    - role: systemd_app
```

Without it the run fails part-way through the first install, on a permission error. The role's
own controller-side tasks opt back out with `become: false`.

**Rootful podman, by construction.** Quadlet files go where the *system* generator reads them,
secrets into the host-global root store, units are driven at system scope. Repointing
`systemd_app_system_dir` and `systemd_app_unit_dir` at a user's directories does not make that
rootless: the `podman secret` and `systemctl` calls are still the root ones. A rootless variant
is a different role.

**On the target host:** systemd, and podman 4.5 or newer — Quadlet arrived in 4.4 and 4.5 added
the secret labels this role records ownership with. `systemd_app_health_cmd` needs podman 5.0,
where Quadlet learned `Notify=healthy`. That means any current Fedora, or Debian 13 and later;
Debian 12's podman is 4.3 and has no Quadlet. Fedora 43 and Debian 13 are what the molecule
scenario converges and what `meta/main.yml` claims; others will likely work but are not claimed
until something tests them.

Both install directories must exist already — they are podman's and systemd's, and the role
creates neither. It creates what it owns: `systemd_app_root`, each app's home, and
`systemd_app_caddy_confd` for a routed app.

An app with [private files](#private-files) also needs `age` and `sops` on the target, which is
where they are decrypted; both are packaged by Fedora 43 and Debian 13. The role checks before
copying anything, so a host without them fails clearly rather than with a unit that will not
start.

**On the controller:** no privilege and nothing beyond ansible-core, unless an app ships
encrypted secrets — those need `community.sops`, the `sops` binary and the decryption key.
Private files need none of that here: they are copied still encrypted. Nothing is written on
the controller either way.

## Kinds

### `source` — install from a directory

The app ships a directory on the controller; the role copies its Podman
[Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
files, plain systemd units, and config tree to the host.

```
apps/
  <app>/
    quadlet/            # *.container, *.pod, *.network, *.volume, *.kube, ... (optional)
    unit/               # plain *.service, *.timer, *.socket, ...              (optional)
    config/             # arbitrary tree, copied recursively to the host       (optional)
    private/            # encrypted files, copied to the host as they are      (optional)
    secrets.sops.yaml   # SOPS-encrypted podman secrets                        (optional)
```

An `inline` app has no such directory, except when it needs secrets or private files: then
it holds only those.

Each subdirectory is optional — an app may ship only a Quadlet, only config, etc.

### `inline` — render a single container

For the common one-image-one-container case, the role renders a single
`<name>.container` Quadlet from inline parameters — no source directory needed. Just give
it an image (and usually a domain). The container is named
`<systemd_app_name>`, joins `systemd_app_network`, and is auto-updated unless
`systemd_app_auto_update` says otherwise (see
[rendered container policy](#rendered-container-policy)).

## What it does

### `present` (default)

1. **`inline`**: renders `<name>.container` to `/etc/containers/systemd/`.
   **`source`**: copies `<app>/quadlet/*` there and `<app>/unit/*` to
   `/etc/systemd/system/`, then copies `<app>/config/` to `/var/app/<app>/config/`.
   Either way, every host path installed is recorded in an
   [install manifest](#install-manifest); anything the previous deploy recorded and
   this one no longer installs (a renamed or deleted file, in `config/` as much as in
   `quadlet/`, or a file belonging to the kind the app has just stopped being) is
   removed from the host.
2. When `systemd_app_domain` is set, writes a Caddy route snippet to the imported
   `conf.d/` directory (see [Caddy routing](#caddy-routing)).
3. Runs `systemctl daemon-reload` (once, only if anything changed).
4. Starts and enables the [managed units](#unit-names-and-boot-persistence), then makes
   whatever this deploy changed take effect: a **restart** for a changed unit definition
   or secret, a **reload** for a changed config tree (see
   [Making changes take effect](#making-changes-take-effect)).

### `absent`

1. Stops and disables the managed units, and every unit its
   [install manifest](#install-manifest) implies (a Quadlet service's `ExecStopPost` also
   removes its container). You do **not**
   have to repeat `systemd_app_enable_units` at teardown: the manifest is on the host, so
   the role can work out what the app is running without being told.
2. Removes exactly the paths in the app's [install manifest](#install-manifest) — its
   Quadlet files, plain systemd units and config tree — and the `<name>.caddy` route
   snippet.
3. Removes `/var/app/<app>` entirely: its deployed config and any data a Quadlet
   bind-mounts under it.
4. Removes the app's podman secrets — the ones labelled as its own, so nothing on the
   controller is needed and another app's are never touched.
5. Runs `systemctl daemon-reload`.

> **`absent` is destructive and not reversible. Back the app up before setting it.**
> Whatever is under `/var/app/<app>` goes, including anything the container wrote
> there. The route stops resolving once Caddy is reloaded, which your play does, not
> this role.
> After a decommission has run on the host, delete the role call.

Two things `absent` does **not** remove, because they outlive the units that declared
them: **Podman named volumes** and **networks**. Removing the `.volume`/`.network` Quadlet
file does not delete the volume or network it created. Clear those by hand with
`podman volume rm` / `podman network rm` once you are sure — and look at what is in them
first: a volume holding issued TLS certificates costs a re-issuance, which ACME CAs
rate-limit.

## Role parameters

| Param                      | Required        | Purpose                                                       |
| -------------------------- | --------------- | ------------------------------------------------------------- |
| `systemd_app_kind`         | yes             | `source` (files from a dir) or `inline` (rendered container). |
| `systemd_app_name`         | yes             | `source`: app dir name. `inline`: container name / DNS name.  |
| `systemd_app_state`        | no              | `present` (default) deploys; `absent` decommissions.          |
| `systemd_app_enable_units` | no              | systemd unit names to enable and start (see below).           |
| `systemd_app_domain`       | no              | Public hostname; when set, a Caddy route is added.            |
| `systemd_app_upstream`     | no              | Upstream container name to proxy to (default `systemd_app_name`). |
| `systemd_app_port`         | no              | Upstream port for the Caddy route (default `8080`).           |
| `systemd_app_data_dirs`    | no              | Bind-mount dirs to pre-create with a given owner (see below). |

### `inline`-kind parameters

| Param                       | Required             | Purpose                                            |
| --------------------------- | -------------------- | -------------------------------------------------- |
| `systemd_app_image`         | yes (inline+present) | Full image ref, e.g. `docker.io/org/app:latest`.   |
| `systemd_app_description`   | no                   | Unit description (default `<name> container`).     |
| `systemd_app_network`       | no                   | Network the container joins (default `web.network`). |
| `systemd_app_env`           | no                   | Env vars rendered as `Environment=` lines.         |
| `systemd_app_volumes`       | no                   | Raw `Volume=` values.                              |
| `systemd_app_publish_ports` | no                   | Raw `PublishPort=` values.                         |
| `systemd_app_container_options` | no               | Raw lines for the `[Container]` section.           |
| `systemd_app_service_options` | no                 | Raw lines for the `[Service]` section.            |
| `systemd_app_auto_update`   | no                   | `AutoUpdate=` policy: `registry` (default), `local` or `never`. |
| `systemd_app_restart`       | no                   | `Restart=` policy (default `always`); quote `'no'`. |
| `systemd_app_health_cmd`    | no                   | Probe command; enables the health block (see below). |

`systemd_app_env` values are quoted and escaped into the unit, so a value with spaces, a
`"` or a `%` needs nothing special at the call site (systemd splits `Environment=` on
whitespace and reads `%` as a specifier, so a bare value would otherwise be truncated or
mangled). Keys have to spell legal variable names — letters, digits and underscore, no
leading digit. A control character is refused rather than written, in any value the unit
is rendered from — the env values, the description, the raw-line lists, the image, the
network, the two policy values and the health settings alike: a newline ends the line and turns whatever follows
into another unit directive, and no quoting fixes that. The raw-line parameters are one Quadlet
line per list entry, which is why an entry may not contain a newline of its own.

## Tunables (defaults)

| Variable                  | Default                      | Purpose                                  |
| ------------------------- | ---------------------------- | ---------------------------------------- |
| `systemd_app_apps_dir`    | none (required for `source`) | Source of `source`-kind app definitions. |
| `systemd_app_system_dir`  | `/etc/containers/systemd`    | Quadlet install dir on the host.         |
| `systemd_app_unit_dir`    | `/etc/systemd/system`        | Plain-unit install dir on the host.      |
| `systemd_app_root`        | `/var/app`                   | Every app's home → `<root>/<app>`.       |
| `systemd_app_caddy_confd` | `{{ systemd_app_root }}/reverse_proxy/config/conf.d` | Dir for generated route snippets. |

`systemd_app_apps_dir` has no default — where a fleet keeps its app definitions is a
property of that repository, not of this role — and deploying a `source` app fails the run
without it. Set it once as a play variable, since every app in a play reads the same tree:

```yaml
  vars:
    systemd_app_apps_dir: "{{ playbook_dir }}/../apps"
```

A `source` app whose directory is not under it fails the run too, before anything is
installed, rather than being treated as an app that ships no files: a deploy that read
nothing would install nothing, report success, and prune every file the last one recorded
(see [install manifest](#install-manifest)).

An `inline` app needs the variable only to be found by the secrets lookup below; without
it that lookup is skipped, and the app is deployed as one that ships no secrets. A
decommission of either kind never reads it: it works from the host alone.

## Install manifest

A deploy records the host paths it installed to `/var/app/<app>/.install-manifest`, one per
line: a `source` app's Quadlet files, units and config tree, an `inline` app's one rendered
`<name>.container`, and for either kind its encrypted [private files](#private-files) and the
drop-ins written for them. It lives in the app's home so the two share fate — a hand-removed
or restored `/var/app/<app>` cannot leave a stale record behind. A later deploy and a
decommission both work from it rather than re-deriving from `apps/<app>/`, which by then may
name different files or be gone.

Reading, pruning and recording are one call of the `install_manifest` module, on the host.
Acted on as root, so every line is checked first: a single path segment
directly inside `systemd_app_system_dir` / `systemd_app_unit_dir`, a `<unit>.d/<name>.conf`
drop-in inside `systemd_app_unit_dir`, or a path under this app's own
`/var/app/<app>/config` or `/var/app/<app>/private` with no empty, `.` or `..` segment, and
a regular file, symlink or missing path — never a directory, since nothing recorded is
removed recursively. One illegal line fails the run without deleting anything.

`absent` has to read it: the Quadlet files and units it removes live in shared directories
systemd and the generator will not read anywhere else, so dropping `/var/app/<app>` alone would
leave them and the generator would recreate the service on the next `daemon-reload`. It asks
the module in check mode which units the record implies, stops those, then calls it to
remove.

**Changing an app's kind converges on it.** Both kinds record and reconcile, so flipping
`systemd_app_kind` while keeping the name prunes whatever the old kind installed and the new
one does not. The one thing left by hand is the running units: a pruned unit file keeps running
until stopped or the host reboots.

Why a manifest and not a destination diff: a deployed config tree can hold files no source tree
contains. `systemd_app_caddy_confd` sits inside the reverse proxy's config tree and holds a
snippet for every *other* routed app, so deleting whatever is not in `<app>/config/` would wipe
all of them every converge.

Only files are recorded, but a directory a prune empties goes too, upwards until one still
holds something. `rmdir` refuses a non-empty directory, so a `<unit>.d/` with a hand-written
override beside the role's own survives by construction.

An app last deployed before the role recorded a manifest for its kind has none, so its first
converge prunes nothing and records one. `absent` covers that gap for an `inline` app by also
removing the rendered `<name>.container` by name.

## Caddy routing

Set `systemd_app_domain` to expose the app through the reverse proxy without editing the
central `Caddyfile`. The role drops a `<systemd_app_name>.caddy` snippet
(`domain → upstream:port`) into `systemd_app_caddy_confd`, which the Caddyfile is expected
to import (`import conf.d/*.caddy`, however that directory reaches Caddy). The role never
reloads Caddy itself: do that once in your play after every app has converged, rather than
cycling the proxy once per routed app.

`systemd_app_upstream` defaults to `systemd_app_name` — correct for every `inline`
app (the container *is* `<name>`) and for `source` apps whose `ContainerName=`
matches the app name. Override it when a `source` app's routable container is
named differently. On `absent`, the role removes the `<name>.caddy` snippet it
generated.

The three values that compose the snippet are checked before it is written:
`systemd_app_domain` must be a hostname of at least two labels, optionally wildcarded
(`*.example.com`); `systemd_app_upstream` a container name; `systemd_app_port` 1-65535.
The check is there because the failure is not local — the domain becomes the address of a
site block, and the Caddyfile imports *every* app's snippet, so one value carrying a brace,
a comment character or a newline stops Caddy loading any route at all. A typo at the call
site is a failed run instead.

One route problem no check on the values can see: two apps given the same
`systemd_app_domain`. Their snippets would hold two site blocks with one address, which
Caddy refuses as an ambiguous site definition — again for the whole imported config. The
role sees one app at a time, so before it writes a route it looks through
`systemd_app_caddy_confd` for any *other* `.caddy` file whose site block opens with this
domain, and fails the run naming that file if it finds one, before anything is installed.
The match is the shape this role writes — the domain alone at the start of a line — so a
hand-written snippet listing several addresses on one line is outside it. `caddy validate`
before the reload in your play is the backstop for that, and worth having anyway.

## Making changes take effect

Installing a file is not the same as the app running from it.

**A changed unit definition or secret needs a new container.** `daemon-reload` rewrites the
`.service`, but systemd does not act on a unit it merely re-read, and podman reads a secret
only at container creation, so the running one carries on with what it started with. The role
**restarts** the managed units when this deploy changed a file that defines them.

**A changed config tree only needs the process told.** The files are bind-mounted and already
in place, so the role **reloads** instead: a unit with `ExecReload=` takes new config without
dropping what it is serving. One reporting `CanReload=no` is restarted instead.

A restart supersedes a reload, and a converge that changed neither leaves the units alone.

Three consequences worth knowing:

- **A restart does not pull.** The pull policy decides that, and the default fetches only an
  image missing locally. A new tag or digest is missing by definition; a moving tag like
  `:latest` is not, and needs `podman auto-update` or `Pull=newer`.
- **Removed files are treated by kind.** A pruned config file is a config change, since the app
  is still reading what is gone. A pruned unit file is not — it leaves nothing to act on, and
  the unit keeps running until stopped by hand.
- **A changed private file is a restart, not a reload.** Its plaintext lives in a
  `RuntimeDirectory` the decrypt unit destroys and re-creates, so a reload would leave the
  container on a directory that is gone. The decrypt instance is restarted first, the app
  after (see [private files](#private-files)).

Generated route snippets are outside this entirely; the play applies those centrally once every
app has converged (see [Caddy routing](#caddy-routing)).

## Dry runs (`--check`)

A dry run reports what a deploy would change without touching the host. Every task here is
`file`, `copy`, `template`, `systemd` or one of the collection's two modules, and both
modules support check mode: `install_manifest` reports the paths it would prune and record
without writing them, and `podman_secrets` lists and inspects the store — reads — then
reports the secrets it would create, rotate or drop without running `podman secret create`.
So `--check` previews the installed files, the manifest, the secret store, and the restart
or reload that follows from them.

Two limits:

- Whether `podman secret create` would succeed is only knowable by running it.
- A first deploy cannot be previewed to the end. `--check` writes no Quadlet file, so the
  generator never makes the service from it, so the task that starts that service fails:
  the systemd module refuses a unit the host does not have, in check mode as in a real
  run. Dry-running an app that is already deployed is fine, its units being there from
  last time.

Nothing in the gates runs the role under `--check`; the molecule scenario converges for
real.

## Rendered container policy

Beyond the parameters above, an `inline` container is rendered with directives that are
fleet policy rather than mechanism — the defaults for a long-running service, not something
the role needs in order to work. Two have a parameter, with today's rendering as its default;
the rest are fixed:

| Directive                                  | Section       | Why                                                                                                                                                                       |
| ------------------------------------------ | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AutoUpdate=registry`                       | `[Container]` | Enrols the container in `podman auto-update`, so a moving tag is pulled and the app restarted on it without a deploy. Only useful together with the healthcheck below, which is what makes a bad image roll back. `systemd_app_auto_update`: `local` follows a locally built image; `never` writes no line at all, which is what an image pinned by digest wants — it has nothing to follow. |
| `Restart=always`                            | `[Service]`   | The app is a service: it is expected to stay up, and any exit is a fault to recover from rather than a result. `systemd_app_restart` takes any value systemd's `Restart=` does; `on-failure` fits an app whose clean exit means it is done. Quote `'no'`, which YAML otherwise reads as a boolean. |
| `Notify=healthy`, `TimeoutStartSec=`        | both          | Emitted only when `systemd_app_health_cmd` is set; see below.                                                                                                              |
| `After=`, `Wants=network-online.target`     | `[Unit]`      | Starts after the network is up, which is what a container that pulls its image or serves a route needs.                                                                  |
| `WantedBy=multi-user.target default.target` | `[Install]`   | Starts at boot. Both targets are named so the app comes up whether or not the host's `default.target` is `multi-user.target`.                                               |

Both parameters are checked against a closed list of values in the argument spec, because
the target does not reject a typo loudly: Quadlet copies any `AutoUpdate=` value into the
container's label unread and `podman auto-update` complains only when it next runs, and
systemd logs a warning for an unknown `Restart=` value and runs the unit with no restart
at all.

A raw line in `systemd_app_container_options` or `systemd_app_service_options` can still
carry either key. Quadlet's parser reads a single-valued key through a lookup that returns
the *last* instance of the key in its section, so a raw `AutoUpdate=` or `Restart=` line —
appended after the rendered one — wins. The molecule scenario observes this on a host,
through the label the container ends up with. List-valued keys (`Volume=`, `After=`,
`Wants=`, `Environment=`, `PublishPort=`) behave differently: every instance counts, so a
raw line accumulates beside the rendered ones rather than replacing them.

`[Unit]` and `[Install]` have no parameter and no raw-line list. An `inline` app is the
role's shape for a service that starts at boot, after the network; an app that wants a
different dependency ordering or boot target is not that shape, and is a `source` app
writing its own Quadlet — as is one that wants none of this at all.

## Healthchecks and auto-update rollback

`podman auto-update` reverts an image only when the unit **fails to start**. Without a
healthcheck a container that boots and then serves errors counts as started, so the bad
image stays. `Notify=healthy` closes that gap: systemd withholds "started" until the
first probe passes, which turns a broken image into a failed start that gets rolled back.

For an `inline` app, set `systemd_app_health_cmd` and the role emits the whole block —
`HealthCmd`, `HealthInterval`, `HealthRetries`, `HealthStartPeriod`, `Notify=healthy`,
and a matching `TimeoutStartSec`. Only the command is per-app, because nothing else can
be: an HTTP app wants a request, Postgres wants `pg_isready`. Everything around it is
fleet policy in `defaults/main.yml` and rarely needs overriding.

```yaml
    - role: systemd_app
      systemd_app_kind: inline
      systemd_app_name: myapp
      systemd_app_health_cmd: "wget -qO /dev/null http://127.0.0.1:8080/ || exit 1"
```

Two things to know. The command runs **inside** the container, so the tool has to exist
in the image — check with `podman exec <name> sh -lc 'command -v wget curl'` before
relying on it, because a probe that can never pass makes the deploy itself fail. And
address the app as `127.0.0.1`, not `localhost`, which resolves to `::1` on musl images
where nothing is listening. Leave `systemd_app_health_cmd` empty for an app that cannot
be probed; it then gets no health block and no rollback protection.

`source` apps declare these keys in their own Quadlet files, so this policy does not
reach them.

## Secrets

`systemd_app_env` renders `Environment=` lines into a unit file, which is world-readable:
fine for a hostname, wrong for a client secret. An app that needs secrets ships one
SOPS-encrypted file in its own directory, keyed by the podman secret names:

```yaml
# apps/myapp/secrets.sops.yaml   (values encrypted; keys readable)
myapp-oidc-issuer-uri: https://accounts.example.com
myapp-oidc-client-id: …
myapp-oidc-client-secret: …
```

Nothing at the call site, for either kind: the role finds the file the way it finds
`quadlet/` and `config/`, decrypts it on the controller, and stores each entry in podman's
secret store. It sits at the app root, outside the directories the role installs from, so
nothing copies it to the host.

How the container reaches a stored value differs by kind:

**`inline`** — the role renders the reference, deriving the variable from the name:
upper-cased, dashes as underscores. The file above yields

```ini
Secret=myapp-oidc-client-secret,type=env,target=MYAPP_OIDC_CLIENT_SECRET
```

so the name has to be the variable the app reads, lower-cased with dashes. Rarely a
constraint: podman secret names are host-global and want an app prefix anyway. An image that
insists on a bare name (`POSTGRES_PASSWORD`) means either a host-global secret called
`postgres-password` or the `source` kind.

**`source`** — the app writes the line itself and can point any name at any variable:

```ini
# apps/myapp/quadlet/myapp.container
Secret=myapp-oidc-client-secret,type=env,target=MYAPP_OIDC_CLIENT_SECRET
```

The name is the whole contract between file and Quadlet: rename it in one and the container
fails to start on a name that no longer exists.

Values are loaded into one dict, never top-level variables, and handed to podman on **stdin**,
never a command line, `/proc/<pid>/cmdline` being world-readable.

**What this buys.** The value still reaches the container's environment, so it is readable by
the process, by root, and in `podman inspect`. What it avoids is a copy in a `0644` file under
`/etc/containers/systemd/`, in a config tree, or in git. Podman's file driver keeps the store
in a root-only unencrypted file, so "root on the host" is the trust boundary either way.

**Rotation, renames and ownership.** Each stored secret carries two labels: its owner
(`io.binarycodes.homelab.app`) and a digest of its value (`io.binarycodes.homelab.digest`,
`sha256:<hex>`, the algorithm named so it can change without a rotation). The store is the
only record. Podman reads a secret at container creation and cannot update one in place, so
each deploy compares declared values against those labels: nothing changed touches nothing; a
changed value is removed and re-created and the app **restarted**; a name dropped from the
file is removed, as the [install manifest](#install-manifest) does for files. One removed by
hand simply comes back.

The owner label is what `absent` removes by, so a decommission needs nothing from the
controller, and what keeps two apps apart: declaring a name another app owns is refused,
naming that app, and the other secret is untouched. One with no owner label — stored by hand,
or before 1.1.0 — is adopted and re-created with labels, which costs one restart.

A failed `podman secret create` is reported with the name, exit code and stderr; the value is
`no_log`.

**Requirements.** The controller needs `sops`, the `community.sops` collection and the
decryption key; without the key the run fails at the decrypting task, before anything on the
host changes. How the key gets there is outside this role — sops finds it the usual ways, and
`.sops.yaml` decides which keys encrypt which files.

## Private files

Secrets above are the environment-shaped channel. An app that needs a *file* at a path its
image chose — a TLS key, a service-account JSON, a `.env` read at startup — ships it in its
own `private/` directory, encrypted:

```
apps/myapp/private/
  db.env.age              # raw age
  tls/server.sops.yaml    # SOPS, decrypting to tls/server.yaml
  ca.crt                  # neither; copied through unchanged
```

`.age` means raw age, a `.sops.<ext>` component means SOPS, and the marker is dropped from the
decrypted name. Anything else is copied through, so a public certificate can sit beside its
key. `private/` is a tree, like `config/`, and subdirectories are mirrored. Two files that
would decrypt to one name fail the play, named, before anything is installed.

**Nothing readable is written to disk.** The tree is copied to `{{ systemd_app_home }}/private`
**still encrypted**, so a backup of `systemd_app_root` carries no plaintext and the controller
never sees the key. Decryption happens on the host at unit start:

```
apps/myapp/private/db.env.age             (controller, encrypted)
  -> /var/app/myapp/private/db.env.age    (host, still encrypted, 0600)
     -> /run/app/myapp/private/db.env     (tmpfs 0700, file 0600, gone when the unit stops)
```

`homelab-private-decrypt@<app>.service` does it — one template unit shared by every app on the
host, installed by whichever app needs it first. The role writes a drop-in for **every unit the
app installs**, so no app author has to remember the dependency and an `inline` app, which has
no `[Unit]` escape hatch, gets it too:

```ini
# /etc/systemd/system/myapp.service.d/10-private.conf, written by the role
[Unit]
After=homelab-private-decrypt@myapp.service
Requires=homelab-private-decrypt@myapp.service
```

`Requires=`, not `Wants=`: missing files are a wrong start, not a degraded one. If the decrypt
fails — no key, a key that does not match, `age` or `sops` missing — the app does not start.

**Mounting it.** The role renders no `Volume=` of its own, for either kind: an image wants its
file at a path only the app knows. `systemd_app_private_run_dir` saves repeating the host side.

```yaml
# inline
systemd_app_volumes:
  - "{{ systemd_app_private_run_dir }}/tls.key:/etc/myapp/tls.key:ro"
```

```ini
# source, in the app's own quadlet/myapp.container
Volume=/run/app/myapp/private/tls.key:/etc/myapp/tls.key:ro
```

**The key.** Read as a systemd credential from `/etc/homelab/age.key`, so it reaches only that
unit's processes and lives on ramfs. The role never provisions it; a host keeping it elsewhere
symlinks it into place. Fixed rather than a parameter: one unit file serves every app, so a
per-app value would have the last app deployed rewrite it for all of them.

**On decommission** the app's instance is stopped, taking its tmpfs tree with it, and its
copies and drop-ins go with everything else it installed. The shared unit and helper are left
behind: every app writes them identically and shares them. Same call as podman networks and
named volumes.

## Pre-created data directories

A container that writes to a bind mount needs the host directory to exist with the right
owner first: podman creates a missing path as `root:root`, and an image running as a
non-root user then cannot write in it. `systemd_app_data_dirs` creates them before the
container starts.

```yaml
      systemd_app_data_dirs:
        # Relative to /var/app/<app>. 10001 is the uid the image runs as; if upstream
        # changes it, this must follow or the app starts and fails to write.
        - path: data
          owner: "10001"
          group: "10001"
          mode: "0700"
      systemd_app_volumes:
        - "{{ systemd_app_home }}/data:/app/data"
```

A `source` app declares `systemd_app_data_dirs` the same way and writes the matching
`Volume=` line in its own Quadlet, where the host path has to be spelled out in full
(`/var/app/<app>/data:/app/data`) — a static file cannot reference the role's variables.

Paths are relative to the app's own home, so they cannot name another app's: absolute
paths and `..` are refused, since the role creates these as root and `absent` deletes the
tree they live in. Which is the
trade against a named volume: a bind mount here is backed up and restored with the rest
of `/var/app/<app>`, and **destroyed with it** on decommission, where a named volume
would survive.

## Unit names and boot persistence

The role starts/enables its **managed units**: `systemd_app_enable_units` if you
list any, otherwise — for an `inline` app — the single generated `<name>.service`.
A `source` app that lists none starts nothing and relies entirely on its
`[Install]` section — an app shipping only a `.network` Quadlet, say.

`systemd_app_enable_units` takes the **generated** service name (the `.service`
suffix is optional):

| Quadlet file      | Generated unit         |
| ----------------- | ---------------------- |
| `foo.container`   | `foo.service`          |
| `foo.pod`         | `foo-pod.service`      |
| `foo.network`     | `foo-network.service`  |
| `foo.volume`      | `foo-volume.service`   |

At teardown the role does not depend on this list. `absent` stops the managed units
*and* whatever the recorded manifest implies: a `.container` or `.kube` file is stopped as
`<name>.service`, a `.pod` as `<name>-pod.service`, and a plain unit file as itself. So a
`source` app deployed with `systemd_app_enable_units` is stopped by an `absent` call that
omits it — otherwise its unit files would be deleted while its containers kept running,
the generated service gone from systemd's view and its `ExecStopPost` never fired.

`.network` and `.volume` units are deliberately not stopped: they create a resource rather
than run a container, and the role leaves those resources behind on purpose (see the note
in the `absent` list above).

Quadlet-generated services live under `/run` and cannot be `systemctl enable`d
directly. The role tolerates that specific failure and relies on an `[Install]`
section in the Quadlet (e.g. `WantedBy=multi-user.target`) for boot startup.
Plain units in `unit/` are enabled normally, and disabled again before their file is
pruned or the app decommissioned, so no `.wants` symlink outlives the unit it names.

## Examples

A `source` app with a Caddy route:

```yaml
- hosts: all
  become: true

  vars:
    # Where this project keeps its app definitions; every app in the play reads the
    # same tree, and a 'source' app fails the run without it.
    systemd_app_apps_dir: "{{ playbook_dir }}/../apps"

  roles:
    - role: systemd_app
      systemd_app_kind: source
      systemd_app_name: myapp
      systemd_app_enable_units:
        - myapp.service
      # Optional: route app.example.com -> myapp:8080 via Caddy.
      systemd_app_domain: app.example.com
```

An `inline` app — one image, no source directory:

```yaml
    - role: systemd_app
      systemd_app_kind: inline
      systemd_app_name: myapi
      systemd_app_image: docker.io/org/myapi:latest
      systemd_app_port: 8080
      systemd_app_domain: api.example.com
```

Decommission an app (leave the call in place for one converge, then delete it).
This destroys `/var/app/<app>` — back it up first:

```yaml
    - role: systemd_app
      systemd_app_state: absent
      systemd_app_kind: inline
      systemd_app_name: oldapp
```

## Where the logic lives

The role's computation is Python, not Jinja: filters for the controller, modules for the host.
All ship with this collection and are called by FQCN.

| Plugin               | Kind   | Used for                                                          |
| -------------------- | ------ | ----------------------------------------------------------------- |
| `podman_secrets`     | module | Reconciling the app's podman secrets against the store, on the host. |
| `install_manifest`   | module | Reading, pruning and recording the install manifest, on the host; on `absent`, the units it implies. |
| `source_tree`        | filter | Reading what a `source` app ships from its directory, and the host paths it installs to. |
| `private_tree`       | filter | Reading what an app keeps encrypted in `private/`, where it is copied, and what it decrypts to. |
| `unit_names`         | filter | The units a set of installed paths implies, for the drop-ins that order them after the decrypt. |
| `app_validation_errors`       | filter | Checking `systemd_app_name`, `_kind`, `_state`, what each kind requires (a `source` app's directory included), `_data_dirs`, and the secret names. |
| `route_validation_errors`     | filter | Checking `systemd_app_domain` / `_upstream` / `_port`.             |
| `container_validation_errors` | filter | Checking what would be interpolated into a rendered Quadlet.      |
| `systemd_env_lines`  | filter | Quoting and escaping `systemd_app_env` into `Environment=` lines.  |

One file each, and Python so they can be tested as Python: a table of cases in under a second
rather than a playbook run per case (`tests/unit/`). The `*_validation_errors` filters return a
list and never raise, so one run reports everything wrong at once. The secrets module keeps
every podman call behind one runner and is tested against a fake store; the manifest module
against a temporary directory. A change to what the role accepts, or to what the store or the
manifest should hold, belongs there rather than in a YAML scalar.

They are collection-global public API, named for what they compute rather than for the role
that calls them. `secret_digests` and `reconcile_secrets` are deprecated and go in 2.0.0, the
secrets module doing their work from labels. `manifest_units` was renamed `unit_names` in
1.2.0, when the role began asking it what a deploy is *about* to install; the old name resolves
with a warning until 2.0.0.
