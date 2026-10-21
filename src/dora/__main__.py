"""`dora <command> ...`."""

import sys

from cli_kit import cli

from dora.commands import check, sync

COMMANDS = [sync, check]


def main(argv: list[str] | None = None) -> int:
    return cli.run("dora", "The DORA metrics of a team's delivery.", COMMANDS, argv)


if __name__ == "__main__":
    sys.exit(main())
