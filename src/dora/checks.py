"""Every requirement the DORA metrics have, as docs/requirements.md lists them, with a check for
each one that can be checked. The code host's (C) are checked in Bitbucket and the tracker's (T)
in Jira, the providers supported so far.

Settings are checked as they are now. Habits are checked in what happened since a time: the pull
requests merged and the production failures recorded since then. No check assumes a branching
model or merge strategy: those the metrics can't work with are what's checked. A check never
changes anything in Bitbucket or Jira.
"""

import platform
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from functools import cached_property

from cli_kit.outcome import Precondition, Problem
from jira_tools import config as jira_config
from jira_tools import session as jira_session
from jira_tools.api import Jira
from repo_tools.bitbucket import api as bitbucket
from repo_tools.forge import Forge

from dora import tracker
from dora.project import FILE, TRACKER, Project, Settings

KEY = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
REUSE_AFTER = timedelta(days=14)
"""How long after a failure's first fix merged a later one with its key looks like reuse."""
TOKENS = "https://id.atlassian.com/manage-profile/security/api-tokens"
GITHUB = "https://github.com/ianflanagan1"
TOOL_REPOSITORIES = {"bitbucket": "repo-tools", "jira": "jira-tools"}
"""The repository each command dora needs on the PATH comes from."""


class Status(StrEnum):
    MET = "met"
    MISSED = "missed"
    REVIEW = "review"
    """Something a person should look at: the check can't tell whether it's a miss."""
    UNCHECKED = "unchecked"
    """The check couldn't run, such as for want of a token or a scope."""
    OPTIONAL = "optional"
    """An optional requirement that isn't met."""


@dataclass(frozen=True)
class Result:
    status: Status
    summary: str
    details: tuple[str, ...] = ()
    """What was found."""
    fix: tuple[str, ...] = ()
    """How to put it right, or what to do next time when what happened can't be changed."""


def met(summary: str, *details: str) -> Result:
    return Result(Status.MET, summary, details)


def missed(summary: str, *details: str, fix: list[str]) -> Result:
    """A requirement not met. fix is required: every miss says what to do about it."""
    return Result(Status.MISSED, summary, details, tuple(fix))


def review(summary: str, *details: str, fix: list[str]) -> Result:
    return Result(Status.REVIEW, summary, details, tuple(fix))


def optional(summary: str, *details: str, fix: list[str]) -> Result:
    return Result(Status.OPTIONAL, summary, details, tuple(fix))


@dataclass
class Context:
    """What the checks read, each fetched once, when a check first needs it. bitbucket_client
    and jira_client replace signing in, for tests."""

    project: Project
    since: datetime
    host: Forge
    """The code host the project is on."""
    bitbucket_client: bitbucket.Bitbucket | None = None
    jira_client: Jira | None = None

    @property
    def settings(self) -> Settings:
        if self.project.settings is None:
            raise Precondition("Needs R1: .config/ianf-dora.toml.")
        return self.project.settings

    @cached_property
    def bitbucket(self) -> bitbucket.Bitbucket:
        return self.bitbucket_client or bitbucket.connect()

    @property
    def production_branch(self) -> str:
        """The branch production is deployed from: production_branch in ianf-dora.toml, or the
        repo's main branch."""
        return self.project.production_branch

    @cached_property
    def tracker(self) -> jira_config.Config:
        """The settings in the committed .config/ianf-tracker.toml."""
        return tracker.settings(self.project)

    @cached_property
    def jira(self) -> Jira:
        return self.jira_client or jira_session.connect(self.tracker)

    @cached_property
    def merged(self) -> list[dict]:
        """The pull requests merged since since, each with its merge commit under "commit"."""
        pulls = self.bitbucket.merged_pull_requests(self.project.repo, self.since.isoformat(), None)
        found = []
        for pull in pulls:
            merge = (pull.get("merge_commit") or {}).get("hash")
            if merge:
                found.append({**pull, "commit": self.bitbucket.commit(self.project.repo, merge)})
        return found

    @cached_property
    def failures(self) -> list[dict]:
        """The production failures recorded since since."""
        issues = self.jira.with_label(self.tracker.space, self.settings.failure_label)
        return [i for i in issues if _time(i["fields"]["created"]) >= self.since]

    def pulls_with(self, key: str) -> list[dict]:
        """The merged pull requests that carry key: in their branch name, their title or their
        merge commit's message."""
        return [p for p in self.merged if key in KEY.findall(_carried(p))]


@dataclass(frozen=True)
class Requirement:
    id: str
    title: str
    check: Callable[[Context], Result] | None = None
    """None for a requirement nothing can check; why is in note."""
    note: str = ""


# Settings


def pipelines_on(ctx: Context) -> Result:
    repo = ctx.project.repo
    try:
        if ctx.bitbucket.pipelines_enabled(repo):
            return met("Pipelines is on.")
        return missed(
            "Pipelines is off.",
            fix=["A repository admin turns it on: Repository settings → Pipelines → Settings."],
        )
    except bitbucket.Forbidden:
        pass
    if next(iter(ctx.bitbucket.pipelines(repo)), None) is not None:
        return met(
            "Pipelines has run, so it's on.",
            "Reading the setting itself needs the admin:repository:bitbucket scope.",
        )
    return Result(
        Status.UNCHECKED,
        "Pipelines has never run, and reading the setting needs admin:repository:bitbucket.",
        fix=("It's checked once a pipeline has run.",),
    )


def production_environment(ctx: Context) -> Result:
    wanted = ctx.settings.production
    names = [e["name"] for e in ctx.bitbucket.environments(ctx.project.repo)]
    if any(name.casefold() == wanted.casefold() for name in names):
        return met(f"The environment {wanted} exists.")
    fix = [f"A repository admin adds {wanted} in Repository settings → Pipelines → Deployments."]
    if names:
        choices = ", ".join(names)
        fix.append(
            f"Or, if production is one of those, set production in {FILE} to one of: {choices}."
        )
    return missed(f"No environment {wanted}.", f"It has: {', '.join(names) or 'none'}.", fix=fix)


def deployment_step(ctx: Context) -> Result:
    branch, wanted = ctx.production_branch, ctx.settings.production
    text = ctx.bitbucket.file(ctx.project.repo, branch, "bitbucket-pipelines.yml")
    fix = [
        f"Commit to {branch}'s bitbucket-pipelines.yml a step with `deployment: {wanted}` that"
        " deploys production.",
        f"Or, if production is deployed from another branch, set production_branch in {FILE}"
        " to it.",
    ]
    if text is None:
        return missed(f"No bitbucket-pipelines.yml on {branch}, or no branch {branch}.", fix=fix)
    step = re.compile(rf"^\s*deployment:\s*['\"]?{re.escape(wanted)}['\"]?\s*$", re.I | re.M)
    if step.search(text):
        return met(
            f"A step deploys to {wanted} in {branch}'s bitbucket-pipelines.yml.",
            "That nothing else deploys to production can't be checked (H4).",
        )
    return missed(
        f"No step in {branch}'s bitbucket-pipelines.yml has deployment: {wanted}.", fix=fix
    )


def dora_table(ctx: Context) -> Result:
    if ctx.project.settings is None:
        return missed(
            *ctx.project.problem,
            fix=[
                f"Commit to {ctx.project.main_branch} a {FILE} with at least:",
                'version = 1, production = "<your production deployment environment>"',
                "dora-tools' docs/requirements.md, under R1, lists every setting.",
            ],
        )
    settings = ctx.project.settings
    through = ", ".join(settings.integration_branches) or "no integration branch"
    return met(
        f'production = "{settings.production}", failure_label = "{settings.failure_label}".',
        f"Production deploys from {ctx.production_branch}; changes reach it through {through}.",
    )


def team_document(ctx: Context) -> Result:
    doc, branch = ctx.settings.doc, ctx.project.main_branch
    if ctx.host.file(ctx.project.repo, branch, doc) is not None:
        return met(f"{doc} is on {branch}.", "Whether the team has read it can't be checked.")
    return missed(
        f"No {doc} on {branch}.",
        fix=[
            f"Commit {doc} to {branch} for the team: how the metrics apply to your process and"
            " what each person does (dora-tools' docs/requirements.md, R2).",
            f"Or, if it's elsewhere, set doc in {FILE} to its path.",
        ],
    )


def jira_space(ctx: Context) -> Result:
    space = ctx.tracker.space
    name = ctx.jira.space_name(space)
    if name is None:
        return missed(
            f"No space {space} the Jira token can see.",
            fix=[
                f"Set space in {TRACKER} to the key of your space, as in KAN-12.",
                "Or ask a Jira admin to let your account browse it.",
            ],
        )
    return met(f"Issues go to {space} ({name}).")


def causes_link_type(ctx: Context) -> Result:
    types = ctx.jira.link_types()
    for link_type in types:
        if link_type.inward.casefold() == "is caused by":
            return met(f"{link_type.name}: {link_type.outward} / {link_type.inward}.")
    return optional(
        'No link type reads "is caused by"; causes can be recorded as web links instead.',
        f"The site has: {', '.join(t.name for t in types)}.",
        fix=[
            "To record a cause as a link to the issue whose work introduced it, a Jira site admin"
            " adds it: Settings → System → Issue linking → Add link type: name Causes, outward"
            " causes, inward is caused by. dora-tools can add it through Jira's API, with a site"
            " admin's consent."
        ],
    )


def detected_field(ctx: Context) -> Result:
    if any(name.casefold() == "detected" for name in ctx.jira.field_names()):
        return met("The site has a Detected field.")
    return optional("No Detected field; it's for later.", fix=[])


def tools_set_up(ctx: Context) -> Result:
    if missing := [tool for tool in ("bitbucket", "jira") if shutil.which(tool) is None]:
        # dora has its own pinned copy of each tool's library, not a checkout to point at, so
        # it says where to clone one. Setup links the tool's commands onto the PATH, and says
        # so if they still won't run.
        fix = []
        for tool in missing:
            name = TOOL_REPOSITORIES[tool]
            home = f"~/projects/cli/{name}"
            fix.append(f"git clone {GITHUB}/{name} {home} (unless it's there already)")
            fix.append(f"cd {home} && devbox run setup")
        return missed(f"Not on the PATH: {', '.join(missing)}.", fix=fix)
    return met("bitbucket and jira are on the PATH.")


def bitbucket_token(ctx: Context) -> Result:
    try:
        client = ctx.bitbucket
    except Precondition as problem:
        return missed(problem.message, fix=list(problem.details))
    repo = ctx.project.repo
    probes = {
        "read:repository:bitbucket": lambda: client.repository(repo),
        "read:pullrequest:bitbucket": lambda: next(
            iter(client.merged_pull_requests(repo, ctx.since.isoformat(), None)), None
        ),
        "read:pipeline:bitbucket": lambda: client.environments(repo),
    }
    lacking = []
    for scope, probe in probes.items():
        try:
            probe()
        except bitbucket.Forbidden:
            lacking.append(scope)
    if lacking:
        return missed(
            f"The token lacks {', '.join(lacking)}.",
            fix=[
                f"Create a token with scopes {', '.join(probes)}, at {TOKENS}.",
                f"Delete the stored one: {_delete_token('bitbucket-api-token')}",
                f"Then run `bitbucket environments {repo}`, which says how to store the new one.",
            ],
        )
    return met("The token can read the repository, its deployments and its pull requests.")


def jira_token(ctx: Context) -> Result:
    try:
        user = ctx.jira.myself()
    except Precondition as problem:
        return missed(problem.message, fix=list(problem.details))
    return met(f"Signed in as {user.name}.")


def daily_job(ctx: Context) -> Result:
    crontab = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
    if crontab.returncode == 0 and "dora sync" in crontab.stdout:
        return met("The crontab runs dora sync.")
    return missed(
        "No daily job runs dora sync.",
        fix=[
            "Nothing to do yet: dora sync isn't built. Once it is, dora installs the daily job,"
            " with your consent."
        ],
    )


# Habits


def history_kept(ctx: Context) -> Result:
    production = ctx.production_branch
    named = set(ctx.settings.integration_branches)
    targets = {p["destination"]["branch"]["name"] for p in ctx.merged}
    # A branch other pull requests merge into, merged into production itself, gathers changes
    # on their way there, whatever it's called and whether or not it's named.
    detected = {
        _source(p)
        for p in ctx.merged
        if p["destination"]["branch"]["name"] == production and _source(p) in targets
    } - named
    integration = named | detected
    notes = [
        f"{branch} gathers changes on their way to {production}: name it in integration_branches"
        f" in {FILE}."
        for branch in sorted(detected)
    ]
    if not integration:
        return met(
            f"No branch gathers changes on their way to {production}.",
            f"If one does, name it in integration_branches in {FILE}.",
        )
    gathered = [
        p
        for p in ctx.merged
        if _source(p) in integration and p["destination"]["branch"]["name"] == production
    ]
    if not gathered:
        return met(f"No pull requests from {', '.join(sorted(integration))} merged since.", *notes)
    lost = []
    for pull in gathered:
        head = pull["source"]["commit"]["hash"]
        if next(iter(ctx.bitbucket.commits(ctx.project.repo, head, production)), None):
            lost.append(f"#{pull['id']} {pull['title']}: {_source(pull)} into {production}")
    if lost:
        return missed(
            f"{_count(lost, 'merge')} into {production} didn't keep the branch's commits.",
            *lost,
            *notes,
            fix=[
                f"Merge an integration branch into {production} with a merge commit, a rebase or a"
                " fast forward, not a squash: a squash turns every change on it into one new"
                " commit, so their lead times start at that merge. A merge can't be changed"
                " afterwards."
            ],
        )
    return met(
        f"All {_count(gathered, 'merge')} into {production} kept the branch's commits.", *notes
    )


def fixes_carry_the_key(ctx: Context) -> Result:
    if not ctx.failures:
        return met(f"No failures recorded since {ctx.since.date().isoformat()}.")
    done = [i for i in ctx.failures if _done(i)]
    lost = [i for i in done if not ctx.pulls_with(i["key"])]
    if lost:
        label = ctx.settings.failure_label
        return missed(
            f"{_count(lost, 'failure')} done with no merged pull request carrying the key.",
            *(f"{i['key']} {i['fields']['summary']}" for i in lost),
            fix=[
                "A fix is found by the failure's key in its branch name, pull request title or"
                " commit message. A fix without it can't be linked now, so its recovery time and"
                " rework aren't counted. Next time, put the key in one of those; with jira-tools,"
                " `jira start <key>` names the branch.",
                *(
                    f"If {i['key']} wasn't a production failure: jira edit {i['key']} --unlabel"
                    f" {label}"
                    for i in lost
                ),
            ],
        )
    still_open = len(ctx.failures) - len(done)
    return met(f"{len(done)} done, with a merged pull request carrying its key; {still_open} open.")


def keys_not_reused(ctx: Context) -> Result:
    suspects = []
    for issue in ctx.failures:
        times = sorted(_time(p["commit"]["date"]) for p in ctx.pulls_with(issue["key"]))
        later = [t for t in times if t - times[0] > REUSE_AFTER]
        if later:
            days = (later[0] - times[0]).days
            suspects.append(f"{issue['key']}: merged again {days} days after its first fix")
    if suspects:
        return review(
            f"{_count(suspects, 'failure')} may have had its key reused.",
            *suspects,
            fix=[
                "Look at those pull requests. If one was unrelated work, the failure's recovery"
                " time runs to it; nothing can change that now. Next time, give new work its own"
                " issue."
            ],
        )
    return met("No failure's key was used long after its fix.")


def causes_recorded(ctx: Context) -> Result:
    without = [i for i in ctx.failures if not _has_cause(ctx, i)]
    if not without:
        return met(f"All {_count(ctx.failures, 'failure')} have a cause recorded.")
    linkable = any(t.inward.casefold() == "is caused by" for t in ctx.jira.link_types())
    fix = [
        "Find the commit that brought in the broken code, which `git blame` on the broken line in"
        f" {ctx.production_branch} shows, or the pull request that merged it. Then record it:"
    ]
    for issue in without:
        key = issue["key"]
        commit = f"https://bitbucket.org/{ctx.project.repo}/commits/<hash>"
        fix.append(f'jira weblink {key} {commit} "Caused by"')
        if linkable:
            fix.append(f'jira link {key} "is caused by" <key of the issue whose work it was>')
    fix.append(
        "If one wasn't a production failure, remove its label: "
        + ", ".join(f"jira edit {i['key']} --unlabel {ctx.settings.failure_label}" for i in without)
    )
    verb = "has" if len(without) == 1 else "have"
    return missed(
        f"{len(without)} of {_count(ctx.failures, 'failure')} {verb} no cause recorded.",
        *(f"{i['key']} {i['fields']['summary']}" for i in without),
        fix=fix,
    )


def labelled_on_discovery(ctx: Context) -> Result:
    late = []
    for issue in ctx.failures:
        created = _time(issue["fields"]["created"])
        opened = [_time(p["created_on"]) for p in ctx.pulls_with(issue["key"])]
        if opened and min(opened) < created:
            late.append(f"{issue['key']}: created after its fix's pull request opened")
    note = "A failure never labelled can't be detected."
    if late:
        return review(
            f"{_count(late, 'failure')} may have been recorded late.",
            *late,
            note,
            fix=[
                "Jira can't change an issue's creation time, so their recovery times are short by"
                " the gap. Next time, create the issue when the failure is found."
            ],
        )
    return met(f"None of {_count(ctx.failures, 'failure')} was recorded after its fix began.", note)


REQUIREMENTS: dict[str, list[Requirement]] = {
    "Settings": [
        Requirement("C1", "CI is on", pipelines_on),
        Requirement("C2", "A production deployment environment", production_environment),
        Requirement("C3", "Production is deployed by a CI step", deployment_step),
        Requirement("R1", f"{FILE} is committed", dora_table),
        Requirement("R2", "The team's document exists", team_document),
        Requirement("T1", "A tracker space for the issues", jira_space),
        Requirement("T3", 'A link type that reads "is caused by" (optional)', causes_link_type),
        Requirement("T4", "A Detected field (optional)", detected_field),
        Requirement("M1", "The code host's and tracker's commands set up", tools_set_up),
        Requirement("M2", "A code host token that can read the data", bitbucket_token),
        Requirement("M3", "A tracker token that works", jira_token),
        Requirement("M4", "The daily collection job", daily_job),
    ],
    "Habits": [
        Requirement("C4", "Integration branches reach production with their commits", history_kept),
        Requirement("H1", "Failures recorded on discovery", labelled_on_discovery),
        Requirement("H2", "Fixes carry the failure's key", fixes_carry_the_key),
        Requirement("H3", "A failure's key isn't reused", keys_not_reused),
        Requirement("H5", "Each failure's cause recorded", causes_recorded),
    ],
    "Not checkable": [
        Requirement(
            "C5",
            "Only the production branch may deploy to production",
            note="not checked: Bitbucket's API doesn't show it",
        ),
        Requirement("T2", "The failure label", note="nothing to check: a label needs no setup"),
        Requirement(
            "H4",
            "Production deployed only by CI",
            note="a deploy made elsewhere leaves no trace",
        ),
    ],
}


def run(requirement: Requirement, ctx: Context) -> Result:
    """The requirement's result. A check that can't get what it needs, such as for want of a
    token or a scope, is unchecked, saying why."""
    try:
        return requirement.check(ctx)
    except Problem as problem:
        return Result(Status.UNCHECKED, problem.message, problem.details)


def _source(pull: dict) -> str:
    return pull["source"]["branch"]["name"]


def _carried(pull: dict) -> str:
    """What a pull request carries a key in: its branch name, title and merge commit message."""
    return " ".join([_source(pull), pull.get("title", ""), pull["commit"].get("message", "")])


def _done(issue: dict) -> bool:
    return issue["fields"]["status"].get("statusCategory", {}).get("key") == "done"


def _has_cause(ctx: Context, issue: dict) -> bool:
    for link in issue["fields"].get("issuelinks", []):
        if "inwardIssue" in link and link["type"]["inward"].casefold() == "is caused by":
            return True
    for web_link in ctx.jira.web_links(issue["key"]) or []:
        if web_link.get("object", {}).get("title", "").casefold().startswith("caused by"):
            return True
    return False


def _delete_token(service: str) -> str:
    """The command that deletes a stored token on this system."""
    if platform.system() == "Linux":
        return f"secret-tool clear service {service}"
    return f"security delete-generic-password -s {service}"


def _time(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _count(items: list, noun: str) -> str:
    return f"{len(items)} {noun}{'' if len(items) == 1 else 's'}"
