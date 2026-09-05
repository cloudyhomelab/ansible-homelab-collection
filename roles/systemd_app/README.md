# systemd_app

Deploys (or decommissions) a **single** app as Podman Quadlet files and systemd units,
plus an optional Caddy route. Invoke it once per app. Two required selectors drive it:

- **`systemd_app_kind`** — `source` or `inline` (see below). **Required, no default.**
- **`systemd_app_state`** — `present` (default) deploys; `absent` decommissions.

## Requirements

**A privileged play.** Every task that touches the host writes root-owned files, calls
`podman` against the root store, or drives system units — and a role cannot set `become`
for the play that includes it, so the caller does, on the play or on the role entry:

```yaml
- hosts: all
  become: true
  roles:
    - role: systemd_app
```

Without it the run fails part-way through the first install, on a permission error, rather
than before it starts. The role's own controller-side tasks opt back out with
`become: false`: they only stat and glob the app definitions where those live, and root on
the controller is neither needed nor wanted.

**Rootful podman, by construction.** Quadlet files land in `systemd_app_system_dir`, which
the *system* generator reads; podman secrets are created in the host-global root store;
installed files are owned by `systemd_app_owner`, `root` unless told otherwise; and units
are started, enabled and reloaded at system scope. Repointing `systemd_app_system_dir` and
`systemd_app_unit_dir` at a user's own Quadlet and unit directories does not make any of
that rootless — the `podman secret` and `systemctl` calls would still be the root ones. A
rootless variant is a different role, not a different set of paths.

**On the target host:** systemd, and podman 4.5 or newer — Quadlet arrived in 4.4, and
4.5 added the labels on secrets this role records ownership with (see [secrets](#secrets)).
An `inline` app that sets `systemd_app_health_cmd` needs podman 5.0, where Quadlet learned
`Notify=healthy` — the directive that makes a failing probe a failed start (see
[healthchecks](#healthchecks-and-auto-update-rollback)). In distribution terms that is any
current Fedora, or Debian 13 and later — Debian 12's podman is 4.3 and has no Quadlet.
Fedora 43 and Debian 13 are the two the molecule scenario converges, and the platform list
in `meta/main.yml` names exactly those. Nothing in the role is distribution-specific, so
others will likely work, but they are not claimed until something tests them. Both install
directories have to exist already: the role installs into `systemd_app_system_dir` and
`systemd_app_unit_dir` but creates neither, one being podman's own directory and the other
systemd's. What it does create is what it owns — `systemd_app_root` and each app's home
below it, and `systemd_app_caddy_confd` for a routed app.

**On the controller:** no privilege, and nothing beyond ansible-core — unless an app ships
encrypted secrets, which need the `community.sops` collection, the `sops` binary, and a key
that can decrypt the file (see [secrets](#secrets)). Nothing is written on the controller
either way; the app definitions are only read.

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
    secrets.sops.yaml   # SOPS-encrypted podman secrets                        (optional)
```

An `inline` app has no such directory, except when it needs secrets: then it holds that one
file and nothing else.

Each subdirectory is optional — an app may ship only a Quadlet, only config, etc.

### `inline` — render a single container

For the common one-image-one-container case, the role renders a single
`<name>.container` Quadlet from inline parameters — no source directory needed. Just give
it an image (and usually a domain). The container is named
`<systemd_app_name>`, joins `systemd_app_network`, and is auto-updated
(`AutoUpdate=registry`, see [rendered container policy](#rendered-container-policy)).

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

1. Stops the managed units, and every unit its [install manifest](#install-manifest)
   implies (a Quadlet service's `ExecStopPost` also removes its container). You do **not**
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
| `systemd_app_health_cmd`    | no                   | Probe command; enables the health block (see below). |

`systemd_app_env` values are quoted and escaped into the unit, so a value with spaces, a
`"` or a `%` needs nothing special at the call site (systemd splits `Environment=` on
whitespace and reads `%` as a specifier, so a bare value would otherwise be truncated or
mangled). Keys have to spell legal variable names — letters, digits and underscore, no
leading digit. A control character is refused rather than written, in any value the unit
is rendered from — the env values, the description, the raw-line lists, the image, the
network and the health settings alike: a newline ends the line and turns whatever follows
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
property of that repository, not of this role — and a `source` app fails the run without
it. Set it once as a play variable, since every app in a play reads the same tree:

```yaml
  vars:
    systemd_app_apps_dir: "{{ playbook_dir }}/../apps"
```

A `source` app whose directory is not under it fails the run too, before anything is
installed, rather than being treated as an app that ships no files: the lookups that read
the directory are globs and return nothing for a path that does not exist, so a deploy
would otherwise install nothing, report success, and prune every file the last one
recorded (see [install manifest](#install-manifest)).

An `inline` app needs the variable only to be found by the secrets lookup below; without
it that lookup is skipped, and the app is deployed as one that ships no secrets.

## Install manifest

A deploy records the absolute host paths it installed to
`/var/app/<app>/.install-manifest`, one per line — a `source` app's Quadlet files,
systemd units and every file of its config tree, an `inline` app's one rendered
`<name>.container`. It sits inside the app's own home so the two share
fate: `absent` drops that tree and the manifest goes with it, and a hand-removed or
restored `/var/app/<app>` cannot leave a stale record behind. Both a later deploy and a decommission work from that
file rather than re-deriving from `apps/<app>/`, which by then may name different files
or be gone entirely, so a renamed or deleted file does not linger on the host and a
decommission needs no source tree at all.

Reading, pruning and recording are one call of the collection's `install_manifest`
module, on the host (`ansible-doc binarycodes.homelab.install_manifest`). The manifest is
host state acted on as root, so the module checks every recorded path before it removes
anything. A line is legal in exactly two shapes: a single path segment directly inside
`systemd_app_system_dir` / `systemd_app_unit_dir`, or any path nested under this app's own
`/var/app/<app>/config` with no empty, `.` or `..` segment. It must also be a regular file,
a symlink or missing: a line naming a directory is refused, because the record is a list of
files and nothing on it is ever removed recursively. One illegal line fails the run without
deleting anything, and what this deploy installed is held to the same rules before it is
recorded.

`absent` reads it too, and has to: the Quadlet files and systemd units it must remove
live in `/etc/containers/systemd/` and `/etc/systemd/system/`, which are shared with
every other app and cannot be relocated — systemd and the Quadlet generator only read
those paths. Dropping `/var/app/<app>` alone would leave them behind, and the generator
would recreate the service on the next `daemon-reload`. It asks the module first, in check
mode, what the record holds and which units that implies, stops those, and then calls it
again to remove the files and the record.

**Changing an app's kind converges on it.** Both kinds record a manifest and both
reconcile against it, so flipping `systemd_app_kind` between `source` and `inline` —
keeping the name — prunes whatever the old kind installed and the new one does not. A
`source` app that shipped a sidecar Quadlet, a `.timer` and a config tree becomes an
`inline` app whose only installed path is the rendered `<name>.container`, and the other
three are removed on that converge. The one thing to do by hand is the running units: a
unit whose file is pruned keeps running until it is stopped or the host reboots (see
below), so stop the ones the app no longer ships, once.

Why a manifest and not a destination diff: a deployed config tree can hold files that no
source tree contains. `systemd_app_caddy_confd` normally sits inside the reverse proxy's
own config tree, and holds a route snippet generated for every *other* routed app.
Deleting whatever is not in `<app>/config/` would wipe all of them on every converge. A
manifest only ever removes what a previous deploy recorded installing, so generated and
runtime files are invisible to it.

Only files are reconciled — a pruned tree can leave empty directories behind. And a unit
dropped from the app that is still running keeps running until it is stopped or the host
reboots: remove it from `systemd_app_enable_units` and stop it once by hand. That applies
to a change of kind as much as to a deleted file.

An app last deployed before the role recorded a manifest for its kind has none, so its
first converge under this role prunes nothing and records one — the reconciliation starts
from the next deploy. `absent` covers the gap for an `inline` app in that state by also
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

Installing a file is not the same as the app running from it, and the two kinds of change
this role writes become live in different ways.

**A changed unit definition, or a changed secret, needs a new container.** The Quadlet
generator rewrites the `.service` at `daemon-reload`, but systemd does not act on a unit it
merely re-read, and podman reads a secret only when it *creates* a container — so the
running one carries on with the image, options and values it started with. The role
therefore **restarts** the app's managed units when this deploy changed a file that defines
them: anything installed from `quadlet/` or `unit/`, or the rendered `<name>.container` of an
`inline` app.

**A changed config tree only needs the process told.** The files are bind-mounted, so they
are already in place. The role **reloads** the managed units instead, which is why config
is not folded in with the above: a unit that declares `ExecReload=` can take new config
without dropping what it is serving, and cycling it would throw that away. A unit with no
reload action reports `CanReload=no` and is restarted — blunt, but the only way to get the
change into the process. Nothing extra to declare per app: the unit already says which it
is, and the role reads that rather than keeping a second copy of the answer.

A restart supersedes a reload, so a deploy that changed both does one restart. A converge
that changed neither leaves the units alone, so this costs nothing on a no-op run.

Two consequences worth knowing:

- **A restart does not pull.** The pull policy decides that, and the default only fetches
  an image that is missing locally, so bumping `Image=` to a tag already on the host
  reuses it. A new tag or digest is missing by definition and is fetched; a moving tag
  like `:latest` is not, and needs `podman auto-update` (or `Pull=newer`) to move.
- **Removed files are treated by kind.** A pruned config file counts as a config change,
  since a running app is still reading what is now gone. A pruned unit file does not — it
  leaves nothing to act on, and a unit dropped from the app keeps running until it is
  stopped by hand (see [install manifest](#install-manifest)).

Generated route snippets are not part of any app's config tree, so they are outside this
entirely; your play applies those centrally once every app has converged (see
[Caddy routing](#caddy-routing)).

## Dry runs (`--check`)

A dry run reports what a deploy would change without touching the host. Most of what this
role does is `file`, `copy`, `template` and `systemd`, all of which answer that honestly,
so `--check` is a fair preview of the files it would install, the ones the manifest would
prune, and the units it would touch.

Secrets are the exception, because they are driven by `podman` rather than by a module.
Ansible skips a `command` task in check mode — it cannot know whether an arbitrary command
writes — so:

- **The store listing runs anyway** (`check_mode: false`). It only reads, and skipping it
  would leave the role reconciling against an empty view of the store, unable to tell
  "podman holds nothing" from "we did not ask" — a dry run would then claim it was about
  to re-store every secret the host already has.
- **Storing and removing a secret are skipped**, and cannot be previewed. Whether
  `podman secret create` would succeed is only knowable by running it.
- **A deploy whose only change is a secret reports no restart.** The restart decision keys
  off the store task having changed something, and a skipped task changes nothing. So a
  dry run can under-report a restart here, in the one case where it is caused by a secret
  alone.

Nothing in the gates runs the role under `--check`; the molecule scenario converges for
real. Treat a dry run as a good preview of the filesystem and unit state, and as silent
about the secret store.

## Rendered container policy

Beyond the parameters above, an `inline` container is rendered with four directives no
parameter controls. They are fleet policy rather than mechanism — the defaults for a
long-running service, not something the role needs in order to work:

| Directive                                  | Section       | Why                                                                                                                                                                       |
| ------------------------------------------ | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AutoUpdate=registry`                       | `[Container]` | Enrols the container in `podman auto-update`, so a moving tag is pulled and the app restarted on it without a deploy. Only useful together with the healthcheck below, which is what makes a bad image roll back. |
| `Restart=always`                            | `[Service]`   | The app is a service: it is expected to stay up, and any exit is a fault to recover from rather than a result. Wrong for a job that should run once and exit — that is a `source` app with its own unit and timer. |
| `Notify=healthy`, `TimeoutStartSec=`        | both          | Emitted only when `systemd_app_health_cmd` is set; see below.                                                                                                              |
| `WantedBy=multi-user.target default.target` | `[Install]`   | Starts at boot. Both targets are named so the app comes up whether or not the host's `default.target` is `multi-user.target`.                                               |

The first two are overridable without touching the role: a `[Container]` or `[Service]`
key set twice takes its last value, so an `AutoUpdate=` line in
`systemd_app_container_options`, or a `Restart=` line in `systemd_app_service_options`,
wins over the rendered one. `[Install]` has no such list, so an app that wants different
boot behaviour is a `source` app writing its own Quadlet — as is one that wants none of
this at all.

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

`systemd_app_env` renders `Environment=` lines into a Quadlet, which is a unit file and
world-readable — fine for a hostname, wrong for a client secret. Instead, an app that needs
secrets ships one SOPS-encrypted file in its own directory, keyed by the podman secret
names:

```yaml
# apps/myapp/secrets.sops.yaml   (values encrypted; keys readable)
myapp-oidc-issuer-uri: https://accounts.example.com
myapp-oidc-client-id: …
myapp-oidc-client-secret: …
```

Nothing at the call site, for either kind. The role looks for
`apps/<app>/secrets.sops.yaml` the same way it looks for the app's `quadlet/` and
`config/` directories, decrypts it on the controller, and stores each entry in podman's
secret store. The file sits at the app root, outside the three directories the role
installs from, so nothing copies it to the host.

How the container reaches a stored value differs by kind:

**`inline`** — the role renders the reference, deriving the variable from the name:
upper-cased, dashes as underscores. The file above yields

```ini
Secret=myapp-oidc-client-secret,type=env,target=MYAPP_OIDC_CLIENT_SECRET
```

so the name has to be the variable the app reads, spelled in lower case with dashes. That
is usually no constraint, since podman secret names are host-global and want an app prefix
anyway — and app-prefixed variables are what most images expect. When an image insists on a
bare name (`POSTGRES_PASSWORD`), the choice is a host-global secret called
`postgres-password` or the `source` kind.

**`source`** — the app writes the line itself and can point any name at any variable:

```ini
# apps/myapp/quadlet/myapp.container
Secret=myapp-oidc-client-secret,type=env,target=MYAPP_OIDC_CLIENT_SECRET
```

The name is then the whole contract between file and Quadlet — rename it in one and the
container fails to start referencing a name that no longer exists.

The values are loaded into a single dict, never top-level variables: podman secret names
are not legal variable names, and nothing sensitive becomes a play variable. Each is handed
to podman on **stdin**, never in a command line, since `/proc/<pid>/cmdline` is
world-readable and would expose it to every user on the host for the life of the process.

What this does and does not buy you. The value still ends up in the container's
environment, so it is readable by the process itself, by root, and in `podman inspect` of
the running container. What it avoids is a copy sitting in a `0644` file under
`/etc/containers/systemd/`, in a config tree, or in git. Podman's default file driver keeps
the store in a root-only file, unencrypted — treat "root on the host" as the trust
boundary either way.

**Rotation, renames and ownership.** Every secret the role stores carries two labels: the
app that owns it (`io.binarycodes.homelab.app`) and a digest of its value
(`io.binarycodes.homelab.digest`, as `sha256:<hex>` — the algorithm is named so it can
change later without every secret being rotated once). The store is the only record. Podman reads a secret when
it *creates* a container and cannot update a stored one in place, so on each deploy the
`podman_secrets` module compares the declared values against those labels: a converge where
nothing changed touches nothing; a changed value is removed and re-created, and the app's
units are **restarted** rather than started, since a running container would otherwise keep
serving with the old value; a name dropped from the file — renamed, or deleted — is removed
from the host, the same reconciliation the [install manifest](#install-manifest) does for
files. A secret removed by hand, or lost with a store reset, is simply missing and comes
back on the next deploy.

The owner label is also what `absent` removes by, so a decommission needs nothing from the
controller — the encrypted file may be gone by then — and what keeps two apps apart: a
deploy that declares a name another app owns is refused, naming that app, and the other
app's secret is left as it was. A secret with no owner label at all — stored by hand, or by
a release of this role before 1.1.0 — is taken to be the declaring app's with an unknown
value, and is re-created with labels; that is the one restart the upgrade costs.

A failed `podman secret create` is reported with the secret's name, podman's exit code and
its stderr. The value is a `no_log` parameter of the module, so it stays out of the log
while the diagnosis does not.

**Requirements.** The controller needs the `sops` binary and the `community.sops`
collection, and the decryption key — without it the run fails at the decrypting task,
before anything on the host changes. The host's podman must understand `Secret=` in a
Quadlet `[Container]` section; too old and the generator refuses the unit at
`daemon-reload`.

How the key reaches the controller is outside this role: sops finds it the usual ways
(`SOPS_AGE_KEY`, `~/.config/sops/age/keys.txt`, a KMS), and which keys encrypt which files
is your project's `.sops.yaml`. In CI that usually means one secret in the job environment
and nothing on disk.

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
Plain units in `unit/` are enabled normally.

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

The role's real computation is Python, not Jinja: filter plugins for what runs on the
controller, a module for what runs on the host. All ship with this collection and are called
by their fully qualified names (`binarycodes.homelab.route_problems` and so on), so they
resolve wherever the collection is installed:

| Plugin               | Kind   | Used for                                                          |
| -------------------- | ------ | ----------------------------------------------------------------- |
| `podman_secrets`     | module | Reconciling the app's podman secrets against the store, on the host. |
| `install_manifest`   | module | Reading, pruning and recording the install manifest, on the host; on `absent`, the units it implies. |
| `route_problems`     | filter | Checking `systemd_app_domain` / `_upstream` / `_port`.             |
| `container_problems` | filter | Checking what would be interpolated into a rendered Quadlet.      |
| `systemd_env_lines`  | filter | Quoting and escaping `systemd_app_env` into `Environment=` lines.  |

The filters live in `plugins/filter/`, the modules in `plugins/modules/`, one file each, and
are Python so they can be tested as Python: a table of cases in under a second, rather than
a playbook run per case (`tests/unit/`). Both `*_problems` filters return a list of
human-readable problems and never raise, so one run reports everything wrong at once. The
secrets module keeps every podman call behind one runner, so its reconciliation is tested
against a fake store; the manifest module only reads, unlinks and writes small files, so
its tests drive it against a temporary directory. A change to what the role *accepts*, to
how it decides what the secret store should hold, or to what a manifest may name, belongs
there rather than in a YAML scalar.

They are collection-global public API: anyone who installs the collection can call them,
whether or not they use this role, which is why they are named for what they compute
rather than for the role that calls them. Three filters are deprecated and go in 2.0.0:
`secret_digests` and `reconcile_secrets`, whose work the secrets module does from labels on
the secrets themselves, and `manifest_units`, whose answer the manifest module returns as
`units`.
