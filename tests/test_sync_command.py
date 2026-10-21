"""dora sync's choice of repositories and how it reports each."""

import pytest
from cli_kit import outcome
from cli_kit.outcome import Done, Failure, NothingToDo, Precondition

from dora import config
from dora.commands import sync as command
from dora.sync import Synced


@pytest.fixture
def synced(monkeypatch, tmp_path):
    """What each repository's sync records, by name; one named "broken" stops."""
    results: dict[str, Synced] = {}

    def sync_one(store, ref, now, since):
        if ref.name.endswith("broken"):
            raise Precondition("No .workspace.toml on main.")
        return results.get(ref.name, Synced())

    monkeypatch.setattr(command, "sync_one", sync_one)
    monkeypatch.setattr(
        config, "load", lambda: config.Config(("bitbucket.org/team/app",), tmp_path / "d.sqlite")
    )
    outcome.reset()
    return results


def test_with_no_repositories_named_or_configured_it_says_where_to_list_them(monkeypatch):
    monkeypatch.setattr(config, "load", lambda: config.Config())
    with pytest.raises(Precondition, match="No repositories to sync"):
        command.run([])


def test_with_none_named_it_syncs_the_configured_ones(synced, capsys):
    synced["team/app"] = Synced(deployments=2)
    result = command.run([])
    assert isinstance(result, Done)
    assert "bitbucket.org/team/app: 2 new deployments" in capsys.readouterr().out


def test_with_nothing_new_it_has_nothing_to_do(synced, capsys):
    assert isinstance(command.run(["bitbucket.org/team/app"]), NothingToDo)
    assert "bitbucket.org/team/app: up to date" in capsys.readouterr().out


def test_warnings_are_shown_even_when_nothing_is_new(synced, capsys):
    synced["team/app"] = Synced(warnings=["The last sync was 20 days ago: …"])
    command.run(["bitbucket.org/team/app"])
    assert "  The last sync was 20 days ago: …" in capsys.readouterr().out


def test_a_repository_that_fails_doesnt_stop_the_others(synced, capsys):
    synced["team/app"] = Synced(pulls=1)
    with pytest.raises(Failure) as failed:
        command.run(["bitbucket.org/team/broken", "bitbucket.org/team/app"])
    assert failed.value.message == "1 of 2 didn't sync."
    assert failed.value.details == ("bitbucket.org/team/broken: No .workspace.toml on main.",)
    assert "bitbucket.org/team/app: 1 merged pull request" in capsys.readouterr().out
