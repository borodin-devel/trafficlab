import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

import trafficlab.comparison.codec as comparison_codec
import trafficlab.comparison.schema as comparison_schema
from tests.fixtures.paths import PIPELINE_FIXTURE_ROOT
from tests.support.comparison import (
    add_acf_feature_weight,
    add_input_identity_field,
    add_method_field,
    add_multiscale_scale_feature,
    add_top_level_field,
    change_aggregate,
    change_diagnostic_window,
    change_method_weight,
    corrupt_method_diagnostics,
    make_input_identity_non_string,
    make_input_identity_size_boolean,
    make_window_an_integer,
    method_document,
    remove_acf_vector,
    remove_iat_method,
    remove_input_identity,
    remove_multiscale_packet_totals,
    shorten_capture_hash,
    valid_result_document,
)
from tests.support.comparison import (
    settings as _settings,
)
from tests.support.comparison import (
    trace as _trace,
)
from trafficlab.common.compatibility import ContentIdentity
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.comparison.codec import parse_comparison_result, render_comparison_result
from trafficlab.comparison.metrics import compare_traces
from trafficlab.comparison.schema import ComparisonResult, MethodComparison


def test_strict_result_json_round_trip_has_the_documented_sorted_readable_shape() -> None:
    """Permissive or unstable JSON would make similarity identity and reuse ambiguous."""
    document = valid_result_document()

    result = ComparisonResult.from_dict(document)
    rendered = render_comparison_result(result)

    assert rendered == (json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    assert parse_comparison_result(rendered) == result
    assert result.as_dict() == document
    assert result.as_dict() is not result.as_dict()


@pytest.mark.parametrize(
    "method_name",
    ["frame_size_ks", "iat_ks", "autocorrelation", "multiscale_rate"],
)
@pytest.mark.parametrize(
    "corruption",
    [
        "missing",
        "extra",
        "nonfinite",
        "bool-alias",
        "int-alias",
        "out-of-range",
        "inconsistent-count",
        "inconsistent-length",
        "score-discrepancy",
        "internal-discrepancy",
    ],
)
def test_result_parser_rejects_method_specific_diagnostic_corruption(
    method_name: str,
    corruption: str,
) -> None:
    """Each retained method shape must reject structural, scalar, range, and mathematical drift."""
    document = valid_result_document()
    corrupt_method_diagnostics(document, method_name, corruption)

    with pytest.raises(ValueError):
        ComparisonResult.from_dict(document)


@pytest.mark.parametrize(
    ("method_name", "_nested_name", "mutation"),
    [
        ("autocorrelation", "missing-acf-feature-key", remove_acf_vector),
        ("autocorrelation", "extra-feature-weight-key", add_acf_feature_weight),
        ("multiscale_rate", "missing-direction-total-key", remove_multiscale_packet_totals),
        ("multiscale_rate", "extra-scale-feature-key", add_multiscale_scale_feature),
    ],
)
def test_result_parser_rejects_nested_diagnostic_schema_drift(
    method_name: str,
    _nested_name: str,
    mutation: Callable[[dict[str, object]], object],
) -> None:
    """Nested diagnostic objects must preserve their documented exact key sets."""
    document = valid_result_document()
    _method, diagnostics = method_document(document, method_name)
    mutation(diagnostics)

    with pytest.raises(ValueError):
        ComparisonResult.from_dict(document)


@pytest.mark.parametrize(
    ("method_name", "weight_name"),
    [
        ("autocorrelation", "lag_weights"),
        ("autocorrelation", "feature_weights"),
        ("multiscale_rate", "scale_weights"),
        ("multiscale_rate", "feature_weights"),
    ],
)
def test_result_parser_rejects_unnormalized_diagnostic_weights(method_name: str, weight_name: str) -> None:
    """Every configured diagnostic weight vector must remain normalized within the shared tolerance."""
    document = valid_result_document()
    _method, diagnostics = method_document(document, method_name)
    weights = diagnostics[weight_name]
    if type(weights) is list:
        cast(list[object], weights)[0] = 0.75
    else:
        weight_document = cast(dict[str, object], weights)
        weight_document[next(iter(weight_document))] = 0.75

    with pytest.raises(ValueError, match="weights.*sum to one"):
        ComparisonResult.from_dict(document)


def test_result_parser_rejects_a_subnormal_multiscale_width_without_leaking_arithmetic_errors() -> None:
    """A finite positive width whose W quotient overflows must remain an ordinary artifact validation error."""
    document = valid_result_document()
    _method, diagnostics = method_document(document, "multiscale_rate")
    cast(list[object], diagnostics["widths"])[0] = 5e-324

    with pytest.raises(ValueError, match="W divided by a width must be finite"):
        ComparisonResult.from_dict(document)


def test_method_parser_rejects_an_unknown_dispatch_name() -> None:
    """An unrecognized name must not borrow another method's diagnostic contract."""
    document = valid_result_document()
    method, _diagnostics = method_document(document, "frame_size_ks")

    with pytest.raises(ValueError, match="unsupported comparison method"):
        MethodComparison.from_dict("unknown", method)


@pytest.mark.parametrize(
    "mutation",
    [
        add_top_level_field,
        remove_iat_method,
        shorten_capture_hash,
        add_method_field,
        make_window_an_integer,
    ],
    ids=["unknown-top-level", "missing-method", "bad-hash", "unknown-method-field", "strict-window-float"],
)
def test_result_parser_rejects_schema_drift(mutation: Callable[[dict[str, object]], None]) -> None:
    """Accepting incomplete, extra, or coerced values would trust an artifact with a different meaning."""
    document = valid_result_document()
    mutation(document)

    with pytest.raises(ValueError):
        ComparisonResult.from_dict(document)


@pytest.mark.parametrize(
    "mutation",
    [
        change_diagnostic_window,
        change_method_weight,
        change_aggregate,
        remove_input_identity,
        make_input_identity_non_string,
        make_input_identity_size_boolean,
        add_input_identity_field,
    ],
    ids=[
        "different-window",
        "unnormalized-weights",
        "wrong-aggregate",
        "missing-identity",
        "non-object-identity",
        "boolean-identity-size",
        "extra-identity-field",
    ],
)
def test_result_parser_rejects_semantic_drift(mutation: Callable[[dict[str, object]], None]) -> None:
    """A structurally valid document must still preserve shared-W, weighting, and identity invariants."""
    document = valid_result_document()
    mutation(document)

    with pytest.raises(ValueError):
        ComparisonResult.from_dict(document)


@pytest.mark.parametrize(
    ("window", "diagnostic_window"),
    [(3.0, 3), (1.0, True)],
    ids=["integer-equal-to-float", "boolean-equal-to-one"],
)
def test_result_parser_requires_diagnostic_windows_to_be_floats(window: float, diagnostic_window: int | bool) -> None:
    """Python numeric equality must not let integer or boolean diagnostic W impersonate the shared float W."""
    document = valid_result_document()
    document["observation_window_seconds"] = window
    methods = cast(dict[str, object], document["methods"])
    for method in methods.values():
        diagnostics = cast(dict[str, object], cast(dict[str, object], method)["diagnostics"])
        diagnostics["observation_window_seconds"] = window
    first = cast(dict[str, object], methods["autocorrelation"])
    cast(dict[str, object], first["diagnostics"])["observation_window_seconds"] = diagnostic_window

    with pytest.raises(ValueError, match="diagnostic.*finite positive float"):
        ComparisonResult.from_dict(document)


def test_result_parser_rejects_duplicate_json_keys() -> None:
    """Last-value-wins duplicate keys would make validation dependent on the JSON decoder."""
    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_comparison_result(b'{"aggregate_score":0.0,"aggregate_score":1.0}')


@pytest.mark.parametrize("content", [b"{", b"\xff"], ids=["malformed-json", "invalid-utf8"])
def test_result_parser_rejects_invalid_json_bytes(content: bytes) -> None:
    """Decoder failures must not escape the strict typed artifact boundary."""
    with pytest.raises(ValueError, match="invalid similarity JSON"):
        parse_comparison_result(content)


@pytest.mark.parametrize("content", [None, b"{}"], ids=["missing", "malformed"])
def test_result_loader_reports_missing_or_malformed_artifacts(content: bytes | None, tmp_path: Path) -> None:
    """File and schema failures need the package's corrective error boundary."""
    path = tmp_path / "similarity.json"
    if content is not None:
        path.write_bytes(content)

    with pytest.raises(TrafficlabError, match="similarity artifact") as error:
        comparison_codec.load_comparison_result(path)

    assert error.value.corrective_action


def test_result_serializer_requires_file_identities(valid_config_data: dict[str, object]) -> None:
    """Publishing an aggregation-only result would omit the artifact inputs needed for reproduction."""
    result = compare_traces(_trace(), _trace(), 3.0, _settings(valid_config_data))

    with pytest.raises(ValueError, match="input content identities"):
        render_comparison_result(result)


def test_sha256_helpers_hash_exact_file_bytes_and_only_effective_similarity_settings(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Including an absolute run path in the settings identity would make an otherwise equal comparison differ."""
    file_path = tmp_path / "input.bin"
    file_path.write_bytes(b"trafficlab\x00fixture\n")
    first = ExperimentConfig.model_validate(valid_config_data)
    moved_run = first.run.model_copy(update={"directory": tmp_path / "different-absolute-run"})
    second = first.model_copy(update={"run": moved_run})

    assert comparison_codec.sha256_file(file_path) == "6107e6e2956c92c3c474c458617dda297a2e1a7a64d0fb20fd8ba43f2b378254"
    assert comparison_codec.similarity_settings_sha256(first.similarity) == comparison_codec.similarity_settings_sha256(
        second.similarity
    )
    compact = json.dumps(first.similarity.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    assert comparison_codec.similarity_settings_sha256(first.similarity) == comparison_codec.sha256_bytes(compact)


def test_sha256_file_reports_an_unreadable_input(tmp_path: Path) -> None:
    """An absent identified input must abort rather than silently hashing an empty value."""
    with pytest.raises(TrafficlabError, match="could not hash comparison input") as error:
        comparison_codec.sha256_file(tmp_path / "missing.pcapng")

    assert error.value.corrective_action == "verify missing.pcapng exists and is readable"


def test_comparison_renderer_rejects_invalid_outer_methods_and_changed_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Renderer validation must reject invalid outer state and a parser result that changes identity."""
    valid = comparison_codec.parse_comparison_result((PIPELINE_FIXTURE_ROOT / "similarity.json").read_bytes())
    invalid = valid.model_copy(update={"methods": object()})
    with pytest.raises(ValueError, match="canonical methods object"):
        comparison_codec.render_comparison_result(invalid)

    assert valid.input_identities is not None
    identities = valid.input_identities.as_content_identities()
    identities["capture_json"] = ContentIdentity(size=1, sha256="0" * 64)
    changed = valid.with_input_identities(identities)
    changed_published = comparison_schema.PublishedComparisonResult.model_validate(changed.as_dict())
    real_validate = comparison_schema.PublishedComparisonResult.model_validate
    calls = 0

    def changed_on_reparse(
        _cls: type[comparison_schema.PublishedComparisonResult], value: object
    ) -> comparison_schema.PublishedComparisonResult:
        nonlocal calls
        calls += 1
        return changed_published if calls == 2 else real_validate(value)

    monkeypatch.setattr(
        comparison_schema.PublishedComparisonResult,
        "model_validate",
        classmethod(changed_on_reparse),
    )
    with pytest.raises(ValueError, match="changed the validated comparison result"):
        comparison_codec.render_comparison_result(valid)
