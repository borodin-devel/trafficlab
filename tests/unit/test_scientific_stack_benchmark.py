"""Scientific-stack benchmark protocol and evidence contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pytest

import trafficlab.comparison.similarity.multiscale as multiscale_module
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


@pytest.mark.parametrize(
    ("integer", "toward"),
    [
        (0.0, math.inf),
        (1.0, -math.inf),
        (1.0, math.inf),
        (3.0, -math.inf),
        (3.0, math.inf),
        (1024.0, -math.inf),
        (1024.0, math.inf),
    ],
)
def test_scalar_snap_uses_the_literal_four_ulp_boundary(integer: float, toward: float) -> None:
    four_ulps = integer
    for _ in range(4):
        four_ulps = math.nextafter(four_ulps, toward)
    fifth_float = math.nextafter(four_ulps, toward)

    assert benchmark._scalar_snap_near_integer(four_ulps) == integer  # pyright: ignore[reportPrivateUsage]
    assert benchmark._scalar_snap_near_integer(fifth_float) == fifth_float  # pyright: ignore[reportPrivateUsage]


def test_scalar_multiscale_does_not_call_production_snapping(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_production_snap(_quotient: float) -> float:
        raise AssertionError("scalar oracle called production snapping")

    monkeypatch.setattr(multiscale_module, "_snap_near_integer", fail_production_snap)
    monkeypatch.setattr(benchmark, "_production_snap_near_integer", fail_production_snap)
    trace = benchmark.generate_benchmark_trace(512)
    packets, byte_counts = benchmark._scalar_multiscale(trace)  # pyright: ignore[reportPrivateUsage]

    assert int(packets.sum()) == 3 * len(trace)
    assert int(byte_counts.sum()) == 3 * int(trace.frame_lengths.astype(object).sum())


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


def test_benchmark_rejects_fabricated_agreement() -> None:
    evidence = benchmark.parse_and_validate_evidence(_EVIDENCE.read_bytes(), repository_root=_ROOT)
    evidence["agreement"]["selected_lag_acf"]["max_abs_error"] = 0.0
    with pytest.raises(ValueError, match="independent kernel agreement"):
        benchmark.validate_evidence(evidence, repository_root=_ROOT)


def test_benchmark_rejects_fabricated_result_digests() -> None:
    fabricated_digests = benchmark.parse_and_validate_evidence(_EVIDENCE.read_bytes(), repository_root=_ROOT)
    for implementation in ("scalar", "vector"):
        for group in ("warmups", "samples"):
            for sample in fabricated_digests["implementations"][implementation][group]:
                sample["result_identities"] = {
                    name: "0" * 64 for name in ("normalization", "iat", "multiscale", "selected_lag_acf")
                }
    with pytest.raises(ValueError, match="independent result identities"):
        benchmark.validate_evidence(fabricated_digests, repository_root=_ROOT)


def test_benchmark_check_does_not_rerun_timing_subprocesses(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_timing(_implementation: str, _event_count: int) -> dict[str, object]:
        raise AssertionError("benchmark check spawned a timing subprocess")

    monkeypatch.setattr(benchmark, "_run_child", fail_timing)  # pyright: ignore[reportPrivateUsage]
    evidence = benchmark.parse_and_validate_evidence(_EVIDENCE.read_bytes(), repository_root=_ROOT)
    assert evidence["decision"]["passed"] is True


@pytest.mark.parametrize(
    "mutation",
    [
        "object",
        "fields",
        "warmups",
        "samples",
        "ordinal",
        "input",
        "rss",
        "wall_object",
        "wall_fields",
        "combined",
        "identity_object",
        "identity_fields",
        "nondeterministic",
        "independent",
        "medians",
    ],
)
def test_benchmark_measurement_validator_rejects_each_untrusted_boundary(mutation: str) -> None:
    evidence = json.loads(_EVIDENCE.read_bytes())
    value: Any = copy.deepcopy(evidence["implementations"]["scalar"])
    expected = value["samples"][0]["result_identities"]
    if mutation == "object":
        value = []
    elif mutation == "fields":
        value["unknown"] = True
    elif mutation == "warmups":
        value["warmups"] = []
    elif mutation == "samples":
        value["samples"] = []
    elif mutation == "ordinal":
        value["warmups"][0]["ordinal"] = 2
    elif mutation == "input":
        value["warmups"][0]["event_count"] = 1
    elif mutation == "rss":
        value["warmups"][0]["peak_rss_kib"] = 0
    elif mutation == "wall_object":
        value["warmups"][0]["wall_seconds"] = []
    elif mutation == "wall_fields":
        del value["warmups"][0]["wall_seconds"]["iat"]
    elif mutation == "combined":
        value["warmups"][0]["wall_seconds"]["combined_multiscale_acf"] += 1.0
    elif mutation == "identity_object":
        value["warmups"][0]["result_identities"] = []
    elif mutation == "identity_fields":
        del value["warmups"][0]["result_identities"]["iat"]
    elif mutation == "nondeterministic":
        value["samples"][1]["result_identities"]["iat"] = "f" * 64
    elif mutation == "independent":
        value["warmups"][0]["result_identities"] = {name: "f" * 64 for name in expected}
    else:
        value["medians"]["iat"] += 1.0

    with pytest.raises(ValueError):
        benchmark._validate_measurements(  # pyright: ignore[reportPrivateUsage]
            value,
            implementation="scalar",
            dataset=evidence["dataset"],
            expected_result_identities=expected,
        )


def test_benchmark_agreement_validator_rejects_incomplete_and_inconsistent_gates() -> None:
    evidence = json.loads(_EVIDENCE.read_bytes())
    expected = copy.deepcopy(evidence["agreement"])
    incomplete = copy.deepcopy(evidence)
    del incomplete["agreement"]["iat"]
    with pytest.raises(ValueError, match="components"):
        benchmark._validate_agreement(incomplete, expected)  # pyright: ignore[reportPrivateUsage]

    inconsistent = copy.deepcopy(evidence)
    inconsistent["agreement"]["iat"]["passed"] = False
    with pytest.raises(ValueError, match="gate"):
        benchmark._validate_agreement(inconsistent, expected)  # pyright: ignore[reportPrivateUsage]


def test_benchmark_evidence_rejects_noncanonical_json() -> None:
    """Equivalent but noncanonical JSON must not masquerade as the retained checked evidence."""
    document = json.loads(_EVIDENCE.read_bytes())
    noncanonical = json.dumps(document, indent=2).encode("utf-8")
    with pytest.raises(ValueError, match="canonical"):
        benchmark.parse_and_validate_evidence(noncanonical, repository_root=_ROOT)
