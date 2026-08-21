import copy
import tomllib
from pathlib import Path
from typing import cast

import pytest
import tomli_w

import trafficlab.common.config_io as config_io
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import load_experiment, render_effective_config
from trafficlab.common.errors import TrafficlabError


def _write_config(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data), encoding="utf-8")


def test_valid_toml_loads_as_an_experiment_config(valid_config_data: dict[str, object], tmp_path: Path) -> None:
    """Failing to validate a valid document would block every experiment entry point."""
    path = tmp_path / "experiment.toml"
    _write_config(path, valid_config_data)

    config = load_experiment(path)

    assert isinstance(config, ExperimentConfig)
    assert config.run.master_seed == 12345


def test_utf8_toml_text_is_loaded_without_losing_characters(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Reading with the wrong encoding would corrupt valid non-ASCII experiment values."""
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["target"])["environment"] = {"GREETING": "Привет"}
    path = tmp_path / "experiment.toml"
    _write_config(path, data)

    config = load_experiment(path)

    assert config.target.environment == {"GREETING": "Привет"}


def test_invalid_utf8_is_reported_as_a_trafficlab_error(tmp_path: Path) -> None:
    """Leaking UnicodeDecodeError would bypass the package's corrective error boundary."""
    path = tmp_path / "experiment.toml"
    path.write_bytes(b'key = "\xff"\n')

    with pytest.raises(TrafficlabError, match="not valid UTF-8") as error:
        load_experiment(path)

    assert error.value.corrective_action == "save the experiment file as valid UTF-8 and retry"


def test_relative_run_and_mount_sources_resolve_against_the_config_file(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Resolving against the process directory would make a saved experiment non-portable."""
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = "runs/case"
    absolute_source = tmp_path / "shared-data"
    cast(dict[str, object], data["target"])["mounts"] = [
        {"source": "data", "target": "/data", "read_only": True},
        {"source": str(absolute_source), "target": "/shared", "read_only": True},
    ]
    path = tmp_path / "config" / "experiment.toml"
    _write_config(path, data)

    config = load_experiment(path)

    assert config.run.directory == (tmp_path / "config" / "runs" / "case").resolve()
    assert config.target.mounts[0].source == (tmp_path / "config" / "data").resolve()
    assert config.target.mounts[1].source == absolute_source


def _non_path_dump(config: ExperimentConfig) -> dict[str, object]:
    data = config.model_dump(mode="json")
    run = cast(dict[str, object], data["run"])
    del run["directory"]
    for mount in cast(list[dict[str, object]], cast(dict[str, object], data["target"])["mounts"]):
        del mount["source"]
    return data


def test_configuration_pair_retains_portable_values_across_relocation(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Resolving more than host paths would make a copied experiment scientifically different."""
    paths = (tmp_path / "first" / "config" / "experiment.toml", tmp_path / "second" / "config" / "experiment.toml")
    pairs: list[config_io.ConfigurationPair] = []
    for path in paths:
        data = copy.deepcopy(valid_config_data)
        cast(dict[str, object], data["run"])["directory"] = "runs/portable-case"
        target = cast(dict[str, object], data["target"])
        target["argv"] = ["--request", "custom-request.txt"]
        target["environment"] = {"MODE": "portable", "RETRIES": "7"}
        target["mounts"] = [{"source": "data", "target": "/work/data", "read_only": True}]
        _write_config(path, data)
        pairs.append(config_io.load_configuration_pair(path))

    first, second = pairs

    assert first.portable == second.portable
    assert first.realized.run.directory != second.realized.run.directory
    assert first.realized.target.mounts[0].source != second.realized.target.mounts[0].source
    assert _non_path_dump(first.portable) == _non_path_dump(first.realized)
    assert _non_path_dump(first.realized) == _non_path_dump(second.realized)


@pytest.mark.parametrize(
    "section",
    ("run", "target", "capture", "models"),
    ids=("seeds-bounds-limits", "image-argv-environment", "url", "methods-similarity-operators"),
)
def test_configuration_realization_preserves_every_non_path_section(
    valid_config_data: dict[str, object], tmp_path: Path, section: str
) -> None:
    """Changing a configuration value while realizing paths would invalidate reproducibility."""
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = "runs/portable-case"
    cast(dict[str, object], data["target"])["mounts"] = [{"source": "data", "target": "/work/data", "read_only": True}]
    path = tmp_path / "config" / "experiment.toml"
    _write_config(path, data)

    pair = config_io.load_configuration_pair(path)

    assert _non_path_dump(pair.portable)[section] == _non_path_dump(pair.realized)[section]


@pytest.mark.parametrize(
    "method_weights",
    [
        {"frame_size_ks": 1.0, "iat_ks": 0.0, "autocorrelation": 0.0, "multiscale_rate": 0.0},
        {"frame_size_ks": 0.0, "iat_ks": 1.0, "autocorrelation": 0.0, "multiscale_rate": 0.0},
        {"frame_size_ks": 0.0, "iat_ks": 0.0, "autocorrelation": 1.0, "multiscale_rate": 0.0},
        {"frame_size_ks": 0.0, "iat_ks": 0.0, "autocorrelation": 0.0, "multiscale_rate": 1.0},
        {"frame_size_ks": 0.1, "iat_ks": 0.2, "autocorrelation": 0.3, "multiscale_rate": 0.4},
    ],
)
def test_similarity_method_weights_round_trip_through_portable_effective_toml(
    valid_config_data: dict[str, object], tmp_path: Path, method_weights: dict[str, float]
) -> None:
    """Rendering a portable config must retain every mandatory method even when its aggregation weight is zero."""
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = "runs/weight-round-trip"
    cast(dict[str, object], data["target"])["mounts"] = [
        {"source": "fixture-data", "target": "/work/data", "read_only": True}
    ]
    cast(dict[str, object], data["similarity"])["method_weights"] = method_weights
    source = tmp_path / "config" / "source.toml"
    snapshot = tmp_path / "config" / "effective.toml"
    _write_config(source, data)

    loaded = config_io.load_configuration_pair(source)
    snapshot.write_bytes(render_effective_config(loaded.portable))
    reloaded = config_io.load_configuration_pair(snapshot)

    assert reloaded.portable.similarity == loaded.portable.similarity
    assert reloaded.realized.similarity == loaded.realized.similarity
    assert reloaded.portable.similarity.method_weights.model_dump() == method_weights
    assert tuple(reloaded.portable.similarity.method_weights.model_dump()) == (
        "frame_size_ks",
        "iat_ks",
        "autocorrelation",
        "multiscale_rate",
    )
    assert _non_path_dump(reloaded.portable) == _non_path_dump(loaded.portable)


@pytest.mark.parametrize(
    "resolution_error",
    [OSError("injected resolution failure"), RuntimeError("injected resolution failure")],
    ids=["os-error", "runtime-error"],
)
def test_path_resolution_failure_is_reported_as_a_trafficlab_error(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resolution_error: OSError | RuntimeError,
) -> None:
    """Leaking resolution errors would bypass the package's corrective error boundary."""
    path = tmp_path / "experiment.toml"
    _write_config(path, valid_config_data)

    def fail_resolve(_path: Path, strict: bool = False) -> Path:
        del strict
        raise resolution_error

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    with pytest.raises(TrafficlabError, match="could not resolve experiment paths") as error:
        load_experiment(path)

    assert error.value.corrective_action == "verify the configured host paths can be resolved and retry"


def test_missing_file_is_reported_as_a_trafficlab_error(tmp_path: Path) -> None:
    """Leaking FileNotFoundError would bypass the package's corrective error boundary."""
    with pytest.raises(TrafficlabError, match="could not read experiment configuration") as error:
        load_experiment(tmp_path / "missing.toml")

    assert error.value.corrective_action


def test_malformed_toml_is_reported_as_a_trafficlab_error(tmp_path: Path) -> None:
    """Leaking TOMLDecodeError would bypass the package's corrective error boundary."""
    path = tmp_path / "experiment.toml"
    path.write_text("[run\n", encoding="utf-8")

    with pytest.raises(TrafficlabError, match="invalid TOML") as error:
        load_experiment(path)

    assert error.value.corrective_action


def test_validation_error_reports_the_dotted_configuration_path(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Dropping the Pydantic location would leave an operator unable to find the invalid value."""
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["target"])["argv"] = []
    path = tmp_path / "experiment.toml"
    _write_config(path, data)

    with pytest.raises(TrafficlabError, match=r"target\.argv") as error:
        load_experiment(path)

    assert error.value.corrective_action


def test_other_validation_errors_retain_the_generic_configuration_context(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["capture"])["total_timeout_seconds"] = 0.0
    path = tmp_path / "experiment.toml"
    _write_config(path, data)

    with pytest.raises(TrafficlabError, match="invalid experiment configuration") as error:
        load_experiment(path)

    assert error.value.corrective_action == "correct the reported configuration values and retry"


def test_effective_config_bytes_are_deterministic_and_round_trip(
    valid_config_data: dict[str, object],
) -> None:
    """An unstable or lossy snapshot would make an experiment irreproducible."""
    config = ExperimentConfig.model_validate(valid_config_data)

    first = render_effective_config(config)
    second = render_effective_config(config)
    reparsed = ExperimentConfig.model_validate(tomllib.loads(first.decode("utf-8")))

    assert first == second
    assert reparsed == config


def test_effective_config_snapshot_contains_exact_operator_defaults(
    valid_config_data: dict[str, object],
) -> None:
    """Omitting resolved operator defaults would make reproduction depend on future code defaults."""
    data = copy.deepcopy(valid_config_data)
    models = cast(dict[str, object], data["models"])
    for family in ("poisson_empirical", "markov_renewal", "mmpp"):
        settings = cast(dict[str, object], models[family])
        for field in ("crossover_probability", "mutation_probability", "mutation_scale"):
            del settings[field]
    config = ExperimentConfig.model_validate(data)

    snapshot = render_effective_config(config)
    reparsed = ExperimentConfig.model_validate(tomllib.loads(snapshot.decode("utf-8")))

    assert reparsed.models.poisson_empirical is not None
    assert reparsed.models.markov_renewal is not None
    assert reparsed.models.mmpp is not None
    assert reparsed.models.poisson_empirical.operator_values == (0.9, 1.0, 0.1)
    assert reparsed.models.markov_renewal.operator_values == (0.9, 0.2, 0.1)
    assert reparsed.models.mmpp.operator_values == (0.9, 0.25, 0.1)


def test_renderer_rejects_a_serializer_result_that_changes_the_model(
    valid_config_data: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publishing serializer-corrupted values would invalidate the effective snapshot contract."""
    config = ExperimentConfig.model_validate(valid_config_data)
    real_dumps = tomli_w.dumps

    def corrupt_final_seed(data: dict[str, object]) -> str:
        corrupted = copy.deepcopy(data)
        cast(dict[str, object], corrupted["run"])["final_seed"] = 0
        return real_dumps(corrupted)

    monkeypatch.setattr(config_io.tomli_w, "dumps", corrupt_final_seed)

    with pytest.raises(TrafficlabError, match="did not round-trip") as error:
        render_effective_config(config)

    assert error.value.corrective_action == "report the deterministic configuration renderer defect"
