"""In-process fit/JSON/generate/PCAPNG pipeline for every built-in family."""

from __future__ import annotations

from pathlib import Path

from trafficlab.compatibility import identify_bytes
from trafficlab.config import (
    FloatBounds,
    GenerationLimits,
    IntegerBounds,
    MarkovRenewalConfig,
    MmppConfig,
    PoissonConfig,
)
from trafficlab.models import FamilyBounds, Genes, ModelFamily
from trafficlab.models.registry import (
    MARKOV_RENEWAL_FAMILY,
    MMPP_FAMILY,
    POISSON_FAMILY,
    load_best_model,
    make_best_model,
    render_best_model,
)
from trafficlab.pcapng import encode_pcapng, parse_pcapng_bytes
from trafficlab.trace import normalize_reference, parse_capture_metadata

_ROOT = Path(__file__).resolve().parents[2]
_DATA = _ROOT / "fixtures" / "examples" / "pipeline"
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
)


def _rounded_nanoseconds(timestamp: float) -> float:
    return round(timestamp * 1_000_000_000) / 1_000_000_000


def test_every_family_runs_through_model_json_and_byte_stable_pcapng() -> None:
    """Reopening or reinterpreting lineage inputs could decouple hashes from the bytes that were fitted."""
    capture_path = _DATA / "capture.json"
    reference_path = _DATA / "reference.pcapng"
    capture_content = capture_path.read_bytes()
    reference_content = reference_path.read_bytes()
    metadata = parse_capture_metadata(capture_content, source=capture_path)
    parsed_reference = parse_pcapng_bytes(reference_content, metadata, source=reference_path)
    normalized_reference, window = normalize_reference(parsed_reference)
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
        generated = family.generate(loaded.fitted, 54321, loaded.observation_window_seconds, _LIMITS).require_complete()
        pcapng_content = encode_pcapng(generated, metadata)
        parsed_generated = parse_pcapng_bytes(pcapng_content, metadata, source=Path("generated.pcapng"))

        assert [(event.direction, event.frame_length) for event in parsed_generated] == [
            (event.direction, event.frame_length) for event in generated
        ]
        assert [event.timestamp for event in parsed_generated] == [
            _rounded_nanoseconds(event.timestamp) for event in generated
        ]

        reloaded = load_best_model(render_best_model(loaded), source=Path("best_model.json"))
        reproduced = family.generate(
            reloaded.fitted,
            54321,
            reloaded.observation_window_seconds,
            _LIMITS,
        ).require_complete()
        assert encode_pcapng(reproduced, metadata) == pcapng_content
