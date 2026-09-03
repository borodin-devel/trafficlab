import copy
import math
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from trafficlab.common.config import (
    AcdConfig,
    ExperimentConfig,
    IntegerBounds,
    MarkovPacketTrainConfig,
    NhppConfig,
    PacketHmmConfig,
)


def test_complete_mapping_creates_immutable_experiment_config(valid_config_data: dict[str, object]) -> None:
    config = ExperimentConfig.model_validate(valid_config_data)

    assert config.run.directory.name == "run"
    assert config.target.argv == ("https://example.invalid/data",)


def test_models_are_frozen(valid_config_data: dict[str, object]) -> None:
    config = ExperimentConfig.model_validate(valid_config_data)

    with pytest.raises(ValidationError, match="frozen_instance"):
        config.run.master_seed = 9


def test_required_structural_bound_types_are_strict() -> None:
    """Future family structural genes accept only exact IntegerBounds values."""
    assert PacketHmmConfig(state_count=IntegerBounds(lower=2, upper=4)).state_count.upper == 4
    assert MarkovPacketTrainConfig(length_cap=IntegerBounds(lower=3, upper=8)).length_cap.lower == 3
    assert NhppConfig(bin_count=IntegerBounds(lower=2, upper=16)).bin_count.upper == 16
    with pytest.raises(ValidationError):
        AcdConfig.model_validate({"order": {"lower": 0, "upper": 3}})


def test_unknown_root_key_is_rejected(valid_config_data: dict[str, object]) -> None:
    data = copy.deepcopy(valid_config_data)
    data["typo"] = 1

    with pytest.raises(ValidationError, match="extra_forbidden"):
        ExperimentConfig.model_validate(data)


def test_unknown_nested_key_is_rejected(valid_config_data: dict[str, object]) -> None:
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["capture"])["typo"] = 1

    with pytest.raises(ValidationError, match="extra_forbidden"):
        ExperimentConfig.model_validate(data)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("target", "image", 1),
        ("run", "master_seed", "12345"),
    ],
)
def test_strict_scalars_are_not_coerced(
    valid_config_data: dict[str, object], section: str, field: str, value: object
) -> None:
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data[section])[field] = value

    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(data)


def test_nonfinite_float_is_rejected(valid_config_data: dict[str, object]) -> None:
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["capture"])["readiness_timeout_seconds"] = math.inf

    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(data)


def test_genetic_tolerance_defaults_to_exact_zero(valid_config_data: dict[str, object]) -> None:
    config = ExperimentConfig.model_validate(valid_config_data)

    assert config.genetic.early_stopping_tolerance == 0.0
    assert type(config.genetic.early_stopping_tolerance) is float


@pytest.mark.parametrize("value", [True, 1, -0.0001, 1.0001, math.inf, math.nan])
def test_genetic_tolerance_requires_a_finite_exact_float(valid_config_data: dict[str, object], value: object) -> None:
    genetic = dict(cast(dict[str, object], valid_config_data["genetic"]))
    genetic["early_stopping_tolerance"] = value

    with pytest.raises(ValidationError, match="early_stopping_tolerance"):
        ExperimentConfig.model_validate({**valid_config_data, "genetic": genetic})


def test_empty_argv_is_rejected(valid_config_data: dict[str, object]) -> None:
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["target"])["argv"] = []

    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(data)


@pytest.mark.parametrize("path_field", ["working_directory", "mount_target"])
def test_relative_container_path_is_rejected(
    valid_config_data: dict[str, object], path_field: str, tmp_path: Path
) -> None:
    data = copy.deepcopy(valid_config_data)
    target = cast(dict[str, object], data["target"])
    if path_field == "working_directory":
        target["working_directory"] = "work"
    else:
        target["mounts"] = [{"source": str(tmp_path), "target": "data", "read_only": True}]

    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(data)


def test_environment_value_must_be_string(valid_config_data: dict[str, object]) -> None:
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["target"])["environment"] = {"LANG": 1}

    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(data)
