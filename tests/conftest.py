"""A fake Bitbucket and Jira for the checks, and a project made from them."""

from datetime import UTC, datetime

import pytest
from cli_kit.outcome import Precondition
from jira_tools.api import LinkType, User
from repo_tools.bitbucket import api as bitbucket
from repo_tools.bitbucket.forge import Bitbucket

from dora.checks import Context
from dora.project import Project, Ref, Settings

SINCE = datetime(2026, 7, 1, tzinfo=UTC)


class FakeBitbucket:
    """A repo that meets every Bitbucket requirement until a test changes it."""

    def __init__(self) -> None:
        self.enabled: bool | None = True  # None: reading the setting is forbidden
        self.runs = [{"uuid": "{1}"}]
        self.environment_names = ["Test", "Staging", "Production"]
        self.main_branch = "main"
        self.files = {("main", "bitbucket-pipelines.yml"): "      deployment: production\n"}
        self.pulls: list[dict] = []
        self.by_hash: dict[str, dict] = {}
        self.forbidden: set[str] = set()
        self.squashed: set[str] = set()  # source commits a merge didn't keep

    def _guard(self, what: str) -> None:
        if what in self.forbidden:
            raise bitbucket.Forbidden("Bitbucket answered 403.")

    def pipelines_enabled(self, repo):
        if self.enabled is None:
            raise bitbucket.Forbidden("Bitbucket answered 403.")
        return self.enabled

    def pipelines(self, repo):
        return iter(self.runs)

    def environments(self, repo):
        self._guard("environments")
        return [{"name": name} for name in self.environment_names]

    def file(self, repo, revision, path):
        return self.files.get((revision, path))

    def repository(self, repo):
        self._guard("repository")
        return {"full_name": repo, "mainbranch": {"name": self.main_branch}}

    def merged_pull_requests(self, repo, updated_since, base):
        self._guard("pullrequests")
        return iter(self.pulls)

    def commit(self, repo, revision):
        return self.by_hash[revision]

    def commits(self, repo, include, exclude):
        """The commits in include and not exclude: a squashed source's head is one of them."""
        return iter([{"hash": include}] if include in self.squashed else [])

    def merge(
        self,
        number,
        source,
        target,
        *,
        parents=1,
        message=None,
        title=None,
        squashed=False,
        opened,
        merged,
    ):
        """Records a merged pull request and its merge commit. squashed: the merge didn't keep
        the source's commits."""
        sha = f"{number:012d}".ljust(40, "0")  # unique in its first 12, as short hashes are
        self.pulls.append(
            {
                "id": number,
                "title": title or f"PR {number}",
                "source": {"branch": {"name": source}, "commit": {"hash": f"head{number}"}},
                "destination": {"branch": {"name": target}},
                "created_on": opened.isoformat(),
                "merge_commit": {"hash": sha[:12]},
            }
        )
        if squashed:
            self.squashed.add(f"head{number}")
        self.by_hash[sha[:12]] = {
            "hash": sha,
            "date": merged.isoformat(),
            "parents": [{"hash": "p"}] * parents,
            "message": message or f"Merged in {source} (pull request #{number})\n\nPR {number}",
        }


class FakeJira:
    """A Jira site that meets every Jira requirement until a test changes it."""

    def __init__(self) -> None:
        self.spaces = {"KAN": "Kanban"}
        self.types = [
            LinkType("1", "Relates", "relates to", "relates to"),
            LinkType("2", "Causes", "is caused by", "causes"),
        ]
        self.fields = ["Summary", "Detected"]
        self.issues: list[dict] = []
        self.links: dict[str, list[dict]] = {}
        self.signed_in = True

    def space_name(self, space):
        return self.spaces.get(space)

    def link_types(self):
        return self.types

    def field_names(self):
        return self.fields

    def myself(self):
        if not self.signed_in:
            raise Precondition("No Jira API token is stored.", "Create one at …")
        return User("1", "Ian Flanagan")

    def with_label(self, space, label):
        return [i for i in self.issues if label in i["fields"]["labels"]]

    def web_links(self, key):
        return self.links.get(key, [])

    def failure(self, key, created, *, done=False, caused_by=None, label="production-failure"):
        """Records a production failure issue."""
        links = []
        if caused_by:
            links.append(
                {
                    "type": {"name": "Causes", "inward": "is caused by", "outward": "causes"},
                    "inwardIssue": {"key": caused_by},
                }
            )
        self.issues.append(
            {
                "key": key,
                "fields": {
                    "summary": f"Failure {key}",
                    "created": created.isoformat(),
                    "labels": [label],
                    "status": {"statusCategory": {"key": "done" if done else "new"}},
                    "issuelinks": links,
                },
            }
        )


@pytest.fixture
def bitbucket_fake() -> FakeBitbucket:
    return FakeBitbucket()


@pytest.fixture
def jira_fake() -> FakeJira:
    return FakeJira()


TRACKER = 'version = 1\nsite = "me.atlassian.net"\nspace = "KAN"\n'
DORA = 'version = 1\nproduction = "production"\n'


@pytest.fixture
def ctx(bitbucket_fake: FakeBitbucket, jira_fake: FakeJira) -> Context:
    """A project on the fake Bitbucket, committing the team's document and its settings."""
    bitbucket_fake.files[("main", "docs/dora-metrics.md")] = "# DORA metrics\n"
    bitbucket_fake.files[("main", ".config/ianf-tracker.toml")] = TRACKER
    bitbucket_fake.files[("main", ".config/ianf-dora.toml")] = DORA
    project = Project(
        Ref("bitbucket.org", "team/app"), "main", Settings(production="production"), (), TRACKER
    )
    host = Bitbucket(bitbucket_fake)
    return Context(project, SINCE, host, bitbucket_client=bitbucket_fake, jira_client=jira_fake)
