"""In-process fit/JSON/generate/PCAPNG pipeline for every built-in family."""

from __future__ import annotations

from pathlib import Path

from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.config import (
    AcdConfig,
    FloatBounds,
    GenerationLimits,
    IntegerBounds,
    MarkovRenewalConfig,
    MmppConfig,
    PoissonConfig,
)
from trafficlab.common.scapy_io import encode_pcapng, read_pcapng
from trafficlab.common.trace import TrafficTrace, normalize_reference, parse_capture_metadata
from trafficlab.generation.models import FamilyBounds, Genes, ModelFamily
from trafficlab.generation.models.fitted_model import (
    load_best_model,
    make_best_model,
    render_best_model,
    runtime_fitted_model,
)
from trafficlab.generation.models.registry import (
    ACD_FAMILY,
    MARKOV_RENEWAL_FAMILY,
    MMPP_FAMILY,
    POISSON_FAMILY,
)

_ROOT = Path(__file__).resolve().parents[3]
_DATA = _ROOT / "examples" / "data"
_LIMITS = GenerationLimits(max_packets=10_000, max_output_bytes=10_000_000, max_wall_seconds=10.0)

CASES: tuple[tuple[ModelFamily, Genes, FamilyBounds], ...] = (
    (
        POISSON_FAMILY,
        (1.0,),
        PoissonConfig(c_lambda=FloatBounds(lower=0.25, upper=4.0)),
    ),
    (
        MARKOV_RENEWAL_FAMILY,
        (0.25, 0.75, 0.5, 2, 1.0),
        MarkovRenewalConfig(
            q1=FloatBounds(lower=0.1, upper=0.4),
            q2=FloatBounds(lower=0.6, upper=0.9),
            alpha=FloatBounds(lower=0.0, upper=2.0),
            r=IntegerBounds(lower=1, upper=8),
            c_t=FloatBounds(lower=0.25, upper=4.0),
        ),
    ),
    (
        MMPP_FAMILY,
        (0.5, 0.75, 0.25, 1.0),
        MmppConfig(
            q01=FloatBounds(lower=0.01, upper=10.0),
            q10=FloatBounds(lower=0.01, upper=10.0),
            lambda0=FloatBounds(lower=0.01, upper=100.0),
            lambda1=FloatBounds(lower=0.1, upper=1000.0),
        ),
    ),
    (
        ACD_FAMILY,
        (1,),
        AcdConfig(order=IntegerBounds(lower=1, upper=3)),
    ),
)


def _scapy_microseconds(timestamp: float) -> float:
    return int(timestamp * 1_000_000) / 1_000_000


def test_every_family_runs_through_model_json_and_byte_stable_pcapng() -> None:
    """Reopening or reinterpreting lineage inputs could decouple hashes from the bytes that were fitted."""
    capture_path = _DATA / "capture.json"
    reference_path = _DATA / "reference.pcapng"
    capture_content = capture_path.read_bytes()
    reference_content = reference_path.read_bytes()
    metadata = parse_capture_metadata(capture_content, source=capture_path)
    parsed_reference = read_pcapng(reference_path, metadata)
    normalized_reference, window = normalize_reference(parsed_reference)
    assert isinstance(normalized_reference, TrafficTrace)
    capture_identity = identify_bytes(capture_content)
    reference_identity = identify_bytes(reference_content)

    for family, genes, bounds in CASES:
        artifact = make_best_model(
            family,
            normalized_reference,
            genes,
            reference_identity=reference_identity,
            capture_identity=capture_identity,
            final_seed=54321,
            final_limits=_LIMITS,
            W=window,
            bounds=bounds,
        )
        loaded = load_best_model(render_best_model(artifact), source=Path("best_model.json"))
        generated = family.generate(
            runtime_fitted_model(loaded), 54321, loaded.observation_window_seconds, _LIMITS
        ).require_complete()
        encoded = encode_pcapng(generated, metadata, observation_window_seconds=loaded.observation_window_seconds)
        pcapng_content = encoded.content
        parsed_generated = encoded.trace

        assert [(event.direction, event.frame_length) for event in parsed_generated] == [
            (event.direction, event.frame_length) for event in generated
        ]
        assert [event.timestamp for event in parsed_generated] == [
            _scapy_microseconds(event.timestamp) for event in generated
        ]

        reloaded = load_best_model(render_best_model(loaded), source=Path("best_model.json"))
        reproduced = family.generate(
            runtime_fitted_model(reloaded),
            54321,
            reloaded.observation_window_seconds,
            _LIMITS,
        ).require_complete()
        assert (
            encode_pcapng(
                reproduced,
                metadata,
                observation_window_seconds=reloaded.observation_window_seconds,
            ).content
            == pcapng_content
        )
