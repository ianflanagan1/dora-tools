"""The collecting machine's own settings, from config.toml in dora-tools' folder, which git
ignores: which repositories it syncs, and where it keeps what it reads. They're personal, unlike
a repository's [dora] settings, which its team commits. config.example.toml shows them.
"""

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from cli_kit.outcome import Precondition

from dora import store

# The project root: .venv's parent, as `uv sync` builds it there.
CONFIG = Path(sys.prefix).parent / "config.toml"


@dataclass(frozen=True)
class Config:
    repos: tuple[str, ...] = ()
    """The repositories dora sync syncs when given none, as host/workspace/name."""
    database: Path | None = None
    """Where the data is kept; store.default_path() unless set."""

    @property
    def database_path(self) -> Path:
        return self.database or store.default_path()


def load(path: Path | None = None) -> Config:
    """The settings in path, by default dora-tools' config.toml; the defaults without one."""
    path = path or CONFIG
    try:
        settings = tomllib.loads(path.read_text())
    except FileNotFoundError:
        return Config()
    except tomllib.TOMLDecodeError as error:
        raise Precondition(f"{path} isn't valid TOML.", str(error)) from None
    if unknown := settings.keys() - {"repos", "database"}:
        raise Precondition(f"Unknown setting {', '.join(sorted(unknown))} in {path}.")
    repos = settings.get("repos", [])
    if not isinstance(repos, list) or not all(isinstance(r, str) and r for r in repos):
        raise Precondition(f"repos in {path} must be a list of host/workspace/name.")
    database = settings.get("database")
    if database is not None and not (isinstance(database, str) and database):
        raise Precondition(f"database in {path} must be a path.")
    return Config(tuple(repos), Path(database).expanduser() if database else None)
