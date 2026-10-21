"""The dora command that uv installs into .venv/bin."""

import subprocess
import sys
from pathlib import Path

BIN = Path(sys.prefix) / "bin"


def test_the_command_runs_from_any_directory(tmp_path: Path):
    result = subprocess.run([BIN / "dora", "--help"], capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout.startswith("usage: dora")


def test_outside_a_repository_it_says_so(tmp_path: Path):
    result = subprocess.run([BIN / "dora", "check"], capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 1
    assert "is not in a git repository" in result.stderr
