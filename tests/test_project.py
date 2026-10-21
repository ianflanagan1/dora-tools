"""Which repository is measured, and what it says about itself on its code host."""

import subprocess
from pathlib import Path

import pytest
from cli_kit.outcome import Precondition

from dora import project
from dora.project import Ref, Settings


class Host:
    """A code host with one repository, team/app, whose main branch is trunk."""

    def __init__(self, workspace: str | None) -> None:
        self.workspace = workspace

    def main_branch(self, repo):
        return "trunk"

    def file(self, repo, revision, path):
        assert (repo, revision, path) == ("team/app", "trunk", ".workspace.toml")
        return self.workspace


def load(workspace: str | None):
    return project.load(Ref("bitbucket.org", "team/app"), Host(workspace))


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
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
    loaded = load('[tracker]\nspace = "KAN"\n\n[dora]\nproduction = "prod"\n')
    assert loaded.main_branch == "trunk"
    assert loaded.settings == Settings(production="prod")
    assert loaded.production_branch == "trunk"


def test_a_production_branch_set_in_dora_overrides_the_main_branch():
    loaded = load('[dora]\nproduction = "prod"\nproduction_branch = "release"\n')
    assert loaded.production_branch == "release"


def test_reads_integration_branches_as_a_list():
    loaded = load('[dora]\nproduction = "prod"\nintegration_branches = ["develop", "release"]\n')
    assert loaded.settings.integration_branches == ("develop", "release")


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        (None, "No .workspace.toml on trunk"),
        ('[tracker]\nspace = "KAN"\n', "has no [dora] table"),
        ('[dora]\nproduction = "prod"\nprod = "x"\n', "Unknown setting prod"),
        ('[dora]\nfailure_label = "x"\n', "doesn't set production"),
        ("[dora]\nproduction = 1\n", "production must be text"),
        ('[dora]\nproduction = "p"\nintegration_branches = "develop"\n', "must be a list"),
        ("[dora\n", "isn't valid TOML"),
    ],
)
def test_a_missing_or_wrong_dora_table_is_the_projects_problem(text, problem):
    loaded = load(text)
    assert loaded.settings is None
    assert problem in loaded.problem[0]
