"""Scientific-stack benchmark protocol and evidence contracts."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import benchmark_scientific_stack as benchmark

_ROOT = Path(__file__).parents[2]
_EVIDENCE = _ROOT / "examples" / "scientific_stack" / "benchmark.json"


def test_benchmark_input_is_the_locked_pcg64_sequence() -> None:
    """Changing draw order, bounds, or bit generator would invalidate performance comparability."""
    trace = benchmark.generate_benchmark_trace(16)

    assert benchmark.SEED == 20260819
    assert benchmark.EVENT_COUNT == 1_000_000
    assert benchmark.BIT_GENERATOR == "PCG64"
    assert benchmark.WARMUP_SUBPROCESS_COUNT == 1
    assert benchmark.MEASURED_SUBPROCESS_COUNT == 5
    assert {
        "timestamps": hashlib.sha256(trace.timestamps.tobytes()).hexdigest(),
        "directions": hashlib.sha256(trace.directions.tobytes()).hexdigest(),
        "frame_lengths": hashlib.sha256(trace.frame_lengths.tobytes()).hexdigest(),
    } == {
        "timestamps": "d5d687d127fac1feaa22ac4df516130ec054e90c1cc96829c541dbbbf220de18",
        "directions": "9239c79861194a0d7c9a7ccd394ff17968a15e2314813b24b7fdfaa3dd9d2ecc",
        "frame_lengths": "68bacf3512f74d8f3c84a61b48d5008899d5effc474fab7b6881d859c52bbf16",
    }


@pytest.mark.parametrize("event_count", [0, -1, True, 1.5])
def test_benchmark_input_rejects_nonpositive_or_noninteger_counts(event_count: object) -> None:
    """An ambiguous event count would make draw identity and sample cardinality unverifiable."""
    with pytest.raises(ValueError, match="positive integer"):
        benchmark.generate_benchmark_trace(event_count)  # type: ignore[arg-type]


def test_scalar_and_vector_kernels_agree_independently() -> None:
    """A fast result that changes normalization, IAT, multiscale, or selected-lag ACF is invalid."""
    agreement = benchmark.compare_kernel_results(benchmark.generate_benchmark_trace(512))

    assert tuple(agreement) == ("normalization", "iat", "multiscale", "selected_lag_acf")
    assert all(component["max_abs_error"] <= 1e-12 for component in agreement.values())
    assert all(component["passed"] is True for component in agreement.values())


def test_checked_benchmark_retains_five_fresh_samples_and_recomputes_gate() -> None:
    """Dropping samples or trusting stored medians could manufacture the performance decision."""
    content = _EVIDENCE.read_bytes()
    evidence = benchmark.parse_and_validate_evidence(content, repository_root=_ROOT)

    assert evidence["dataset"]["seed"] == 20260819
    assert evidence["dataset"]["event_count"] == 1_000_000
    assert evidence["protocol"]["warmup_subprocess_count"] == 1
    assert evidence["protocol"]["measured_subprocess_count"] == 5
    assert evidence["protocol"]["parent_command"] == [
        "scripts/run_bounded.sh",
        "--memory-high",
        "6G",
        "--memory-max",
        "8G",
        "--swap-max",
        "1G",
        "--wall-time",
        "15m",
        "--kill-after",
        "10s",
        "--",
        "uv",
        "run",
        "--locked",
        "python",
        "scripts/benchmark_scientific_stack.py",
    ]
    source_files = evidence["environment"]["source_files"]
    assert set(source_files) == {
        "scripts/benchmark_scientific_stack.py",
        "src/trafficlab/similarity/autocorrelation.py",
        "src/trafficlab/similarity/multiscale.py",
        "src/trafficlab/trace.py",
    }
    for relative, identity in source_files.items():
        content_at_path = (_ROOT / relative).read_bytes()
        assert identity == {
            "sha256": hashlib.sha256(content_at_path).hexdigest(),
            "size": len(content_at_path),
        }
    for implementation in ("scalar", "vector"):
        measurements = evidence["implementations"][implementation]
        assert len(measurements["warmups"]) == 1
        assert len(measurements["samples"]) == 5
        assert all(sample["fresh_subprocess"] is True for sample in measurements["samples"])
    assert evidence["decision"]["passed"] is True
    assert (
        evidence["comparison"]["combined_multiscale_acf_speedup"] >= 3.0
        or evidence["comparison"]["peak_rss_ratio"] <= 0.5
    )
    assert content == benchmark.canonical_json_bytes(evidence)

    missing_sample = copy.deepcopy(evidence)
    missing_sample["implementations"]["vector"]["samples"].pop()
    with pytest.raises(ValueError, match="five measured"):
        benchmark.validate_evidence(missing_sample, repository_root=_ROOT)

    false_decision = copy.deepcopy(evidence)
    false_decision["decision"]["passed"] = False
    with pytest.raises(ValueError, match="decision"):
        benchmark.validate_evidence(false_decision, repository_root=_ROOT)

    wrong_environment = copy.deepcopy(evidence)
    wrong_environment["environment"]["machine"] = "wrong-machine"
    with pytest.raises(ValueError, match="environment"):
        benchmark.validate_evidence(wrong_environment, repository_root=_ROOT)


def test_benchmark_evidence_rejects_noncanonical_json() -> None:
    """Equivalent but noncanonical JSON must not masquerade as the retained checked evidence."""
    document = json.loads(_EVIDENCE.read_bytes())
    noncanonical = json.dumps(document, indent=2).encode("utf-8")
    with pytest.raises(ValueError, match="canonical"):
        benchmark.parse_and_validate_evidence(noncanonical, repository_root=_ROOT)
