"""A repository measured: which one, its git host, and what it says about itself in the
.config/ianf-dora.toml committed on its main branch, as docs/requirements.md (R1) describes it.

    version = 1                              # needed: the version of the file's format
    production = "production"                # needed: the production deployment environment
    failure_label = "production-failure"     # the label marking a production failure
    production_branch = "main"               # what production is deployed from; the repo's
                                             # main branch unless set
    integration_branches = ["develop"]       # branches that gather changes on their way to
                                             # production; none unless set
    doc = "docs/dora-metrics.md"             # the team's document (R2)

A repository is named by its git host's domain and its name, bitbucket.org/workspace/name, or
found from a clone's origin. Its settings are read from the git host, not a clone, so they're
what's committed, and no clone is needed.
"""

import subprocess
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import assert_never

import repo_settings
from cli_kit.outcome import Precondition
from jira_tools import config as jira_config
from repo_settings import Invalid, Missing, RemoteFiles, Source, Text, Unlocatable
from repo_tools import clone
from repo_tools.bitbucket import api as bitbucket
from repo_tools.bitbucket.git_host_api import Bitbucket
from repo_tools.git_host_api import GitHostApi

FILE = ".config/ianf-dora.toml"
TRACKER = jira_config.FILE
TEXT = ("production", "failure_label", "production_branch", "doc")
LISTS = ("integration_branches",)
GIT_HOSTS = {"bitbucket.org": Bitbucket}


class Version(IntEnum):
    """The versions of ianf-dora.toml's format dora-tools reads."""

    V1 = 1


@dataclass(frozen=True)
class Ref:
    """A repository on a git host."""

    domain: str
    name: str
    """workspace/name on Bitbucket; owner/name on GitHub."""

    def __str__(self) -> str:
        return f"{self.domain}/{self.name}"


@dataclass(frozen=True)
class Settings:
    """The settings in ianf-dora.toml, with defaults for what it leaves out."""

    production: str
    failure_label: str = "production-failure"
    production_branch: str | None = None
    """None for the repository's main branch."""
    integration_branches: tuple[str, ...] = ()
    doc: str = "docs/dora-metrics.md"


@dataclass(frozen=True)
class Project:
    ref: Ref
    main_branch: str
    settings: Settings | None
    """None when ianf-dora.toml is missing or wrong; problem says why."""
    problem: tuple[str, ...] = ()
    """What's wrong with ianf-dora.toml, as a message and its details, when settings is None."""
    tracker: str | Invalid | None = None
    """The text of the committed .config/ianf-tracker.toml, None when there's none, or Invalid
    when it's committed both there and at the root."""

    @property
    def repo(self) -> str:
        return self.ref.name

    @property
    def production_branch(self) -> str:
        if self.settings and self.settings.production_branch:
            return self.settings.production_branch
        return self.main_branch


def parse(text: str) -> Ref:
    """The repository text names, as git-host/name, such as bitbucket.org/team/app. Raises
    Precondition for a git host that isn't supported or a name that isn't one."""
    domain, _, name = text.strip().strip("/").partition("/")
    if domain not in GIT_HOSTS:
        raise Precondition(
            f"{text} isn't a repository on a supported git host.",
            f"Name it as {' or '.join(f'{h}/<workspace>/<name>' for h in GIT_HOSTS)}.",
        )
    if name.count("/") != 1 or not all(name.split("/")):
        raise Precondition(f"{text} isn't git-host/workspace/name.")
    return Ref(domain, name.lower())


def from_clone(directory: Path) -> Ref:
    """The repository directory's clone is of, by its origin. Raises Precondition outside a git
    repository, or for one without an origin on a supported git host."""
    result = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise Precondition(f"{directory} is not in a git repository.")
    root = Path(result.stdout.strip())
    origin = clone.origin_url(root)
    if origin is None:
        raise Precondition(f"{root} has no origin remote.")
    if "bitbucket.org" not in origin:
        raise Precondition(
            f"origin is {origin}.", "dora supports repositories on Bitbucket, for now."
        )
    return Ref("bitbucket.org", bitbucket.repo_from_url(origin).lower())


def resolve(given: str | None) -> Ref:
    """The repository a command is given: by name, as git-host/workspace/name, or as a clone's
    folder; the clone the current folder is in when it's given none."""
    if given is None or Path(given).is_dir():
        return from_clone(Path(given or "."))
    return parse(given)


def git_host(ref: Ref) -> GitHostApi:
    """The git host ref is on."""
    return GIT_HOSTS[ref.domain]()


def load(ref: Ref, git_host: GitHostApi) -> Project:
    """The project ref names, with the settings committed on its main branch. A missing or wrong
    ianf-dora.toml is the project's problem, since that's one of the requirements."""
    files = HostedFiles(ref, git_host)
    main = files.default_branch()
    dora = _committed(files, FILE)
    if isinstance(dora, Invalid):
        settings, problem = None, (dora.message, *dora.details)
    else:
        settings, problem = read_settings(dora, main)
    return Project(ref, main, settings, problem, _committed(files, TRACKER))


class HostedFiles(RemoteFiles):
    """The repository ref names, read from its git host. The git host is asked for its main branch
    once."""

    def __init__(self, ref: Ref, git_host: GitHostApi) -> None:
        self.name = str(ref)
        self._ref = ref
        self._git_host = git_host
        self._main: str | None = None

    def default_branch(self) -> str:
        if self._main is None:
            self._main = self._git_host.main_branch(self._ref.name)
        return self._main

    def file(self, revision: str, path: str) -> str | None:
        return self._git_host.file(self._ref.name, revision, path)


def _committed(files: HostedFiles, file: str) -> str | Invalid | None:
    """The text of file committed on the main branch, None when there's none, or Invalid when
    it's committed both in .config and at the root."""
    found = repo_settings.fetch(Source.REMOTE_DEFAULT_BRANCH, files, file)
    match found:
        case Text():
            return found.text
        case Missing():
            return None
        case Invalid():
            return found
        case Unlocatable():
            raise AssertionError("A remote copy is always found, or its git host raises.")
        case _:
            assert_never(found)


def read_settings(text: str | None, branch: str) -> tuple[Settings | None, tuple[str, ...]]:
    """The settings in an ianf-dora.toml's text, or None and what's wrong with them."""
    if text is None:
        return None, (f"No {FILE} on {branch}.",)
    read = repo_settings.loads(text, FILE, Version)
    if isinstance(read, repo_settings.Invalid):
        return None, (read.message, *read.details)
    match read.version:
        case Version.V1:
            return _settings(read.table)
        case _:
            assert_never(read.version)


def _settings(table: dict) -> tuple[Settings | None, tuple[str, ...]]:
    if unknown := table.keys() - {*TEXT, *LISTS}:
        return None, (f"Unknown setting {', '.join(sorted(unknown))} in {FILE}.",)
    if "production" not in table:
        return None, (f"{FILE} doesn't set production, the production deployment environment.",)
    if wrong := sorted(k for k in TEXT if k in table and not _text(table[k])):
        return None, (f"{', '.join(wrong)} in {FILE} must be text.",)
    if wrong := sorted(k for k in LISTS if k in table and not _texts(table[k])):
        return None, (f"{', '.join(wrong)} in {FILE} must be a list of branch names.",)
    lists = {k: tuple(table[k]) for k in LISTS if k in table}
    return Settings(**{**table, **lists}), ()


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _texts(value: object) -> bool:
    return isinstance(value, list) and all(_text(item) for item in value)
