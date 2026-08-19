from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from typing import cast
from urllib.parse import urlsplit

import pytest

_EXTERNAL_MARKERS = frozenset({"docker", "internet"})
_MARK_EXPRESSION_TOKEN = re.compile(r"\b(?:not|and|or|[A-Za-z_][A-Za-z0-9_]*)\b|[()]")


def external_tests_requested(config: pytest.Config, marker: str) -> bool:
    """Return whether one external marker was named positively or by its test path."""
    if marker not in _EXTERNAL_MARKERS:
        raise ValueError(f"unknown external marker {marker!r}")
    expression = cast(str, config.getoption("markexpr"))
    negated = False
    pending_not = False
    negation_stack: list[bool] = []
    positive = False
    negative = False
    for token in _MARK_EXPRESSION_TOKEN.findall(expression):
        if token == "not":
            pending_not = not pending_not
        elif token == "(":
            negation_stack.append(negated)
            negated ^= pending_not
            pending_not = False
        elif token == ")":
            negated = negation_stack.pop() if negation_stack else False
            pending_not = False
        elif token in {"and", "or"}:
            pending_not = False
        else:
            if token == marker:
                if negated ^ pending_not:
                    negative = True
                else:
                    positive = True
            pending_not = False
    path_fragment = f"/tests/{marker}"
    path_selected = any(
        (normalized := "/" + str(argument).replace("\\", "/").lstrip("/")).startswith(f"/tests/{marker}")
        or path_fragment in normalized
        for argument in config.invocation_params.args
    )
    return (positive or path_selected) and not negative


def validate_internet_url(value: str | None) -> str:
    """Require an operator-supplied credential-free HTTPS URL with a hostname."""
    if value is None or not value:
        raise pytest.UsageError("--internet-url must supply an explicit HTTPS URL for the Internet smoke test")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as error:
        raise pytest.UsageError("--internet-url must supply a valid HTTPS URL with a hostname") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise pytest.UsageError("--internet-url must supply a credential-free HTTPS URL with a hostname")
    return value


def run_external_command(
    argv: Sequence[str],
    *,
    purpose: str,
    timeout: float,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run one bounded external-test command and translate unavailable tooling actionably."""
    try:
        result = subprocess.run(
            tuple(argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as error:
        raise pytest.UsageError(
            f"Docker CLI was not found while attempting to {purpose}; install Docker Engine with a supported Compose plugin "
            "and ensure docker is available without sudo"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise pytest.UsageError(f"timed out after {timeout:g}s while attempting to {purpose}") from error
    except OSError as error:
        raise pytest.UsageError(f"could not {purpose}: {error}") from error
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no command output"
        raise pytest.UsageError(f"could not {purpose} (status {result.returncode}): {detail}")
    return result


def require_serial_external_tests(config: pytest.Config) -> None:
    """Reject parallel execution because external tests own real project resources."""
    numprocesses = cast(int | None, getattr(config.option, "numprocesses", None))
    if numprocesses not in (None, 0):
        raise pytest.UsageError("Docker and Internet tests must run serially; invoke pytest with -n 0")
