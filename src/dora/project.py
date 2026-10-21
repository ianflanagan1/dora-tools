"""A repository measured: which one, its code host, and what it says about itself in the [dora]
table of the .workspace.toml committed on its main branch, as docs/requirements.md (R1)
describes it.

    [dora]
    production = "production"                # needed: the production deployment environment
    failure_label = "production-failure"     # the label marking a production failure
    production_branch = "main"               # what production is deployed from; the repo's
                                             # main branch unless set
    integration_branches = ["develop"]       # branches that gather changes on their way to
                                             # production; none unless set
    doc = "docs/dora-metrics.md"             # the team's document (R2)

A repository is named by its host and name, bitbucket.org/workspace/name, or found from a clone's
origin. Its settings are read from the host, not a clone, so they're what's committed, and no
clone is needed.
"""

import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from cli_kit.outcome import Precondition
from repo_tools import clone
from repo_tools.bitbucket import api as bitbucket
from repo_tools.bitbucket.forge import Bitbucket
from repo_tools.forge import Forge

FILE = ".workspace.toml"
TEXT = ("production", "failure_label", "production_branch", "doc")
LISTS = ("integration_branches",)
HOSTS = {"bitbucket.org": Bitbucket}


@dataclass(frozen=True)
class Ref:
    """A repository on a code host."""

    host: str
    name: str
    """workspace/name on Bitbucket; owner/name on GitHub."""

    def __str__(self) -> str:
        return f"{self.host}/{self.name}"


@dataclass(frozen=True)
class Settings:
    """The [dora] table, with defaults for what it leaves out."""

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
    """None when the [dora] table is missing or wrong; problem says why."""
    problem: tuple[str, ...] = ()
    """What's wrong with the [dora] table, as a message and its details, when settings is None."""
    workspace: str | None = None
    """The text of the committed .workspace.toml, or None when there's none."""

    @property
    def repo(self) -> str:
        return self.ref.name

    @property
    def production_branch(self) -> str:
        if self.settings and self.settings.production_branch:
            return self.settings.production_branch
        return self.main_branch


def parse(text: str) -> Ref:
    """The repository text names, as host/name, such as bitbucket.org/team/app. Raises
    Precondition for a host that isn't supported or a name that isn't one."""
    host, _, name = text.strip().strip("/").partition("/")
    if host not in HOSTS:
        raise Precondition(
            f"{text} isn't a repository on a supported code host.",
            f"Name it as {' or '.join(f'{h}/<workspace>/<name>' for h in HOSTS)}.",
        )
    if name.count("/") != 1 or not all(name.split("/")):
        raise Precondition(f"{text} isn't host/workspace/name.")
    return Ref(host, name.lower())


def from_clone(directory: Path) -> Ref:
    """The repository directory's clone is of, by its origin. Raises Precondition outside a git
    repository, or for one without an origin on a supported host."""
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
    """The repository a command is given: by name, as host/workspace/name, or as a clone's
    folder; the clone the current folder is in when it's given none."""
    if given is None or Path(given).is_dir():
        return from_clone(Path(given or "."))
    return parse(given)


def forge(ref: Ref) -> Forge:
    """The code host ref is on."""
    return HOSTS[ref.host]()


def load(ref: Ref, host: Forge) -> Project:
    """The project ref names, with the settings committed on its main branch. A missing or wrong
    [dora] table is the project's problem, since that's one of the requirements."""
    main = host.main_branch(ref.name)
    text = host.file(ref.name, main, FILE)
    settings, problem = read_settings(text, main)
    return Project(ref, main, settings, problem, text)


def read_settings(text: str | None, branch: str) -> tuple[Settings | None, tuple[str, ...]]:
    """The [dora] settings in a .workspace.toml's text, or None and what's wrong with them."""
    if text is None:
        return None, (f"No {FILE} on {branch}.",)
    try:
        table = tomllib.loads(text).get("dora")
    except tomllib.TOMLDecodeError as error:
        return None, (f"{FILE} isn't valid TOML.", str(error))
    if not isinstance(table, dict):
        return None, (f"{FILE} has no [dora] table.",)
    if unknown := table.keys() - {*TEXT, *LISTS}:
        return None, (f"Unknown setting {', '.join(sorted(unknown))} in [dora].",)
    if "production" not in table:
        return None, ("[dora] doesn't set production, the production deployment environment.",)
    if wrong := sorted(k for k in TEXT if k in table and not _text(table[k])):
        return None, (f"[dora] {', '.join(wrong)} must be text.",)
    if wrong := sorted(k for k in LISTS if k in table and not _texts(table[k])):
        return None, (f"[dora] {', '.join(wrong)} must be a list of branch names.",)
    lists = {k: tuple(table[k]) for k in LISTS if k in table}
    return Settings(**{**table, **lists}), ()


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _texts(value: object) -> bool:
    return isinstance(value, list) and all(_text(item) for item in value)
