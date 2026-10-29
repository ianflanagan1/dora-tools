"""Which repository is measured, and what it says about itself on its code host."""

import json
import subprocess
from pathlib import Path

import pytest
from cli_kit.outcome import Precondition

from dora import project
from dora.project import Ref, Settings

TRACKER = 'version = 1\nspace = "KAN"\n'


class Host:
    """A code host with one repository, team/app, whose main branch is trunk, committing the
    files given by path."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def main_branch(self, repo):
        return "trunk"

    def file(self, repo, revision, path):
        assert (repo, revision) == ("team/app", "trunk")
        return self.files.get(path)


def load(dora: str | None, tracker: str | None):
    files = {".config/ianf-dora.toml": dora, ".config/ianf-tracker.toml": tracker}
    present = {path: text for path, text in files.items() if text is not None}
    return project.load(Ref("bitbucket.org", "team/app"), Host(present))


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", "git@bitbucket.org:Team/App.git"],
        check=True,
    )
    return tmp_path


def test_a_repository_is_named_by_host_and_name_in_lower_case():
    assert project.parse("bitbucket.org/Team/App") == Ref("bitbucket.org", "team/app")
    assert str(Ref("bitbucket.org", "team/app")) == "bitbucket.org/team/app"


@pytest.mark.parametrize("text", ["gitlab.com/team/app", "bitbucket.org/app", "team/app"])
def test_a_name_not_on_a_supported_host_is_refused(text):
    with pytest.raises(Precondition):
        project.parse(text)


def test_a_clone_names_its_repository_by_origin(clone):
    assert project.from_clone(clone) == Ref("bitbucket.org", "team/app")


def test_a_folder_inside_a_clone_names_the_clones_repository(clone):
    (clone / "src").mkdir()
    assert project.resolve(str(clone / "src")) == Ref("bitbucket.org", "team/app")


def test_without_a_name_the_current_folders_clone_is_meant(clone, monkeypatch):
    monkeypatch.chdir(clone)
    assert project.resolve(None) == Ref("bitbucket.org", "team/app")


def test_outside_a_repository_it_stops(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(Precondition, match="not in a git repository"):
        project.resolve(None)


def test_a_clone_not_on_bitbucket_stops(clone):
    subprocess.run(
        ["git", "-C", str(clone), "remote", "set-url", "origin", "git@github.com:me/app.git"],
        check=True,
    )
    with pytest.raises(Precondition) as stopped:
        project.from_clone(clone)
    assert "Bitbucket" in stopped.value.details[0]


def test_the_settings_are_read_from_the_main_branch_with_defaults(tmp_path):
    loaded = load('version = 1\nproduction = "prod"\n', TRACKER)
    assert loaded.main_branch == "trunk"
    assert loaded.settings == Settings(production="prod")
    assert loaded.production_branch == "trunk"


def test_a_production_branch_set_in_dora_overrides_the_main_branch():
    loaded = load('version = 1\nproduction = "prod"\nproduction_branch = "release"\n', TRACKER)
    assert loaded.production_branch == "release"


def test_reads_integration_branches_as_a_list():
    loaded = load(
        'version = 1\nproduction = "prod"\nintegration_branches = ["develop", "release"]\n', TRACKER
    )
    assert loaded.settings.integration_branches == ("develop", "release")


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        (None, "No .config/ianf-dora.toml on trunk"),
        ('production = "prod"\n', "No version in .config/ianf-dora.toml"),
        ('version = 2\nproduction = "prod"\n', "is version 2, which this tool doesn't read"),
        ('version = 1\nproduction = "prod"\nprod = "x"\n', "Unknown setting prod"),
        ('version = 1\nfailure_label = "x"\n', "doesn't set production"),
        ("version = 1\nproduction = 1\n", "production in .config/ianf-dora.toml must be text"),
        ('version = 1\nproduction = "p"\nintegration_branches = "develop"\n', "must be a list"),
        ("version = \n", "isn't valid TOML"),
    ],
)
def test_a_missing_or_wrong_dora_file_is_the_projects_problem(text, problem):
    loaded = load(text, TRACKER)
    assert loaded.settings is None
    assert problem in loaded.problem[0]


def test_the_committed_tracker_file_is_kept_for_the_tracker_checks():
    assert load('version = 1\nproduction = "prod"\n', TRACKER).tracker == TRACKER
    assert load('version = 1\nproduction = "prod"\n', None).tracker is None


def test_the_schema_has_the_settings_dora_reads():
    schema = json.loads((Path(__file__).parent.parent / "schema" / "ianf-dora.v1.json").read_text())
    assert set(schema["properties"]) == {"version", *project.TEXT, *project.LISTS}
