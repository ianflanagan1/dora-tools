"""Production failures as the tracker records them: issues marked with the failure label (T2),
each with its causes (H5), as plain records whichever tracker it is. Jira is the only tracker so
far; another would read its own issues into the same records.
"""

import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cli_kit.outcome import Precondition
from jira_tools import config as jira_config
from jira_tools import session as jira_session
from jira_tools.api import Jira

from dora.project import Project

CAUSED_BY = "is caused by"


@dataclass(frozen=True)
class Cause:
    kind: str
    """"issue", the key of the issue whose work introduced the failure, or "link", a web link
    to the commit or pull request that did."""
    target: str


@dataclass(frozen=True)
class Failure:
    key: str
    summary: str
    created_at: datetime
    """When it was recorded, taken as when it was detected."""
    done: bool
    causes: tuple[Cause, ...] = ()


def settings(project: Project) -> jira_config.Config:
    """The settings in the project's committed .config/ianf-tracker.toml."""
    if project.tracker is None:
        raise Precondition(f"No {jira_config.FILE} on {project.main_branch}.")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "ianf-tracker.toml"
        path.write_text(project.tracker)
        return jira_config.load(path)


def connect(tracker: jira_config.Config) -> Jira:
    return jira_session.connect(tracker)


def failures(jira: Jira, space: str, label: str) -> list[Failure]:
    """Every issue in space with label, as a failure with its causes, newest first."""
    found = []
    for issue in jira.with_label(space, label):
        fields = issue["fields"]
        causes = [
            Cause("issue", link["inwardIssue"]["key"])
            for link in fields.get("issuelinks", [])
            if "inwardIssue" in link and link["type"]["inward"].casefold() == CAUSED_BY
        ]
        causes += [
            Cause("link", web_link["object"]["url"])
            for web_link in jira.web_links(issue["key"]) or []
            if web_link.get("object", {}).get("title", "").casefold().startswith("caused by")
        ]
        found.append(
            Failure(
                key=issue["key"],
                summary=fields.get("summary", ""),
                created_at=datetime.fromisoformat(fields["created"]).astimezone(UTC),
                done=fields["status"].get("statusCategory", {}).get("key") == "done",
                causes=tuple(causes),
            )
        )
    return found
