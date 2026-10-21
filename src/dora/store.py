"""The SQLite file dora sync keeps what it reads in, and the metrics are calculated from.

Every repository's data is in the one file, each row keyed by its repository, so a team's
repositories can be combined later in a query. Times are ISO 8601 text, in UTC. Writing the same
thing again changes nothing, so a sync can always be run again.

    repos          the repositories synced, by host and name
    deployments    production deployments: their commit, status and times, and once worked out,
                   the previous production deployment's commit and whether this one rolled back
    shipped        the commits each production deployment shipped
    pull_requests  merged pull requests, for finding fixes by their key
    failures       production failures recorded in the tracker
    causes         what each failure was recorded as caused by
    syncs          each sync of each repository, and whether it finished
"""

import os
import sqlite3
from collections.abc import Iterable
from dataclasses import astuple
from datetime import UTC, datetime
from pathlib import Path

from repo_tools.forge import Commit, Deployment, PullRequest, Status

from dora.project import Ref
from dora.tracker import Failure

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    id INTEGER PRIMARY KEY,
    host TEXT NOT NULL,
    name TEXT NOT NULL,
    UNIQUE (host, name)
);
CREATE TABLE IF NOT EXISTS deployments (
    repo_id INTEGER NOT NULL REFERENCES repos (id),
    id TEXT NOT NULL,
    environment TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    url TEXT NOT NULL,
    shipped_from TEXT,      -- the previous production deployment's commit, once worked out
    first INTEGER,          -- 1 for the first production deployment on record
    rollback INTEGER,       -- 1 when it deployed a commit older than production had
    PRIMARY KEY (repo_id, id)
);
CREATE TABLE IF NOT EXISTS shipped (
    repo_id INTEGER NOT NULL,
    deployment_id TEXT NOT NULL,
    sha TEXT NOT NULL,
    authored_at TEXT NOT NULL,
    summary TEXT NOT NULL,
    merge INTEGER NOT NULL,
    PRIMARY KEY (repo_id, deployment_id, sha),
    FOREIGN KEY (repo_id, deployment_id) REFERENCES deployments (repo_id, id)
);
CREATE TABLE IF NOT EXISTS pull_requests (
    repo_id INTEGER NOT NULL REFERENCES repos (id),
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    base TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    merged_at TEXT NOT NULL,
    merge_commit TEXT,
    url TEXT NOT NULL,
    PRIMARY KEY (repo_id, number)
);
CREATE TABLE IF NOT EXISTS failures (
    repo_id INTEGER NOT NULL REFERENCES repos (id),
    key TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    done INTEGER NOT NULL,
    PRIMARY KEY (repo_id, key)
);
CREATE TABLE IF NOT EXISTS causes (
    repo_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    PRIMARY KEY (repo_id, key, kind, target)
);
CREATE TABLE IF NOT EXISTS syncs (
    repo_id INTEGER NOT NULL REFERENCES repos (id),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT
);
"""


def default_path() -> Path:
    """$XDG_DATA_HOME/dora/dora.sqlite, or ~/.local/share/dora/dora.sqlite without it."""
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "dora" / "dora.sqlite"


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def repo(self, ref: Ref) -> int:
        """The repository's id, adding it the first time."""
        self.db.execute(
            "INSERT OR IGNORE INTO repos (host, name) VALUES (?, ?)", (ref.host, ref.name)
        )
        row = self.db.execute(
            "SELECT id FROM repos WHERE host = ? AND name = ?", (ref.host, ref.name)
        ).fetchone()
        return row[0]

    # Syncs

    def last_synced(self, repo: int) -> datetime | None:
        """When the repository's last sync that finished started: what it read is complete."""
        row = self.db.execute(
            "SELECT max(started_at) FROM syncs WHERE repo_id = ? AND finished_at IS NOT NULL"
            " AND error IS NULL",
            (repo,),
        ).fetchone()
        return _time(row[0])

    def begin(self, repo: int, now: datetime) -> int:
        cursor = self.db.execute(
            "INSERT INTO syncs (repo_id, started_at) VALUES (?, ?)", (repo, _text(now))
        )
        self.db.commit()
        return cursor.lastrowid

    def end(self, sync: int, now: datetime, error: str | None = None) -> None:
        self.db.execute(
            "UPDATE syncs SET finished_at = ?, error = ? WHERE rowid = ?", (_text(now), error, sync)
        )
        self.db.commit()

    # Deployments

    def record_deployment(self, repo: int, deployment: Deployment) -> str | None:
        """Records the deployment: "new" when it wasn't recorded, "changed" when its status or
        finish was, and None when it's recorded as it is."""
        row = self.db.execute(
            "SELECT status, finished_at FROM deployments WHERE repo_id = ? AND id = ?",
            (repo, deployment.id),
        ).fetchone()
        values = (
            deployment.environment,
            deployment.commit,
            str(deployment.status),
            _text(deployment.started_at),
            _text(deployment.finished_at),
            deployment.url,
        )
        if row is None:
            self.db.execute(
                "INSERT INTO deployments (repo_id, id, environment, commit_sha, status,"
                " started_at, finished_at, url) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (repo, deployment.id, *values),
            )
            return "new"
        if row == (str(deployment.status), _text(deployment.finished_at)):
            return None
        self.db.execute(
            "UPDATE deployments SET environment = ?, commit_sha = ?, status = ?, started_at = ?,"
            " finished_at = ?, url = ? WHERE repo_id = ? AND id = ?",
            (*values, repo, deployment.id),
        )
        return "changed"

    def unworked(self, repo: int) -> list[tuple[str, str, str]]:
        """The succeeded production deployments whose shipped commits aren't worked out yet, as
        (id, commit, finished_at), oldest first."""
        return self.db.execute(
            "SELECT id, commit_sha, finished_at FROM deployments WHERE repo_id = ? AND status = ?"
            " AND shipped_from IS NULL AND first IS NULL ORDER BY finished_at, id",
            (repo, str(Status.SUCCEEDED)),
        ).fetchall()

    def previous(self, repo: int, finished_at: str, deployment: str) -> str | None:
        """The commit of the last succeeded production deployment that finished before
        finished_at, other than deployment, or None when there's none on record."""
        row = self.db.execute(
            "SELECT commit_sha FROM deployments WHERE repo_id = ? AND status = ? AND id != ?"
            " AND finished_at < ? ORDER BY finished_at DESC, id DESC LIMIT 1",
            (repo, str(Status.SUCCEEDED), deployment, finished_at),
        ).fetchone()
        return row[0] if row else None

    def mark_first(self, repo: int, deployment: str) -> None:
        self.db.execute(
            "UPDATE deployments SET first = 1 WHERE repo_id = ? AND id = ?", (repo, deployment)
        )

    def record_shipped(
        self,
        repo: int,
        deployment: str,
        previous: str,
        commits: Iterable[Commit],
        rollback: bool,
    ) -> None:
        self.db.executemany(
            "INSERT OR IGNORE INTO shipped (repo_id, deployment_id, sha, authored_at, summary,"
            " merge) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (repo, deployment, c.sha, _text(c.authored_at), c.summary, int(c.merge))
                for c in commits
            ],
        )
        self.db.execute(
            "UPDATE deployments SET shipped_from = ?, rollback = ? WHERE repo_id = ? AND id = ?",
            (previous, int(rollback), repo, deployment),
        )

    # Pull requests and failures

    def record_pull(self, repo: int, pull: PullRequest) -> bool:
        """Records the merged pull request; True when it wasn't recorded."""
        values = astuple(pull)
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO pull_requests (repo_id, number, title, author, base,"
            " opened_at, merged_at, merge_commit, url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (repo, *values[:4], _text(pull.opened_at), _text(pull.merged_at), *values[6:]),
        )
        return cursor.rowcount == 1

    def record_failures(self, repo: int, failures: list[Failure]) -> int:
        """Records the repository's failures as the tracker has them now, replacing what was
        recorded, and returns how many weren't recorded before."""
        known = {
            row[0] for row in self.db.execute("SELECT key FROM failures WHERE repo_id = ?", (repo,))
        }
        self.db.execute("DELETE FROM causes WHERE repo_id = ?", (repo,))
        self.db.execute("DELETE FROM failures WHERE repo_id = ?", (repo,))
        for failure in failures:
            self.db.execute(
                "INSERT INTO failures (repo_id, key, summary, created_at, done)"
                " VALUES (?, ?, ?, ?, ?)",
                (repo, failure.key, failure.summary, _text(failure.created_at), int(failure.done)),
            )
            self.db.executemany(
                "INSERT OR IGNORE INTO causes (repo_id, key, kind, target) VALUES (?, ?, ?, ?)",
                [(repo, failure.key, c.kind, c.target) for c in failure.causes],
            )
        return len({f.key for f in failures} - known)

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()


def _text(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).isoformat() if moment else None


def _time(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None
