"""Run the repository's mandatory development checks through fixed commands."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

type CommandRunner = Callable[[tuple[str, ...]], int]


@dataclass(frozen=True, slots=True)
class Check:
    """One named, fixed-argument quality gate."""

    name: str
    argv: tuple[str, ...]


CHECKS: tuple[Check, ...] = (
    Check("format", ("ruff", "format", "--check", ".")),
    Check("lint", ("ruff", "check", ".")),
    Check("typecheck", ("pyright",)),
    Check("test", (sys.executable, "-m", "pytest")),
    Check(
        "coverage",
        (
            sys.executable,
            "-m",
            "pytest",
            "--cov=trafficlab",
            "--cov-report=term-missing:skip-covered",
            "--cov-fail-under=100",
        ),
    ),
    Check(
        "build",
        (
            # 2000-01-01 UTC is ZIP-safe and independent of checkout timestamps.
            "/usr/bin/env",
            "SOURCE_DATE_EPOCH=946684800",
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--wheel",
            "--outdir",
            "dist",
        ),
    ),
)

_CHECKS_BY_NAME = {check.name: check for check in CHECKS}


def select_checks(name: str) -> tuple[Check, ...]:
    """Return the fixed checks selected by a public command name."""
    if name == "all":
        return CHECKS
    try:
        return (_CHECKS_BY_NAME[name],)
    except KeyError as error:
        raise ValueError(f"unknown check: {name}") from error


def _run_command(argv: tuple[str, ...]) -> int:
    # Every vector comes from CHECKS; user input selects names, never arguments.
    return subprocess.run(argv, check=False).returncode


def run_checks(checks: Sequence[Check], *, runner: CommandRunner = _run_command) -> int:
    """Run checks in order, stopping at and returning the first failure."""
    for check in checks:
        print(f"==> {check.name}", flush=True)
        return_code = runner(check.argv)
        if return_code != 0:
            return return_code
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse one check name and return the aggregate process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "check",
        choices=("all", *tuple(_CHECKS_BY_NAME)),
        default="all",
        nargs="?",
    )
    arguments = parser.parse_args(argv)
    return run_checks(select_checks(arguments.check))


if __name__ == "__main__":
    raise SystemExit(main())
