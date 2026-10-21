"""dora sync [repo]... [--since <date>]

Reads each repository's production deployments, the commits each shipped, its merged pull
requests and its production failures from its code host and tracker, into the SQLite file the
metrics are calculated from. repo is host/workspace/name, such as bitbucket.org/team/app, or a
clone's folder; with none, the repositories in dora-tools' config.toml.

Each sync reads back two weeks before the last one, so it's safe to run again and catches up
on what it missed; the first reads back 90 days, or to --since. Bitbucket lists deployments for
only 14 days, so run it daily: it warns when the gap since the last sync was longer.

It only reads from the code host and tracker, and writes only to the SQLite file:
~/.local/share/dora/dora.sqlite unless config.toml says otherwise.
"""

from datetime import UTC, datetime

from cli_kit import cli, outcome
from cli_kit.outcome import Done, Failure, NothingToDo, Precondition, Problem
from repo_tools.forge_commands import parse_since

from dora import config, project, tracker
from dora.store import Store
from dora.sync import Synced, sync


def register(subparsers: cli.Subparsers) -> None:
    parser = subparsers.add_parser(
        "sync",
        help="read repositories' delivery data into the local database",
        description=__doc__.split("\n\n", 1)[1],
    )
    parser.add_argument(
        "repo", nargs="*", help="host/workspace/name, or a clone's folder; config.toml's if none"
    )
    parser.add_argument("--since", type=parse_since, help="read back to this date or time")
    parser.set_defaults(run=lambda args: run(args.repo, args.since))


def run(given: list[str], since: datetime | None = None) -> Done | NothingToDo:
    settings = config.load()
    names = given or list(settings.repos)
    if not names:
        raise Precondition(
            "No repositories to sync.",
            f"Name one, or list them as repos in {config.CONFIG}, as config.example.toml shows.",
        )
    refs = [project.resolve(name) for name in names]
    store = Store(settings.database_path)
    now = datetime.now(UTC)
    failed: list[str] = []
    try:
        for ref in refs:
            try:
                synced = sync_one(store, ref, now, since)
            except Problem as problem:
                failed += [f"{ref}: {problem.message}", *(f"  {d}" for d in problem.details)]
                continue
            if lines := synced.lines():
                outcome.changed(f"{ref}: {', '.join(lines)}")
            else:
                print(f"{ref}: up to date")
            for warning in synced.warnings:
                print(f"  {warning}")
    finally:
        store.close()
    if failed:
        count = sum(not line.startswith("  ") for line in failed)
        raise Failure(f"{count} of {len(refs)} didn't sync.", *failed)
    if not outcome.changes():
        return NothingToDo(f"Already up to date, in {settings.database_path}.")
    return Done("Synced.", str(settings.database_path))


def sync_one(store: Store, ref: project.Ref, now: datetime, since: datetime | None) -> Synced:
    """Syncs ref, returning what it recorded."""
    host = project.forge(ref)
    found = project.load(ref, host)
    if found.settings is None:
        raise Precondition(*found.problem, "Run `dora check` on it to see what it needs.")
    tracker_settings = tracker.settings(found)
    jira = tracker.connect(tracker_settings)
    failures = tracker.failures(jira, tracker_settings.space, found.settings.failure_label)
    return sync(store, found, host, failures, now, since)
