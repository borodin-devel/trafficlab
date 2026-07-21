"""Tests for the repository-owned quality command interface."""

import sys
from collections.abc import Callable

import pytest
from tools.quality import CHECKS, Check, run_checks, select_checks


@pytest.mark.unit
def test_select_all_preserves_required_gate_order() -> None:
    """The aggregate check runs mandatory gates in their declared order."""
    assert tuple(check.name for check in select_checks("all")) == (
        "format",
        "lint",
        "typecheck",
        "test",
        "coverage",
        "build",
    )


@pytest.mark.unit
def test_select_one_returns_only_requested_check() -> None:
    """A named command remains a thin adapter for exactly one gate."""
    assert select_checks("lint") == (CHECKS[1],)


@pytest.mark.unit
def test_unknown_check_is_rejected() -> None:
    """Unknown command names cannot become subprocess arguments."""
    with pytest.raises(ValueError, match="unknown check: deploy"):
        select_checks("deploy")


@pytest.mark.unit
@pytest.mark.parametrize("check_name", ["test", "coverage"])
def test_pytest_checks_preserve_repository_import_path(check_name: str) -> None:
    """Pytest gates use the active interpreter so local tools stay importable."""
    (check,) = select_checks(check_name)

    assert check.argv[:3] == (sys.executable, "-m", "pytest")


@pytest.mark.unit
def test_build_uses_locked_environment() -> None:
    """The build gate must not resolve backend dependencies outside uv.lock."""
    (check,) = select_checks("build")

    assert check.argv == (
        "/usr/bin/env",
        "SOURCE_DATE_EPOCH=946684800",
        sys.executable,
        "-m",
        "build",
        "--no-isolation",
        "--wheel",
        "--outdir",
        "dist",
    )


@pytest.mark.unit
def test_run_checks_stops_at_first_failure() -> None:
    """A failed mandatory gate stops later work and preserves its exit code."""
    calls: list[tuple[str, ...]] = []

    def fake_runner(argv: tuple[str, ...]) -> int:
        calls.append(argv)
        return 9 if argv == ("second",) else 0

    checks = (
        Check("first", ("first",)),
        Check("second", ("second",)),
        Check("third", ("third",)),
    )

    assert run_checks(checks, runner=fake_runner) == 9
    assert calls == [("first",), ("second",)]


@pytest.mark.unit
def test_run_checks_returns_success_after_every_gate() -> None:
    """The aggregate result succeeds only after all selected gates succeed."""
    calls: list[tuple[str, ...]] = []

    def fake_runner(argv: tuple[str, ...]) -> int:
        calls.append(argv)
        return 0

    runner: Callable[[tuple[str, ...]], int] = fake_runner
    checks = (Check("first", ("first",)), Check("second", ("second",)))

    assert run_checks(checks, runner=runner) == 0
    assert calls == [("first",), ("second",)]
