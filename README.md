# Deluge Sync

Helper script to manage Deluge.

## Commands

### Global Options

The command has 3 global options that can be used:

* `-u`, `--deluge-url` -- env: `DELUGE_SYNC_URL` -- Required -- Base URL for your Deluge Web UI
* `-p`, `--deluge-password` -- env: `DELUGE_SYNC_PASSWORD` -- Required -- Password for your Deluge Web UI
* `-q`, `--quiet` -- Suppress most output for the command

### Filters

The `query` and the `sync` command both allow you to filter torrents. The two filters you can apply are by labels and exclude labels:

* `-l`, `--label` -- env: `DELUGE_SYNC_LABELS` -- A list of labels that a torrent _must_ have to be selected. `-l` can be provided multiple times or all items can be provided in a comma list (`label1,label2,etc`).
* `-e`, `--exclude-label` -- env: `DELUGE_SYNC_EXCLUDE_LABELS` -- A list of labels that a torrent _must not_ have to be selected. `-e` can be provided multiple times or all items can be provided in a comma list (`label1,label2,etc`).

Both `-l` and `-e` can be provided at the sametime, but it does not make much sense to do so. And it make cause you to have no results.

### `query`

The `query` command is mostly used for debugging and testing. But it lets you list all of the torrents in your instance using the same filters the `sync` command will use.

Example:
```bash
deluge-sync query -l linux-isos
Logging in to deluge (https://deluge.example.com )
Getting list of seeding torrents (label=linux-isos)...
                                                                                                          Torrents
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ ID    ┃ Name       ┃ State   ┃ Progress            ┃ Label      ┃ Tracker    ┃ Added                     ┃ Seeding Time  ┃ Path               ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
│ someid │ Archlinux   │ Seeding │ 700MiB / 700MiB 100% │ linux-isos  │ example.com │ 2024-09-02T21:16:14+00:00 │ 1 day, 0:38:25 │ /downloads/complete │
└──────────────────────────────────────┴───────────────────────────────────────┴─────────┴──────────────────────────┴───────┴──────────────────────────┘
```

### `sync`

The `sync` command is the meat of the script. It grabs a list of torrents using some filters and then potentially applies the following actions to them:

* Move the download folder for the torrent based on the tracker
* Remove the torrent from Deluge based on some tracker rules (time seeded, etc.)

Configurations options:

* `-t`, `--seed-time` -- env: `DELUGE_SYNC_SEED_TIME` -- default: `1h 30m` -- Time to seed a torrent before removing it if it matches no other rules.
* `-m`, `--path-map` -- env: `DELUGE_SYNC_PATH_MAP` -- List of rules to change the download paths per tracker. Format is `trackerhost=/download/path`, example: `example.com=/downloads/example`
* env: `DELUGE_SYNC_RULES` -- Env only. A list of tracker rules for choosing when to remove a seeding torrent.

#### Rules

There are some preconfigured tracker rules, but you can also configure your own with the env `DELUGE_SYNC_RULES`. The env is a JSON list of rules.

Each rule is composed of

* `host` -- required -- the tracker host to match on
* `priority` -- required -- the rule priority. Lower numbered rules are applied first
* `min_time` -- required -- the required seed time before the torrent can be removed
* `name_search` -- regex to match the name of the torrent. Can be combined with `priority` to apply a different `min_time` based on the name

These example rules will make sure

* all torrents for the `example.com` tracker host is seeded for 24 hours before being removed
* torrents for the `example2.com` tracker host will be seeded for 24 hours if they have the word "nightly" (case insensitive) in the name
* all other torrents for `example2.com` will be seeded for 1 week (168 hours) before being removed

```json
[{"host":"example.com","priority":10,"min_time":"24:00:00"},{"host":"example2.com","priority":1,"min_time":"24:00:00","name_search":"(?i)nightly"},{"host":"example2.com","priority":10,"min_time":"168:00:00"}]
```

## Setup

There is an example [kubernetes manifest](https://github.com/AngellusMortis/deluge-sync/blob/master/manifest.yml) that you can use as an example for how to set this up in a k8s cluster.
