"""Direct history checkpoint behavior tests."""

import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import trafficlab.fitting.genetic.checkpoint.codec as checkpoint_codec
import trafficlab.fitting.genetic.checkpoint.compatibility as checkpoint_compatibility
import trafficlab.fitting.genetic.checkpoint.history as checkpoint_history
from tests.support.checkpoint import (
    COMPATIBILITY,
    MMPP_TRIAL,
    OVERALL_ROW,
    POISSON_TRIAL,
    VALID_STATE,
)
from trafficlab.common.errors import TrafficlabError
from trafficlab.fitting.genetic.checkpoint import (
    CheckpointState,
    load_checkpoint,
    load_generation,
    load_history_csv,
    publish_checkpoint,
    publish_generation,
    publish_history_csv,
    render_checkpoint,
    render_history_csv,
)
from trafficlab.fitting.genetic.types import (
    HistoryRow,
)


def test_checkpoint_and_history_publication_round_trip_through_real_atomic_replace(tmp_path: Path) -> None:
    publish_checkpoint(tmp_path / "checkpoint.json", VALID_STATE)
    assert load_checkpoint(tmp_path / "checkpoint.json", COMPATIBILITY) == VALID_STATE
    publish_history_csv(tmp_path / "ga_history.csv", VALID_STATE)
    assert (tmp_path / "ga_history.csv").read_bytes() == render_history_csv(VALID_STATE)


def test_checkpoint_load_and_generation_history_repair_preserve_authoritative_bytes(tmp_path: Path) -> None:
    with pytest.raises(TrafficlabError, match="could not read checkpoint"):
        load_checkpoint(tmp_path / "missing.json", COMPATIBILITY)

    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_bytes(render_checkpoint(VALID_STATE))
    authoritative = checkpoint_path.read_bytes()
    history_path = tmp_path / "ga_history.csv"
    for existing in (None, b"stale\n", render_history_csv(VALID_STATE)):
        history_path.unlink(missing_ok=True)
        if existing is not None:
            history_path.write_bytes(existing)
        assert load_generation(tmp_path, COMPATIBILITY) == VALID_STATE
        assert checkpoint_path.read_bytes() == authoritative
        assert history_path.read_bytes() == render_history_csv(VALID_STATE)


def test_history_repair_read_or_publication_failure_never_changes_valid_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_bytes(render_checkpoint(VALID_STATE))
    authoritative = checkpoint_path.read_bytes()
    real_read = Path.read_bytes

    def fail_history_read(path: Path) -> bytes:
        if path.name == "ga_history.csv":
            raise OSError("injected history read failure")
        return real_read(path)

    monkeypatch.setattr(Path, "read_bytes", fail_history_read)
    with pytest.raises(TrafficlabError, match="history read failure"):
        load_generation(tmp_path, COMPATIBILITY)
    assert checkpoint_path.read_bytes() == authoritative

    monkeypatch.undo()

    def fail_history_publication(_path: Path, _state: CheckpointState) -> None:
        raise TrafficlabError("injected history publication failure", corrective_action="preserve checkpoint")

    monkeypatch.setattr(checkpoint_history, "publish_history_csv", fail_history_publication)
    with pytest.raises(TrafficlabError, match="history publication failure"):
        load_generation(tmp_path, COMPATIBILITY)
    assert checkpoint_path.read_bytes() == authoritative


@pytest.mark.parametrize(
    "content",
    [
        b"\xff",
        b"wrong\n",
        b"generation,scope,family,candidate_count,valid_count,best_fitness,mean_fitness,"
        b"best_birth_generation,best_birth_index\n0,family,mmpp\n",
        render_history_csv(VALID_STATE).replace(b",family,mmpp,", b",other,mmpp,", 1),
        render_history_csv(VALID_STATE).replace(b",overall,,", b",overall,mmpp,", 1),
        render_history_csv(VALID_STATE).replace(b",family,mmpp,", b",family,markov_renewal,", 1),
        render_history_csv(VALID_STATE).replace(b"0,family", b"00,family", 1),
        render_history_csv(VALID_STATE).replace(b"0,family", b"x,family", 1),
        render_history_csv(VALID_STATE).replace(b",0.4,0.4,", b",nan,0.4,", 1),
    ],
)
def test_history_csv_validator_rejects_malformed_header_rows_scalars_and_families(content: bytes) -> None:
    with pytest.raises(ValueError):
        checkpoint_history._parse_history_csv(  # pyright: ignore[reportPrivateUsage]
            content, frozenset(("mmpp", "poisson_empirical"))
        )


def test_checkpoint_atomic_wrapper_rejects_changed_persisted_temporary_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def corrupt(_path: Path, _content: bytes, *, validator: Any) -> None:
        validator(b"changed\n")

    monkeypatch.setattr(checkpoint_compatibility, "_atomic_replace", corrupt)
    with pytest.raises(TrafficlabError, match="persisted temporary artifact"):
        checkpoint_compatibility.atomic_replace(tmp_path / "checkpoint.json", b"expected\n")


def test_render_history_csv_rejects_a_projection_that_does_not_reconstruct_exact_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def return_no_rows(_content: bytes, _families: frozenset[Any]) -> tuple[HistoryRow, ...]:
        return ()

    monkeypatch.setattr(checkpoint_history, "_parse_history_csv", return_no_rows)
    with pytest.raises(TrafficlabError, match="reconstruct"):
        render_history_csv(VALID_STATE)


def test_checkpoint_publishes_before_derived_history_repair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def record(path: Path, _content: bytes) -> None:
        calls.append(path.name)

    monkeypatch.setattr(checkpoint_codec, "atomic_replace", record)
    monkeypatch.setattr(checkpoint_history, "atomic_replace", record)
    publish_generation(tmp_path, VALID_STATE)
    assert calls == ["checkpoint.json", "ga_history.csv"]


def test_history_rows_have_exact_header_lexical_family_rows_then_overall(tmp_path: Path) -> None:
    publish_history_csv(tmp_path / "ga_history.csv", VALID_STATE)
    assert (tmp_path / "ga_history.csv").read_text() == (
        "generation,scope,family,candidate_count,valid_count,best_fitness,mean_fitness,"
        "best_birth_generation,best_birth_index\n"
        f"0,family,mmpp,1,1,{MMPP_TRIAL.aggregate_score!r},{MMPP_TRIAL.aggregate_score!r},0,0\n"
        f"0,family,poisson_empirical,2,1,{POISSON_TRIAL.aggregate_score!r},"
        f"{(POISSON_TRIAL.aggregate_score / 2.0)!r},0,2\n"
        f"0,overall,,3,2,{POISSON_TRIAL.aggregate_score!r},{OVERALL_ROW.mean_fitness!r},0,2\n"
    )
    assert (tmp_path / "ga_history.csv").read_bytes() == render_history_csv(VALID_STATE)


def test_public_history_loader_returns_exact_immutable_rows(tmp_path: Path) -> None:
    history_path = tmp_path / "ga_history.csv"
    publish_history_csv(history_path, VALID_STATE)

    loaded = load_history_csv(history_path, frozenset(("mmpp", "poisson_empirical")))

    assert loaded == VALID_STATE.history
    assert type(loaded) is tuple
    assert all(type(row) is HistoryRow for row in loaded)
    with pytest.raises(ValidationError):  # pyright: ignore[reportUnknownMemberType]
        loaded[0].generation = 1  # type: ignore[misc]


def test_public_history_loader_reports_read_failure(tmp_path: Path) -> None:
    with pytest.raises(TrafficlabError, match="could not read history artifact"):
        load_history_csv(tmp_path / "missing.csv", frozenset(("mmpp", "poisson_empirical")))


@pytest.mark.parametrize("mutation", ("header_only", "reordered", "duplicate", "generation_gap", "bad_counts"))
def test_public_history_loader_rejects_noncanonical_generation_blocks(tmp_path: Path, mutation: str) -> None:
    lines = render_history_csv(VALID_STATE).decode("utf-8").splitlines()
    if mutation == "header_only":
        changed = lines[:1]
    elif mutation == "reordered":
        changed = (lines[0], lines[2], lines[1], lines[3])
    elif mutation == "duplicate":
        changed = (lines[0], lines[1], lines[1], lines[3])
    elif mutation == "generation_gap":
        changed = (lines[0], lines[1].replace("0,family", "1,family", 1), lines[2], lines[3])
    else:
        changed = (lines[0], lines[1], lines[2], lines[3].replace("0,overall,,3,2,", "0,overall,,4,2,", 1))
    history_path = tmp_path / "ga_history.csv"
    history_path.write_text("\n".join(changed) + "\n", encoding="utf-8")

    with pytest.raises(TrafficlabError, match="complete|ascending|candidate_count|population_size"):
        load_history_csv(
            history_path,
            frozenset(("mmpp", "poisson_empirical")),
            population_size=3,
            generation_count=0,
        )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("invalid_population", "population_size must be a positive integer"),
        ("invalid_generation_count", "generation_count must be a nonnegative integer"),
        ("generation_beyond_config", "beyond experiment genetic.generation_count"),
        ("zero_candidate_count", "candidate_count must be positive"),
        ("invalid_valid_sum", "valid_count does not equal family counts"),
        ("future_birth", "birth generation exceeds row generation"),
        ("zero_valid_nonzero_fitness", "zero valid_count"),
        ("invalid_overall_mean", "overall mean"),
        ("invalid_overall_best", "overall best"),
    ),
)
def test_history_projection_rejects_every_infeasible_cross_row_boundary(mutation: str, expected: str) -> None:
    content = render_history_csv(VALID_STATE)
    population_size = 3
    generation_count = 0
    if mutation == "invalid_population":
        population_size = 0
    elif mutation == "invalid_generation_count":
        generation_count = -1
    elif mutation == "generation_beyond_config":
        lines = content.decode("utf-8").splitlines()
        second_block = tuple(line.replace("0,", "1,", 1) for line in lines[1:])
        content = ("\n".join((*lines, *second_block)) + "\n").encode("utf-8")
    elif mutation == "zero_candidate_count":
        content = content.replace(b"0,family,mmpp,1,1,", b"0,family,mmpp,0,0,", 1)
    elif mutation == "invalid_valid_sum":
        content = content.replace(b"0,overall,,3,2,", b"0,overall,,3,1,", 1)
    elif mutation == "future_birth":
        content = content.replace(b"0,family,mmpp,1,1,0.4,0.4,0,0", b"0,family,mmpp,1,1,0.4,0.4,1,0", 1)
    elif mutation == "zero_valid_nonzero_fitness":
        content = content.replace(b"0,family,mmpp,1,1,", b"0,family,mmpp,1,0,", 1)
        content = content.replace(b"0,overall,,3,2,", b"0,overall,,3,1,", 1)
    elif mutation == "invalid_overall_mean":
        content = content.replace(f",{OVERALL_ROW.mean_fitness!r},0,2\n".encode(), b",0.0,0,2\n", 1)
    else:
        content = content.replace(
            f"0,overall,,3,2,{POISSON_TRIAL.aggregate_score!r},".encode(),
            b"0,overall,,3,2,0.75,",
            1,
        )

    with pytest.raises(ValueError, match=expected):
        checkpoint_history.parse_history_csv(
            content,
            frozenset(("mmpp", "poisson_empirical")),
            population_size=population_size,
            generation_count=generation_count,
        )


def _single_family_history_content(
    *,
    candidate_count: int,
    valid_count: int,
    best_fitness: float,
    mean_fitness: float,
) -> bytes:
    return (
        "generation,scope,family,candidate_count,valid_count,best_fitness,mean_fitness,"
        "best_birth_generation,best_birth_index\n"
        f"0,family,mmpp,{candidate_count},{valid_count},{best_fitness!r},{mean_fitness!r},0,0\n"
        f"0,overall,,{candidate_count},{valid_count},{best_fitness!r},{mean_fitness!r},0,0\n"
    ).encode()


def test_history_projection_rejects_mean_impossible_for_valid_candidate_count() -> None:
    content = _single_family_history_content(
        candidate_count=10,
        valid_count=1,
        best_fitness=0.5,
        mean_fitness=0.2,
    )

    with pytest.raises(ValueError, match="history mean_fitness is not feasible for valid_count"):
        checkpoint_history.parse_history_csv(
            content,
            frozenset(("mmpp",)),
            population_size=10,
            generation_count=0,
        )


@pytest.mark.parametrize(
    ("candidate_count", "valid_count", "best_fitness", "mean_fitness"),
    (
        (8, 1, 0.5, 0.0625),
        (10, 0, 0.0, 0.0),
        (10, 10, 0.5, 0.5),
    ),
)
def test_history_projection_accepts_exact_mean_boundary_and_zero_or_full_validity(
    candidate_count: int,
    valid_count: int,
    best_fitness: float,
    mean_fitness: float,
) -> None:
    content = _single_family_history_content(
        candidate_count=candidate_count,
        valid_count=valid_count,
        best_fitness=best_fitness,
        mean_fitness=mean_fitness,
    )

    rows = checkpoint_history.parse_history_csv(
        content,
        frozenset(("mmpp",)),
        population_size=candidate_count,
        generation_count=0,
    )

    assert [(row.valid_count, row.best_fitness, row.mean_fitness) for row in rows] == [
        (valid_count, best_fitness, mean_fitness),
        (valid_count, best_fitness, mean_fitness),
    ]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"wrong\n", "history CSV has the wrong header"),
        (
            render_history_csv(VALID_STATE).replace(b",family,mmpp,", b",family,markov_renewal,", 1),
            "history CSV family is not enabled",
        ),
        (
            render_history_csv(VALID_STATE).replace(b",0.4,0.4,", b",0.400,0.4,", 1),
            "history CSV best_fitness must be a finite Python float repr in [0, 1]",
        ),
        (
            render_history_csv(VALID_STATE).replace(b",0.4,0.4,", b",abc,0.4,", 1),
            "history CSV best_fitness must be a finite Python float repr",
        ),
    ],
)
def test_public_history_loader_rejects_invalid_artifacts(tmp_path: Path, content: bytes, expected: str) -> None:
    history_path = tmp_path / "ga_history.csv"
    history_path.write_bytes(content)

    with pytest.raises(TrafficlabError, match=re.escape(expected)):
        load_history_csv(history_path, frozenset(("mmpp", "poisson_empirical")))
