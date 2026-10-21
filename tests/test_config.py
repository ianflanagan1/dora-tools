"""The collecting machine's own settings."""

from pathlib import Path

import pytest
from cli_kit.outcome import Precondition

from dora import config, store


def test_without_a_file_nothing_is_synced_and_the_database_is_in_the_users_data(tmp_path):
    settings = config.load(tmp_path / "config.toml")
    assert settings.repos == ()
    assert settings.database_path == store.default_path()


def test_the_database_follows_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert store.default_path() == tmp_path / "dora" / "dora.sqlite"


def test_the_database_is_under_local_share_without_xdg_data_home(monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert store.default_path() == Path.home() / ".local" / "share" / "dora" / "dora.sqlite"


def test_reads_the_repos_and_a_database_with_home_expanded(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('repos = ["bitbucket.org/team/app"]\ndatabase = "~/dora.sqlite"\n')
    settings = config.load(path)
    assert settings.repos == ("bitbucket.org/team/app",)
    assert settings.database_path == Path.home() / "dora.sqlite"


@pytest.mark.parametrize(
    "text", ['repos = "bitbucket.org/team/app"\n', "database = 1\n", "rpos = []\n", "repos = [\n"]
)
def test_wrong_settings_stop_it(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    with pytest.raises(Precondition):
        config.load(path)


def test_the_example_is_valid():
    example = Path(config.CONFIG).with_name("config.example.toml")
    assert config.load(example).repos == ("bitbucket.org/your-workspace/your-repo",)
