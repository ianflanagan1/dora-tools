"""dora check [repo] [--since <date>]

Checks a repository against every requirement of the DORA metrics that can be checked, as
dora-tools' docs/requirements.md lists them, and reports each. repo is host/workspace/name, such
as bitbucket.org/team/app, or a clone's folder; the clone the current folder is in unless given.
Its settings are read from what's committed on its main branch, not from the clone:

    ✔  met
    ✘  missed, with what was found
    ?  for a person to look at, or not checked, such as for want of a token's scope
    –  optional, or can't be checked at all, and why

Settings are checked as they are now; habits in the pull requests merged and the production
failures recorded since --since, 90 days ago unless given. It reads Bitbucket and Jira and
changes nothing. It ends ⚠️ when a requirement is missed.
"""

import sys
from datetime import UTC, datetime, timedelta

from cli_kit import cli
from cli_kit.colour import BOLD, CYAN, GREEN, GREY, YELLOW, Style
from cli_kit.outcome import NothingToDo, Precondition
from repo_tools.forge_commands import parse_since

from dora import checks, project
from dora.checks import Context, Result, Status

DEFAULT_DAYS = 90
MARKS = {
    Status.MET: ("✔", GREEN),
    Status.MISSED: ("✘", YELLOW),
    Status.REVIEW: ("?", YELLOW),
    Status.UNCHECKED: ("?", GREY),
    Status.OPTIONAL: ("–", GREY),
}


def register(subparsers: cli.Subparsers) -> None:
    parser = subparsers.add_parser(
        "check",
        help="check a repository has what the DORA metrics need",
        description=__doc__.split("\n\n", 1)[1],
    )
    parser.add_argument("repo", nargs="?", help="host/workspace/name, or a clone's folder")
    parser.add_argument(
        "--since",
        type=parse_since,
        help=f"check habits since this date or time; {DEFAULT_DAYS} days ago unless given",
    )
    parser.set_defaults(run=lambda args: check(args.repo, args.since))


def check(
    given: str | None, since: datetime | None = None, ctx: Context | None = None
) -> NothingToDo:
    since = since or datetime.now(UTC) - timedelta(days=DEFAULT_DAYS)
    if ctx is None:
        ref = project.resolve(given)
        host = project.forge(ref)
        ctx = Context(project.load(ref, host), since, host)
    style = Style.for_stream(sys.stdout)
    print(style(BOLD, f"{ctx.project.ref}, habits since {since.date().isoformat()}"))

    counted: list[Result] = []
    for group, requirements in checks.REQUIREMENTS.items():
        print(f"\n{style(BOLD, group)}")
        for requirement in requirements:
            if requirement.check is None:
                print(f"– {requirement.id}  {requirement.title}: {style(GREY, requirement.note)}")
                continue
            result = checks.run(requirement, ctx)
            counted.append(result)
            mark, colour = MARKS[result.status]
            summary = (
                result.summary if result.status == Status.MET else style(colour, result.summary)
            )
            print(f"{style(colour, mark)} {requirement.id}  {requirement.title}: {summary}")
            for detail in result.details:
                print(f"      {style(GREY, detail)}")
            for index, line in enumerate(result.fix):
                print(f"      {'→' if index == 0 else ' '} {style(CYAN, line)}")

    tally = {status: sum(r.status == status for r in counted) for status in Status}
    looks = [
        f"{tally[Status.REVIEW]} to look at" if tally[Status.REVIEW] else "",
        f"{tally[Status.UNCHECKED]} not checked" if tally[Status.UNCHECKED] else "",
    ]
    rest = "; ".join(look for look in looks if look)
    print()
    if tally[Status.MISSED]:
        raise Precondition(
            f"{tally[Status.MISSED]} of {len(counted)} requirements aren't met"
            f"{f'; {rest}' if rest else ''}.",
            "dora-tools' docs/requirements.md says what each needs.",
        )
    return NothingToDo(f"Every checked requirement is met{f'; {rest}' if rest else ''}.")
