"""dora check's report and how it ends."""

from datetime import UTC, datetime

import pytest
from cli_kit.outcome import NothingToDo, Precondition

from dora.commands.check import check

SINCE = datetime(2026, 7, 1, tzinfo=UTC)  # as the ctx fixture's


@pytest.fixture(autouse=True)
def tools_and_job(monkeypatch: pytest.MonkeyPatch):
    """bitbucket and jira on the PATH, and the daily job in the crontab."""
    import subprocess

    from dora import checks

    monkeypatch.setattr(checks.shutil, "which", lambda tool: f"/bin/{tool}")
    real_run = subprocess.run

    def run(args, **kwargs):
        if args[:2] == ["crontab", "-l"]:
            return subprocess.CompletedProcess(args, 0, "0 9 * * * dora sync\n", "")
        return real_run(args, **kwargs)

    monkeypatch.setattr(checks.subprocess, "run", run)


def test_a_project_meeting_everything_ends_with_nothing_to_do(ctx, capsys):
    result = check(None, SINCE, ctx)
    assert isinstance(result, NothingToDo)
    assert result.summary == "Every checked requirement is met."
    out = capsys.readouterr().out
    assert "✔ C1  CI is on" in out
    assert "– H4  Production deployed only by CI: a deploy made elsewhere" in out


def test_a_missed_requirement_ends_with_how_many(ctx, jira_fake, capsys):
    jira_fake.spaces.clear()
    with pytest.raises(Precondition) as stopped:
        check(None, SINCE, ctx)
    assert stopped.value.message == "1 of 17 requirements aren't met."
    assert "✘ T1" in capsys.readouterr().out


def test_unchecked_and_for_review_requirements_are_counted_in_the_status(ctx, bitbucket_fake):
    bitbucket_fake.enabled, bitbucket_fake.runs = None, []
    assert check(None, SINCE, ctx).summary == ("Every checked requirement is met; 1 not checked.")


def test_details_are_indented_under_their_requirement(ctx, bitbucket_fake, capsys):
    bitbucket_fake.environment_names = ["Test"]
    with pytest.raises(Precondition):
        check(None, SINCE, ctx)
    lines = capsys.readouterr().out.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("✘ C2"))
    assert lines[index + 1] == "      It has: Test."


def test_how_to_fix_follows_a_missed_requirements_details(ctx, bitbucket_fake, capsys):
    bitbucket_fake.environment_names = ["Test"]
    with pytest.raises(Precondition):
        check(None, SINCE, ctx)
    lines = capsys.readouterr().out.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("✘ C2"))
    assert lines[index + 1] == "      It has: Test."
    assert lines[index + 2].startswith("      → A repository admin adds production")
    assert lines[index + 3].startswith("        Or, if production is one of those")
