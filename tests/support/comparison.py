import copy
import json
import math
from pathlib import Path
from typing import cast

from tests.fixtures.paths import PIPELINE_FIXTURE_ROOT
from trafficlab.artifacts.run_directory import create_run_directory
from trafficlab.common.config import ExperimentConfig, SimilarityConfig
from trafficlab.common.config_io import render_effective_config
from trafficlab.common.trace import Direction, TraceEvent
from trafficlab.comparison.schema import ComparisonResult

EXPECTED_AGGREGATE_SCORE = 0.5662202380952381


def settings(data: dict[str, object]) -> SimilarityConfig:
    return ExperimentConfig.model_validate(data).similarity


def trace() -> tuple[TraceEvent, ...]:
    return (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(1.0, Direction.INBOUND, 80),
        TraceEvent(3.0, Direction.OUTBOUND, 100),
    )


def prepare_comparison_run(valid_config_data: dict[str, object], tmp_path: Path) -> tuple[Path, Path, ExperimentConfig]:
    """Create one complete comparison input tree from checked scientific bytes."""
    data = copy.deepcopy(valid_config_data)
    run_directory = tmp_path / "run"
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    config = ExperimentConfig.model_validate(data)
    caller_path = tmp_path / "caller.toml"
    caller_path.write_bytes(render_effective_config(config))
    create_run_directory(config)
    for artifact_name in ("capture.json", "reference.pcapng", "best_model.json", "generated.pcapng"):
        (run_directory / artifact_name).write_bytes((PIPELINE_FIXTURE_ROOT / artifact_name).read_bytes())
    return caller_path, run_directory, config


def comparison_log_records(run_directory: Path) -> list[dict[str, object]]:
    """Load the canonical run-log objects published by a comparison test."""
    return [json.loads(line) for line in (run_directory / "run.log").read_text(encoding="utf-8").splitlines()]


def _add_top_level_field(document: dict[str, object]) -> None:
    document["unexpected"] = 1


def _remove_iat_method(document: dict[str, object]) -> None:
    cast(dict[str, object], document["methods"]).pop("iat_ks")


def _shorten_capture_hash(document: dict[str, object]) -> None:
    inputs = cast(dict[str, object], document["input_identities"])
    cast(dict[str, object], inputs["capture_json"])["sha256"] = "short"


def _add_method_field(document: dict[str, object]) -> None:
    methods = cast(dict[str, object], document["methods"])
    cast(dict[str, object], methods["iat_ks"])["unknown"] = 1


def _make_window_an_integer(document: dict[str, object]) -> None:
    document["observation_window_seconds"] = 3


def _change_diagnostic_window(document: dict[str, object]) -> None:
    methods = cast(dict[str, object], document["methods"])
    method = cast(dict[str, object], methods["iat_ks"])
    cast(dict[str, object], method["diagnostics"])["observation_window_seconds"] = 4.0


def _change_method_weight(document: dict[str, object]) -> None:
    methods = cast(dict[str, object], document["methods"])
    cast(dict[str, object], methods["iat_ks"])["weight"] = 0.5


def _change_aggregate(document: dict[str, object]) -> None:
    document["aggregate_score"] = 0.75


def _remove_input_identity(document: dict[str, object]) -> None:
    cast(dict[str, object], document["input_identities"]).pop("capture_json")


def _make_input_identity_non_string(document: dict[str, object]) -> None:
    cast(dict[str, object], document["input_identities"])["capture_json"] = 1


def _make_input_identity_size_boolean(document: dict[str, object]) -> None:
    inputs = cast(dict[str, object], document["input_identities"])
    cast(dict[str, object], inputs["capture_json"])["size"] = True


def _add_input_identity_field(document: dict[str, object]) -> None:
    inputs = cast(dict[str, object], document["input_identities"])
    cast(dict[str, object], inputs["capture_json"])["path"] = "capture.json"


def valid_result_document() -> dict[str, object]:
    fixture = Path(__file__).parents[2] / "examples" / "data" / "similarity.json"
    return cast(dict[str, object], json.loads(fixture.read_bytes()))


def valid_result() -> ComparisonResult:
    return ComparisonResult.from_dict(valid_result_document())


def method_document(document: dict[str, object], method_name: str) -> tuple[dict[str, object], dict[str, object]]:
    methods = cast(dict[str, object], document["methods"])
    method = cast(dict[str, object], methods[method_name])
    return method, cast(dict[str, object], method["diagnostics"])


def corrupt_method_diagnostics(document: dict[str, object], method_name: str, corruption: str) -> None:
    method, diagnostics = method_document(document, method_name)
    discrepancy_name = "distance" if method_name in ("frame_size_ks", "iat_ks") else "discrepancy"
    if corruption == "missing":
        diagnostics.pop(discrepancy_name)
    elif corruption == "extra":
        diagnostics["unexpected"] = 0
    elif corruption == "nonfinite":
        diagnostics[discrepancy_name] = math.nan
    elif corruption == "bool-alias":
        if method_name == "frame_size_ks":
            diagnostics["reference_count"] = True
        elif method_name == "iat_ks":
            diagnostics["reference_iat_count"] = True
        elif method_name == "autocorrelation":
            cast(list[object], diagnostics["lags"])[0] = True
        else:
            diagnostics["total_direction_bin_cells"] = True
    elif corruption == "int-alias":
        if method_name in ("frame_size_ks", "iat_ks"):
            diagnostics["distance"] = 0
        elif method_name == "autocorrelation":
            diagnostics["discrepancy"] = 0
        else:
            cast(list[object], diagnostics["widths"])[0] = 1
    elif corruption == "out-of-range":
        if method_name in ("frame_size_ks", "iat_ks"):
            diagnostics["distance"] = 1.1
        elif method_name == "autocorrelation":
            feature = cast(dict[str, object], diagnostics["iat"])
            cast(list[object], feature["reference_acf"])[0] = 1.1
        else:
            features = cast(dict[str, object], diagnostics["feature_discrepancies"])
            features["packet"] = 1.1
    elif corruption == "inconsistent-count":
        if method_name == "frame_size_ks":
            diagnostics["reference_minimum_length"] = 141
        elif method_name == "iat_ks":
            diagnostics["reference_zero_iat_count"] = 5
        elif method_name == "autocorrelation":
            cast(dict[str, object], diagnostics["iat"])["reference_sample_count"] = 1
        else:
            diagnostics["total_direction_bin_cells"] = 221
    elif corruption == "inconsistent-length":
        if method_name == "frame_size_ks":
            diagnostics["reference_count"] = 0
        elif method_name == "iat_ks":
            diagnostics["generated_iat_count"] = 0
        elif method_name == "autocorrelation":
            cast(list[object], cast(dict[str, object], diagnostics["size"])["generated_acf"]).pop()
        else:
            cast(list[object], diagnostics["scale_discrepancies"]).pop()
    elif corruption == "score-discrepancy":
        method["score"] = 0.123
    elif corruption == "internal-discrepancy":
        if method_name in ("frame_size_ks", "iat_ks"):
            diagnostics["distance"] = 0.123
        elif method_name == "autocorrelation":
            diagnostics["discrepancy"] = 0.123
        else:
            diagnostics["discrepancy"] = 0.123
    else:
        raise AssertionError(f"unknown corruption {corruption}")


def _remove_acf_vector(diagnostics: dict[str, object]) -> object:
    return cast(dict[str, object], diagnostics["iat"]).pop("generated_acf")


def _add_acf_feature_weight(diagnostics: dict[str, object]) -> None:
    cast(dict[str, object], diagnostics["feature_weights"])["unexpected"] = 0.0


def _remove_multiscale_packet_totals(diagnostics: dict[str, object]) -> object:
    scale = cast(dict[str, object], cast(list[object], diagnostics["scales"])[0])
    return cast(dict[str, object], scale["reference_totals"]).pop("packet")


def _add_multiscale_scale_feature(diagnostics: dict[str, object]) -> None:
    scale = cast(dict[str, object], cast(list[object], diagnostics["scales"])[0])
    cast(dict[str, object], scale["feature_discrepancies"])["unexpected"] = 0.0


add_top_level_field = _add_top_level_field
remove_iat_method = _remove_iat_method
shorten_capture_hash = _shorten_capture_hash
add_method_field = _add_method_field
make_window_an_integer = _make_window_an_integer
change_diagnostic_window = _change_diagnostic_window
change_method_weight = _change_method_weight
change_aggregate = _change_aggregate
remove_input_identity = _remove_input_identity
make_input_identity_non_string = _make_input_identity_non_string
make_input_identity_size_boolean = _make_input_identity_size_boolean
add_input_identity_field = _add_input_identity_field
remove_acf_vector = _remove_acf_vector
add_acf_feature_weight = _add_acf_feature_weight
remove_multiscale_packet_totals = _remove_multiscale_packet_totals
add_multiscale_scale_feature = _add_multiscale_scale_feature
