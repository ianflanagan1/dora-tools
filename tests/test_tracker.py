"""Production failures read from Jira as plain records."""

from datetime import UTC, datetime

from dora import tracker
from dora.tracker import Cause, Failure


def test_reads_each_labelled_issue_with_its_causes(jira_fake):
    jira_fake.failure("KAN-20", datetime(2026, 7, 2, tzinfo=UTC), done=True, caused_by="KAN-7")
    jira_fake.links["KAN-20"] = [
        {"object": {"title": "Caused by", "url": "https://bitbucket.org/t/a/commits/3f2a"}},
        {"object": {"title": "Design notes", "url": "https://wiki/x"}},
    ]
    assert tracker.failures(jira_fake, "KAN", "production-failure") == [
        Failure(
            "KAN-20",
            "Failure KAN-20",
            datetime(2026, 7, 2, tzinfo=UTC),
            True,
            (Cause("issue", "KAN-7"), Cause("link", "https://bitbucket.org/t/a/commits/3f2a")),
        )
    ]


def test_creation_times_are_in_utc(jira_fake):
    jira_fake.failure("KAN-20", datetime(2026, 7, 2, tzinfo=UTC))
    jira_fake.issues[0]["fields"]["created"] = "2026-07-02T23:01:22.862+0100"
    (failure,) = tracker.failures(jira_fake, "KAN", "production-failure")
    assert failure.created_at == datetime(2026, 7, 2, 22, 1, 22, 862000, tzinfo=UTC)


def test_only_issues_with_the_label_are_failures(jira_fake):
    jira_fake.failure("KAN-20", datetime(2026, 7, 2, tzinfo=UTC), label="other")
    assert tracker.failures(jira_fake, "KAN", "production-failure") == []
