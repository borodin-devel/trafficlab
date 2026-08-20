"""Non-gating production Scapy diagnostic contracts."""

from __future__ import annotations

from scripts.benchmark_scapy_production import CaseRecord, SampleRecord, render_diagnostic


def test_diagnostic_schema_has_measurements_without_adoption_or_license_fields() -> None:
    samples = (
        SampleRecord(
            encode_wall_seconds=1.0,
            frame_count=100_000,
            output_sha256="a" * 64,
            output_size_bytes=100,
            peak_rss_kib=10,
            read_wall_seconds=2.0,
            trace_digest="b" * 64,
        ),
        SampleRecord(
            encode_wall_seconds=3.0,
            frame_count=100_000,
            output_sha256="a" * 64,
            output_size_bytes=100,
            peak_rss_kib=20,
            read_wall_seconds=4.0,
            trace_digest="b" * 64,
        ),
    )
    case = CaseRecord.from_samples(100_000, samples, warmup_runs=1)
    document = {
        "codec": "scapy-2.7.0",
        "production": True,
        "schema_version": 1,
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
            "uv_lock_sha256": "d" * 64,
        },
        "cases": [case.model_dump(mode="json")],
    }

    rendered = render_diagnostic(document, expected_frame_counts=(100_000,), expected_repetitions=2)

    assert b'"median_encode_wall_seconds":2.0' in rendered
    assert b'"median_read_wall_seconds":3.0' in rendered
    assert b'"median_peak_rss_kib":15' in rendered
    for forbidden in (b'"license"', b'"decision"', b'"gates"', b'"production_adoption"'):
        assert forbidden not in rendered
