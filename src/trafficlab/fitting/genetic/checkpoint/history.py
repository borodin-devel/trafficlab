"""Derived history CSV and two-file generation persistence."""

from __future__ import annotations

import csv
import math
from io import StringIO
from pathlib import Path
from typing import Literal, cast

from trafficlab.common.config import FamilyName
from trafficlab.common.errors import TrafficlabError
from trafficlab.fitting.genetic.checkpoint.codec import load_checkpoint, publish_checkpoint
from trafficlab.fitting.genetic.checkpoint.compatibility import (
    atomic_replace,
    invalid_checkpoint,
    parse_family_name,
)
from trafficlab.fitting.genetic.checkpoint.schema import CheckpointCompatibility, CheckpointState
from trafficlab.fitting.genetic.checkpoint.state import validate_state
from trafficlab.fitting.genetic.types import CandidateId, HistoryRow

_HISTORY_HEADER = (
    "generation",
    "scope",
    "family",
    "candidate_count",
    "valid_count",
    "best_fitness",
    "mean_fitness",
    "best_birth_generation",
    "best_birth_index",
)


def _parse_decimal(value: str, *, name: str) -> int:
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError(f"{name} must be a canonical nonnegative decimal integer")
    result = int(value)
    if str(result) != value:
        raise ValueError(f"{name} must be a canonical nonnegative decimal integer")
    return result


def _parse_repr_float(value: str, *, name: str) -> float:
    try:
        result = float(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a finite Python float repr") from error
    if not math.isfinite(result) or repr(result) != value or not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be a finite Python float repr in [0, 1]")
    return result


def _parse_history_csv(content: bytes, family_names: frozenset[FamilyName]) -> tuple[HistoryRow, ...]:
    try:
        text = content.decode("utf-8")
        rows = list(csv.reader(StringIO(text, newline="")))
    except (UnicodeDecodeError, csv.Error) as error:
        raise ValueError(f"history CSV is invalid: {error}") from error
    if not rows or tuple(rows[0]) != _HISTORY_HEADER:
        raise ValueError("history CSV has the wrong header")
    parsed: list[HistoryRow] = []
    for fields in rows[1:]:
        if len(fields) != len(_HISTORY_HEADER):
            raise ValueError("history CSV row has the wrong field count")
        generation, scope, family_field, candidate_count, valid_count, best, mean, birth_generation, birth_index = (
            fields
        )
        if scope not in {"family", "overall"}:
            raise ValueError("history CSV scope must be family or overall")
        if scope == "overall":
            if family_field:
                raise ValueError("overall history CSV family must be empty")
            family = None
        else:
            family = parse_family_name(family_field, name="history CSV family")
            if family not in family_names:
                raise ValueError("history CSV family is not enabled")
        parsed.append(
            HistoryRow(
                generation=_parse_decimal(generation, name="history CSV generation"),
                scope=cast(Literal["family", "overall"], scope),
                family=family,
                candidate_count=_parse_decimal(candidate_count, name="history CSV candidate_count"),
                valid_count=_parse_decimal(valid_count, name="history CSV valid_count"),
                best_fitness=_parse_repr_float(best, name="history CSV best_fitness"),
                mean_fitness=_parse_repr_float(mean, name="history CSV mean_fitness"),
                best_identifier=CandidateId(
                    birth_generation=_parse_decimal(birth_generation, name="history CSV best_birth_generation"),
                    birth_index=_parse_decimal(birth_index, name="history CSV best_birth_index"),
                ),
            )
        )
    return tuple(parsed)


def render_history_csv(state: CheckpointState) -> bytes:
    """Render and reparse the exact CSV projection derived solely from checkpoint history."""
    try:
        validate_state(state)
        stream = StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(_HISTORY_HEADER)
        for row in state.history:
            writer.writerow(
                (
                    str(row.generation),
                    row.scope,
                    "" if row.family is None else row.family,
                    str(row.candidate_count),
                    str(row.valid_count),
                    repr(row.best_fitness),
                    repr(row.mean_fitness),
                    str(row.best_identifier.birth_generation),
                    str(row.best_identifier.birth_index),
                )
            )
        content = stream.getvalue().encode("utf-8")
        family_names: frozenset[FamilyName] = frozenset(family.name for family in state.compatibility.families)
        if _parse_history_csv(content, family_names) != state.history:
            raise ValueError("history CSV did not reconstruct the exact checkpoint rows")
        return content
    except (TypeError, ValueError) as error:
        raise invalid_checkpoint(str(error)) from error


def publish_history_csv(path: Path, state: CheckpointState) -> None:
    """Atomically replace derived history after validating its exact scalar reconstruction."""
    content = render_history_csv(state)
    atomic_replace(path, content)


def publish_generation(run_directory: Path, state: CheckpointState) -> None:
    """Publish authoritative checkpoint first and derived history second."""
    publish_checkpoint(run_directory / "checkpoint.json", state)
    publish_history_csv(run_directory / "ga_history.csv", state)


def load_generation(run_directory: Path, compatibility: CheckpointCompatibility) -> CheckpointState:
    """Load authoritative checkpoint and repair only a missing or stale derived history projection."""
    state = load_checkpoint(run_directory / "checkpoint.json", compatibility)
    expected = render_history_csv(state)
    history_path = run_directory / "ga_history.csv"
    try:
        existing = history_path.read_bytes()
    except FileNotFoundError:
        existing = None
    except OSError as error:
        raise TrafficlabError(
            f"could not read derived history {history_path}: {error}",
            corrective_action="verify ga_history.csv is readable before resuming",
        ) from error
    if existing != expected:
        publish_history_csv(history_path, state)
    return state
