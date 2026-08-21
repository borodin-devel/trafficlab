"""Non-gating production Scapy diagnostic contracts."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts import benchmark_scapy_production as diagnostic
from scripts.benchmark_scapy_production import CaseRecord, EnvironmentRecord, SampleRecord, render_diagnostic


def _current_identities(_frame_count: int) -> tuple[str, str, int, str]:
    return "e" * 64, "a" * 64, 100, "b" * 64


def _source_matches(_environment: EnvironmentRecord) -> bool:
    return True


def _source_differs(_environment: EnvironmentRecord) -> bool:
    return False


def _document() -> dict[str, object]:
    samples = (
        SampleRecord(
            encode_wall_seconds=1.0,
            frame_count=100_000,
            input_trace_sha256="e" * 64,
            output_sha256="a" * 64,
            output_size_bytes=100,
            peak_rss_kib=10,
            read_wall_seconds=2.0,
            trace_digest="b" * 64,
        ),
        SampleRecord(
            encode_wall_seconds=3.0,
            frame_count=100_000,
            input_trace_sha256="e" * 64,
            output_sha256="a" * 64,
            output_size_bytes=100,
            peak_rss_kib=20,
            read_wall_seconds=4.0,
            trace_digest="b" * 64,
        ),
    )
    case = CaseRecord.from_samples(100_000, samples, warmup_runs=1)
    return {
        "codec": "scapy-2.7.0",
        "production": True,
        "schema_version": 2,
        "command": (
            "scripts/run_bounded.sh",
            "--memory-high",
            "6G",
            "--memory-max",
            "8G",
            "--swap-max",
            "1G",
            "--wall-time",
            "20m",
            "--kill-after",
            "10s",
            "--",
            "uv",
            "run",
            "--locked",
            "python",
            "scripts/benchmark_scapy_production.py",
        ),
        "environment": {
            "implementation_sha256": "c" * 64,
            "machine": "x86_64",
            "platform": "test",
            "python": "3.12.3",
            "scapy": "2.7.0",
            "source_commit": "1" * 40,
            "source_tree": "2" * 40,
            "uv_lock_sha256": "d" * 64,
        },
        "cases": [case.model_dump(mode="json")],
    }


def test_diagnostic_schema_has_bound_measurements_without_adoption_or_license_fields() -> None:
    rendered = render_diagnostic(_document(), expected_frame_counts=(100_000,), expected_repetitions=2)

    assert b'"median_encode_wall_seconds":2.0' in rendered
    assert b'"median_read_wall_seconds":3.0' in rendered
    assert b'"median_peak_rss_kib":15' in rendered
    assert b'"input_trace_sha256":"' + b"e" * 64 + b'"' in rendered
    assert b'"source_commit":"' + b"1" * 40 + b'"' in rendered
    assert b'"source_tree":"' + b"2" * 40 + b'"' in rendered
    for forbidden in (b'"license"', b'"decision"', b'"gates"', b'"production_adoption"'):
        assert forbidden not in rendered


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("input_trace_sha256", "0" * 64),
        ("output_sha256", "0" * 64),
        ("output_size_bytes", 101),
        ("trace_digest", "0" * 64),
    ),
)
def test_check_recomputes_deterministic_input_output_and_trace_identities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: object,
) -> None:
    document = _document()
    for sample in document["cases"][0]["samples"]:  # type: ignore[index]
        sample[field] = replacement
    output = tmp_path / "diagnostic.json"
    output.write_bytes(render_diagnostic(document, expected_frame_counts=(100_000,), expected_repetitions=2))
    monkeypatch.setattr(diagnostic, "_implementation_sha256", lambda: "c" * 64)
    monkeypatch.setattr(diagnostic, "_current_lock_sha256", lambda: "d" * 64)
    monkeypatch.setattr(diagnostic, "_recorded_source_matches", _source_matches)
    monkeypatch.setattr(diagnostic, "_deterministic_identities", _current_identities)

    assert not diagnostic.check_diagnostic(
        output,
        expected_frame_counts=(100_000,),
        expected_repetitions=2,
    )


def test_check_rejects_source_commit_or_tree_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "diagnostic.json"
    output.write_bytes(render_diagnostic(_document(), expected_frame_counts=(100_000,), expected_repetitions=2))
    monkeypatch.setattr(diagnostic, "_implementation_sha256", lambda: "c" * 64)
    monkeypatch.setattr(diagnostic, "_current_lock_sha256", lambda: "d" * 64)
    monkeypatch.setattr(diagnostic, "_recorded_source_matches", _source_differs)
    monkeypatch.setattr(diagnostic, "_deterministic_identities", _current_identities)

    assert not diagnostic.check_diagnostic(
        output,
        expected_frame_counts=(100_000,),
        expected_repetitions=2,
    )


def test_check_accepts_recomputed_current_identities(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "diagnostic.json"
    document = _document()
    output.write_bytes(render_diagnostic(document, expected_frame_counts=(100_000,), expected_repetitions=2))
    monkeypatch.setattr(diagnostic, "_implementation_sha256", lambda: "c" * 64)
    monkeypatch.setattr(diagnostic, "_current_lock_sha256", lambda: "d" * 64)
    monkeypatch.setattr(diagnostic, "_recorded_source_matches", _source_matches)
    monkeypatch.setattr(diagnostic, "_deterministic_identities", _current_identities)

    assert diagnostic.check_diagnostic(
        output,
        expected_frame_counts=(100_000,),
        expected_repetitions=2,
    )
    assert json.loads(output.read_bytes()) == json.loads(
        render_diagnostic(document, expected_frame_counts=(100_000,), expected_repetitions=2)
    )


def test_case_rejects_inconsistent_repeated_input_identity() -> None:
    document = deepcopy(_document())
    document["cases"][0]["samples"][1]["input_trace_sha256"] = "f" * 64  # type: ignore[index]

    with pytest.raises(ValueError, match="identical input, bytes, and trace"):
        render_diagnostic(document, expected_frame_counts=(100_000,), expected_repetitions=2)
