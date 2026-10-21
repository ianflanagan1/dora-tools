"""Syncing a repository into the store, from a fake code host and the tracker's failures."""

from datetime import UTC, datetime, timedelta

import pytest
from repo_tools.forge import Commit, Deployment, Forge, PullRequest, Status

from dora.project import Project, Ref, Settings
from dora.store import Store
from dora.sync import sync
from dora.tracker import Cause, Failure

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
PROJECT = Project(Ref("bitbucket.org", "team/app"), "main", Settings("production"))


def ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


class Host(Forge):
    """A code host whose history is a line of commits, oldest first: c1, c2, c3, …"""

    name = "Fake"

    def __init__(self) -> None:
        self.history = [f"c{n}" for n in range(1, 10)]
        self.deployed: list[Deployment] = []
        self.pulls: list[PullRequest] = []
        self.asked_since: list[datetime] = []

    def deploy(self, number: int, commit: str, days_ago: float, status=Status.SUCCEEDED):
        start = ago(days_ago)
        finish = None if status == Status.IN_PROGRESS else start + timedelta(minutes=5)
        self.deployed.append(
            Deployment(f"d{number}", "Production", commit, status, start, finish, "https://x")
        )

    def full_name(self, repo):
        return repo

    def main_branch(self, repo):
        return "main"

    def file(self, repo, revision, path):
        return None

    def environments(self, repo):
        return ["Production"]

    def deployments(self, repo, environment, since):
        assert environment == "production"
        self.asked_since.append(since)
        return [d for d in self.deployed if d.started_at >= since]

    def merged_pull_requests(self, repo, since, base=None):
        return [p for p in self.pulls if p.merged_at >= since]

    def commits_between(self, repo, base, head):
        """The commits after base up to head, newest first: none when head isn't after base."""
        start, end = self.history.index(base), self.history.index(head)
        return [
            Commit(sha, ago(30 - n), f"Change {sha}", merge=False)
            for n, sha in enumerate(self.history)
            if start < n <= end
        ][::-1]


@pytest.fixture
def store(tmp_path) -> Store:
    store = Store(tmp_path / "dora" / "dora.sqlite")
    yield store
    store.close()


@pytest.fixture
def host() -> Host:
    return Host()


def rows(store: Store, query: str, *args) -> list[tuple]:
    return store.db.execute(query, args).fetchall()


def test_the_store_is_created_with_its_folder(tmp_path):
    Store(tmp_path / "a" / "b" / "dora.sqlite").close()
    assert (tmp_path / "a" / "b" / "dora.sqlite").is_file()


def test_records_production_deployments(store, host):
    host.deploy(1, "c2", 3)
    synced = sync(store, PROJECT, host, [], NOW)
    assert synced.deployments == 1
    assert rows(store, "SELECT id, commit_sha, status FROM deployments") == [
        ("d1", "c2", "succeeded")
    ]


def test_syncing_again_records_nothing_new(store, host):
    host.deploy(1, "c2", 3)
    sync(store, PROJECT, host, [], NOW)
    synced = sync(store, PROJECT, host, [], NOW + timedelta(hours=1))
    assert (synced.deployments, synced.updated, synced.lines()) == (0, 0, [])


def test_a_deployment_that_finished_since_is_updated(store, host):
    host.deploy(1, "c2", 1, status=Status.IN_PROGRESS)
    sync(store, PROJECT, host, [], NOW)
    host.deployed.clear()
    host.deploy(1, "c2", 1)
    assert sync(store, PROJECT, host, [], NOW + timedelta(hours=1)).updated == 1
    assert rows(store, "SELECT status FROM deployments") == [("succeeded",)]


def test_each_deployment_ships_the_commits_since_the_previous_one(store, host):
    host.deploy(1, "c2", 5)
    host.deploy(2, "c5", 3)
    sync(store, PROJECT, host, [], NOW)
    assert rows(store, "SELECT sha FROM shipped WHERE deployment_id = 'd2' ORDER BY sha") == [
        ("c3",),
        ("c4",),
        ("c5",),
    ]
    assert rows(store, "SELECT shipped_from, rollback FROM deployments WHERE id = 'd2'") == [
        ("c2", 0)
    ]


def test_the_first_deployment_on_record_ships_nothing_known(store, host):
    host.deploy(1, "c2", 5)
    sync(store, PROJECT, host, [], NOW)
    assert rows(store, "SELECT first, shipped_from FROM deployments") == [(1, None)]
    assert rows(store, "SELECT count(*) FROM shipped") == [(0,)]


def test_deploying_an_older_commit_is_a_rollback(store, host):
    host.deploy(1, "c3", 5)
    host.deploy(2, "c5", 3)
    host.deploy(3, "c3", 1)
    sync(store, PROJECT, host, [], NOW)
    assert rows(store, "SELECT rollback FROM deployments WHERE id = 'd3'") == [(1,)]
    assert rows(store, "SELECT count(*) FROM shipped WHERE deployment_id = 'd3'") == [(0,)]


def test_redeploying_the_same_commit_ships_nothing_and_is_no_rollback(store, host):
    host.deploy(1, "c3", 5)
    host.deploy(2, "c3", 3)
    sync(store, PROJECT, host, [], NOW)
    assert rows(store, "SELECT shipped_from, rollback FROM deployments WHERE id = 'd2'") == [
        ("c3", 0)
    ]


def test_failed_deployments_are_recorded_but_ship_nothing(store, host):
    host.deploy(1, "c2", 5)
    host.deploy(2, "c4", 3, status=Status.FAILED)
    host.deploy(3, "c5", 1)
    sync(store, PROJECT, host, [], NOW)
    assert rows(store, "SELECT shipped_from FROM deployments WHERE id = 'd2'") == [(None,)]
    assert rows(store, "SELECT shipped_from FROM deployments WHERE id = 'd3'") == [("c2",)]


def test_the_first_sync_reads_back_ninety_days(store, host):
    sync(store, PROJECT, host, [], NOW)
    assert host.asked_since == [ago(90)]


def test_since_overrides_how_far_back_it_reads(store, host):
    sync(store, PROJECT, host, [], NOW, since=ago(400))
    assert host.asked_since == [ago(400)]


def test_a_later_sync_reads_back_two_weeks_before_the_last(store, host):
    sync(store, PROJECT, host, [], NOW)
    sync(store, PROJECT, host, [], NOW + timedelta(days=1))
    assert host.asked_since[1] == NOW - timedelta(days=14)


def test_a_gap_longer_than_bitbucket_lists_deployments_is_warned_of(store, host):
    sync(store, PROJECT, host, [], NOW)
    synced = sync(store, PROJECT, host, [], NOW + timedelta(days=20))
    assert synced.warnings == [
        "The last sync was 20 days ago: deployments from before 2026-10-07 may no longer be"
        " listed, and are lost."
    ]


def test_records_merged_pull_requests_once(store, host):
    host.pulls.append(
        PullRequest(7, "KAN-20 Fix it", "me", "main", ago(3), ago(2), "c4", "https://x/7")
    )
    assert sync(store, PROJECT, host, [], NOW).pulls == 1
    assert sync(store, PROJECT, host, [], NOW + timedelta(hours=1)).pulls == 0
    assert rows(store, "SELECT number, title, merge_commit FROM pull_requests") == [
        (7, "KAN-20 Fix it", "c4")
    ]


def test_records_failures_with_their_causes_as_the_tracker_has_them_now(store, host):
    failure = Failure(
        "KAN-20", "Checkout fails", ago(2), False, (Cause("link", "https://x/commits/c3"),)
    )
    assert sync(store, PROJECT, host, [failure], NOW).failures == 1
    fixed = Failure("KAN-20", "Checkout fails", ago(2), True, (Cause("issue", "KAN-7"),))
    assert sync(store, PROJECT, host, [fixed], NOW + timedelta(hours=1)).failures == 0
    assert rows(store, "SELECT key, done FROM failures") == [("KAN-20", 1)]
    assert rows(store, "SELECT kind, target FROM causes") == [("issue", "KAN-7")]


def test_repositories_are_kept_apart_in_one_file(store, host):
    host.deploy(1, "c2", 3)
    other = Project(Ref("bitbucket.org", "team/other"), "main", Settings("production"))
    sync(store, PROJECT, host, [], NOW)
    sync(store, other, host, [], NOW)
    assert rows(
        store,
        "SELECT r.name, count(*) FROM deployments d JOIN repos r ON r.id = d.repo_id"
        " GROUP BY r.name ORDER BY r.name",
    ) == [("team/app", 1), ("team/other", 1)]


def test_a_sync_that_fails_records_nothing_and_isnt_counted_as_the_last(store, host):
    host.deploy(1, "c2", 3)

    def broken(repo, since, base=None):
        raise RuntimeError("host went away")

    host.merged_pull_requests = broken
    with pytest.raises(RuntimeError):
        sync(store, PROJECT, host, [], NOW)
    assert rows(store, "SELECT count(*) FROM deployments") == [(0,)]
    assert rows(store, "SELECT error FROM syncs") == [("host went away",)]
    assert store.last_synced(store.repo(PROJECT.ref)) is None
