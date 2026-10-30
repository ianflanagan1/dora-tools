# dora-tools

The [DORA metrics](https://dora.dev/guides/dora-metrics/) of a team's delivery, read from its code host and Jira. Built on [cli-kit](../cli-kit), and on [repo-tools](../repo-tools) for Bitbucket and [jira-tools](../jira-tools) for Jira.

What a project needs for the metrics, in its code host and tracker, in its repository, in its team's habits and on the collecting machine, and whose consent each change needs, is [docs/requirements.md](docs/requirements.md). It works with any branching model and merge strategy. Requirements are named there by id, such as **C2**, as they are here.

## Naming a repository

Both commands take a repository as `host/workspace/name`, such as `bitbucket.org/ianflanagan1/reop1`, or as a clone's folder, which names the repository its `origin` is. A repository's settings, its `.config/ianf-tracker.toml` and `.config/ianf-dora.toml`, are read from what's committed on its main branch on the code host, never from a clone, so no clone is needed and a clone on another branch can't mislead them. Bitbucket is the only code host so far.

## `dora sync [<repo>]... [--since <date>]`

Reads each repository's production deployments, the commits each shipped, its merged pull requests and its production failures, from the code host and tracker, into a SQLite file the metrics are calculated from. With no repository named, it syncs those listed in `config.toml` (see [Setup](#setup)).

```
$ dora sync
bitbucket.org/ianflanagan1/reop1: 1 new deployment, 1 merged pull request
✅ Synced.
~/.local/share/dora/dora.sqlite
```

- **Safe to run again.** Each sync reads back two weeks before the last one that finished, so deployments still running last time, and anything missed, are caught up; what's already recorded is left as it is. The first sync reads back 90 days, or to `--since`.
- **Run it daily.** Bitbucket lists a deployment for only 14 days, so a longer gap may lose some for good; the sync warns when the gap since the last one was longer.
- **Reads only.** It never writes to the code host or tracker. It writes only to the SQLite file: `~/.local/share/dora/dora.sqlite` (under `$XDG_DATA_HOME` when that's set), or `database` in `config.toml`. Back the file up: its older deployments can't be read again.
- **One file for every repository**, each row keyed by its repository, so a team's repositories can be combined in a query.
- **What a deployment shipped** is worked out as the commits in its commit and not in the previous production deployment's. Deploying an older commit than production had is recorded as a rollback.
- A repository that can't be synced, such as one with no `.config/ianf-dora.toml`, is reported, and the others are still synced; it ends ❌ naming it.

## `dora check [<repo>] [--since <date>]`

Checks a repository, the clone the current folder is in unless named, against every requirement that can be checked, and reports each:

```
$ dora check
ianflanagan1/reop1, habits since 2026-07-18

Settings
✔ C1  CI is on: Pipelines is on.
✔ C2  A production deployment environment: The environment production exists.
– T3  A link type that reads "is caused by" (optional): No link type reads "is caused by"; causes can be recorded as web links instead.
      The site has: Blocks, Cloners, Duplicate, Relates.
      → To record a cause as a link to the issue whose work introduced it, a Jira site admin adds it: …
…
Habits
✔ C4  Integration branches reach production with their commits: …
✘ H5  Each failure's cause recorded: 1 of 1 failure has no cause recorded.
      KAN-20 New bug
      → Find the commit that brought in the broken code, which `git blame` on the broken line in main shows, …
        jira weblink KAN-20 https://bitbucket.org/ianflanagan1/reop1/commits/<hash> "Caused by"

Not checkable
– H4  Production deployed only by CI: a deploy made elsewhere leaves no trace

⚠️ 2 of 17 requirements aren't met.
```

| Mark | Means |
|---|---|
| ✔ | met |
| ✘ | missed, with what was found |
| ? | for a person to look at, or not checked, such as for want of a token's scope |
| – | optional and not met, or can't be checked at all, and why |

Under a miss, or something to look at, what was found is grey, and how to fix it follows `→`: the setting to change and where, or the exact command, such as the `jira weblink` that records a cause. Where what happened can't be changed, such as a past merge, it says what to do next time.

Settings are checked as they are now; habits in the pull requests merged and the production failures recorded since `--since`, a date or time, 90 days ago unless given. It reads Bitbucket and Jira and changes nothing. It ends ⚠️, exit status 1, when a requirement is missed, and ➖ otherwise, counting any to look at or not checked.

It needs the repository on Bitbucket, with `.config/ianf-tracker.toml`, as jira-tools reads it, and `.config/ianf-dora.toml` (**R1**) committed on its main branch, as [repo-settings](../repo-settings) describes. Both are read from Bitbucket, not from a clone; a missing `ianf-dora.toml` is reported as R1 missed, and what needs it as not checked

and on the machine, the Bitbucket and Jira tokens repo-tools and jira-tools use (**M2**, **M3**). A token that isn't stored is reported as missed, with how to store it.

## Setup

Once per machine, with cli-kit, repo-tools and jira-tools checked out next to this folder:

```sh
cd ~/projects/cli/dora-tools
devbox run -- task setup
```

It builds `.venv` with repo-tools and jira-tools installed from those folders, and links `dora` into `~/.local/bin`, saying so if that folder isn't on your PATH. After pulling changes here or in them, run `devbox run -- uv sync`.

Setup also copies `config.example.toml` to `config.toml`, if there's none. It's this machine's own settings, which git ignores, unlike a repository's `.config/ianf-dora.toml`, which its team commits:

| Setting | Default | What for |
|---|---|---|
| `repos` | none | the repositories `dora sync` syncs when given none, as `host/workspace/name` |
| `database` | `~/.local/share/dora/dora.sqlite` | where `dora sync` keeps what it reads |

The Bitbucket and Jira tokens are repo-tools' and jira-tools': the first command that needs one says how to store it. To sync daily, add one line to your crontab with `crontab -e`, such as `0 9 * * * ~/.local/bin/dora sync >> ~/.local/share/dora/sync.log 2>&1`; a run missed while the machine was off is caught up by the next. dora can install that line for you, once you've agreed to it (**M4**).

## How it fits together

```
docs/requirements.md   what the metrics need, by id
src/dora/
├── project.py      the repository measured: named or from a clone, and its committed ianf-dora.toml
├── tracker.py      production failures and their causes, read from the tracker as plain records
├── store.py        the SQLite file: its tables, and recording what's read
├── sync.py         syncing one repository into the store, through the code host's Forge
├── config.py       this machine's settings: which repositories to sync, and where the file is
├── checks.py       every requirement in docs/requirements.md, and the check of each that can be checked
└── commands/
    ├── sync.py     dora sync
    └── check.py    dora check: running the checks and reporting them
```

To check a new requirement, add it to docs/requirements.md first, then a function to `checks.py` that takes the `Context` and returns a `Result`, listed in `REQUIREMENTS` under its group. A check that can't get what it needs, such as for want of a scope, raises the API's `Problem`, and is reported as not checked.

## Development

```sh
devbox run -- task test   # pytest
devbox run -- task lint   # ruff check and format
```

Tests run the checks against a fake Bitbucket and Jira, in `tests/conftest.py`. Tests use `--import-mode=importlib`; share helpers through fixtures in `conftest.py`, not by importing it.
