"""Traffic comparison metrics ownership."""

import math
from collections.abc import Iterable

from trafficlab.common.config import SimilarityConfig
from trafficlab.common.errors import (
    TrafficlabError,
)
from trafficlab.common.trace import (
    TraceEvent,
    TrafficTrace,
)
from trafficlab.comparison.diagnostics import FITNESS_METHOD_NAMES, WEIGHT_TOLERANCE
from trafficlab.comparison.schema import ComparisonResult, MethodComparison
from trafficlab.comparison.similarity.autocorrelation import (
    AutocorrelationSamplesInsufficientError,
    autocorrelation_similarity,
)
from trafficlab.comparison.similarity.ecdf import anderson_darling_similarity, cramer_von_mises_similarity
from trafficlab.comparison.similarity.jensen_shannon import jensen_shannon_similarity
from trafficlab.comparison.similarity.ks import frame_size_ks, iat_ks
from trafficlab.comparison.similarity.mmd import approximate_mmd_similarity
from trafficlab.comparison.similarity.multiscale import multiscale_rate_similarity


def _bounded_weighted_score(value: float) -> float:
    """Clamp only the weight-sum tolerance already accepted by configuration."""
    if -WEIGHT_TOLERANCE <= value < 0.0:
        return 0.0
    if 1.0 < value <= 1.0 + WEIGHT_TOLERANCE:
        return 1.0
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError("aggregate_score must be a finite float in [0, 1]")
    return value


def evaluate_fitness(
    reference: Iterable[TraceEvent] | TrafficTrace,
    generated: Iterable[TraceEvent] | TrafficTrace,
    W: float,
    settings: SimilarityConfig,
) -> ComparisonResult:
    """Evaluate all eight configured fitness methods over exactly one observation window."""
    reference_trace = reference if type(reference) is TrafficTrace else TrafficTrace.from_events(reference)
    generated_trace = generated if type(generated) is TrafficTrace else TrafficTrace.from_events(generated)
    frame_size = frame_size_ks(reference_trace, generated_trace, W)
    interarrival = iat_ks(reference_trace, generated_trace, W, settings.iat_diagnostic_quantile)
    try:
        autocorrelation = autocorrelation_similarity(
            reference_trace,
            generated_trace,
            W,
            settings.acf_lags,
            settings.acf_lag_weights,
            settings.acf_iat_weight,
            settings.acf_size_weight,
        )
    except AutocorrelationSamplesInsufficientError as error:
        raise TrafficlabError(
            "autocorrelation requires more samples",
            corrective_action="correct samples or settings",
        ) from error
    component_results = {
        "frame_size_ks": frame_size,
        "iat_ks": interarrival,
        "autocorrelation": autocorrelation,
        "multiscale_rate": multiscale_rate_similarity(
            reference_trace,
            generated_trace,
            W,
            settings.multiscale_widths_seconds,
            settings.multiscale_scale_weights,
            settings.multiscale_packet_weight,
            settings.multiscale_byte_weight,
            settings.max_direction_bin_cells,
        ),
        "cramer_von_mises": cramer_von_mises_similarity(
            reference_trace,
            generated_trace,
            W,
            settings.cvm_iat_weight,
            settings.cvm_size_weight,
        ),
        "anderson_darling": anderson_darling_similarity(
            reference_trace,
            generated_trace,
            W,
            settings.ad_iat_weight,
            settings.ad_size_weight,
        ),
        "jensen_shannon": jensen_shannon_similarity(
            reference_trace,
            generated_trace,
            W,
            settings.js_iat_bin_count,
            settings.js_iat_weight,
            settings.js_mark_weight,
        ),
        "approximate_mmd": approximate_mmd_similarity(
            reference_trace,
            generated_trace,
            W,
            settings.mmd_feature_count,
            settings.mmd_seed,
            settings.mmd_scale_floor,
        ),
    }
    configured_weights = settings.method_weights.model_dump()
    try:
        methods = {
            name: MethodComparison.model_validate(
                {
                    "score": component_results[name].score,
                    "weight": configured_weights[name],
                    "diagnostics": component_results[name].diagnostics,
                }
            )
            for name in FITNESS_METHOD_NAMES
        }
        aggregate = _bounded_weighted_score(math.fsum(method.weight * method.score for method in methods.values()))
        return ComparisonResult.model_validate(
            {
                "aggregate_score": aggregate,
                "observation_window_seconds": W,
                "methods": methods,
                "input_identities": None,
            }
        )
    except ValueError as error:
        raise TrafficlabError(
            f"invalid comparison result: {error}",
            corrective_action="report the comparison result assembly defect",
        ) from error


def compare_traces(
    reference: Iterable[TraceEvent] | TrafficTrace,
    generated: Iterable[TraceEvent] | TrafficTrace,
    W: float,
    settings: SimilarityConfig,
) -> ComparisonResult:
    """Compatibility spelling for the shared eight-method fitness evaluator."""
    return evaluate_fitness(reference, generated, W, settings)
