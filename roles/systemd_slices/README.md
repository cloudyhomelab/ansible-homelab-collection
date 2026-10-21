# systemd_slices

Installs systemd [slice units](https://www.freedesktop.org/software/systemd/man/latest/systemd.slice.html)
with [resource-control](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html)
settings, so a group of units can share one memory, CPU or I/O budget. A unit joins a slice
with `Slice=<name>.slice` in its `[Service]` section, which a Quadlet `.container` file has
too.

## Requirements

**A privileged play.** The slices are written to `/etc/systemd/system` as root, so the caller
sets `become: true` on the play or on the role entry.

**On the target host:** systemd. Fedora 43 and Debian 13 are what the molecule scenario
converges and what `meta/main.yml` claims.

## Role parameters

| Variable               | Default | Purpose                      |
| ---------------------- | ------- | ---------------------------- |
| `systemd_slices_units` | `[]`    | The slices to install, below. |

Each entry:

| Key           | Required | Purpose |
| ------------- | -------- | ------- |
| `name`        | yes      | Unit name without `.slice`. A dash nests it under the slice its prefix names: `homelab-media` sits under `homelab`, which systemd creates implicitly if no file defines it. |
| `description` | no       | The unit's `Description=`. |
| `settings`    | no       | `[Slice]` properties, written one per line as given. A value is a string or an integer; quote a boolean (`"yes"`), since YAML would otherwise turn it into one the unit file cannot spell back. |

## What it does

1. Checks every entry before writing anything: a name that would leave the unit directory, a
   duplicate, a line break in a description or value, an unknown key. One run reports every
   problem.
2. Writes `/etc/systemd/system/<name>.slice` for each entry.
3. Runs `systemctl daemon-reload`, once and only if a file changed, before the role returns:
   a later role in the play can start a unit in the new slice, and an active slice picks up
   changed settings, which it otherwise keeps until something reloads systemd.

It starts nothing. A slice becomes active with the first unit placed in it.

**Removal is not reconciled.** An entry dropped from `systemd_slices_units` leaves its file on
the host. Move the units out of the slice, then delete the file and reload systemd.

## Example

```yaml
- hosts: all
  become: true
  roles:
    - role: binarycodes.homelab.systemd_slices
      systemd_slices_units:
        - name: homelab
          description: Every homelab app
          settings:
            MemoryMax: 12G
        - name: homelab-media
          description: Media apps, capped below the rest
          settings:
            MemoryMax: 4G
            CPUWeight: 50
```

A Quadlet then places its container in one:

```ini
[Service]
Slice=homelab-media.slice
```

## Where the logic lives

| Plugin                    | Kind   | Used for                                     |
| ------------------------- | ------ | -------------------------------------------- |
| `slice_validation_errors` | filter | Checking every entry of `systemd_slices_units`. |
