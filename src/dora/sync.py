"""Syncing one repository into the store: its production deployments and the commits each
shipped, its merged pull requests, and its production failures.

Each sync reads back to OVERLAP before the last one that finished, so deployments that were
still running, or changed status, are read again; the first reads back FIRST_DAYS, or as far as
--since says. Bitbucket lists a deployment for only 14 days, so a gap between syncs longer than
that may have lost some, and the sync says so.

The commits a production deployment shipped are those in its commit and not in the previous
production deployment's. A deployment of a commit older than production had is a rollback, and
ships nothing. The first production deployment on record has no previous one, so it isn't worked
out; nor is anything for a deployment that hasn't succeeded.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from repo_tools.forge import Forge

from dora.project import Project
from dora.store import Store
from dora.tracker import Failure

OVERLAP = timedelta(days=14)
FIRST_DAYS = 90
LISTED_FOR = timedelta(days=14)
"""How long Bitbucket lists a deployment for."""


@dataclass
class Synced:
    """What a sync recorded that wasn't recorded before."""

    deployments: int = 0
    updated: int = 0
    pulls: int = 0
    failures: int = 0
    warnings: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        counts = [
            (self.deployments, "new deployment"),
            (self.updated, "updated deployment"),
            (self.pulls, "merged pull request"),
            (self.failures, "new failure"),
        ]
        return [f"{n} {noun}{'' if n == 1 else 's'}" for n, noun in counts if n]


def sync(
    store: Store,
    project: Project,
    host: Forge,
    failures: list[Failure],
    now: datetime,
    since: datetime | None = None,
) -> Synced:
    """Syncs the project into store, as of now. failures are the project's production failures,
    as the tracker has them now. since overrides how far back it reads."""
    settings = project.settings
    repo = store.repo(project.ref)
    last = store.last_synced(repo)
    synced = Synced()
    if since is None:
        since = last - OVERLAP if last else now - timedelta(days=FIRST_DAYS)
    if last and now - last > LISTED_FOR:
        synced.warnings.append(
            f"The last sync was {(now - last).days} days ago: deployments from before"
            f" {(now - LISTED_FOR).date().isoformat()} may no longer be listed, and are lost."
        )

    run = store.begin(repo, now)
    try:
        for deployment in host.deployments(project.repo, settings.production, since):
            match store.record_deployment(repo, deployment):
                case "new":
                    synced.deployments += 1
                case "changed":
                    synced.updated += 1
        _work_out_shipped(store, repo, project.repo, host)
        for pull in host.merged_pull_requests(project.repo, since):
            synced.pulls += store.record_pull(repo, pull)
        synced.failures = store.record_failures(repo, failures)
        store.commit()
    except Exception as error:
        store.rollback()
        store.end(run, now, error=str(error) or type(error).__name__)
        raise
    store.end(run, now)
    return synced


def _work_out_shipped(store: Store, repo: int, name: str, host: Forge) -> None:
    """Works out what each succeeded production deployment shipped, oldest first, from the
    one before it."""
    for deployment, commit, finished_at in store.unworked(repo):
        previous = store.previous(repo, finished_at, deployment)
        if previous is None:
            store.mark_first(repo, deployment)
            continue
        if previous == commit:
            store.record_shipped(repo, deployment, previous, [], rollback=False)
            continue
        shipped = host.commits_between(name, previous, commit)
        rollback = not shipped and bool(host.commits_between(name, commit, previous))
        store.record_shipped(repo, deployment, previous, shipped, rollback=rollback)
