from pathlib import Path
from typing import cast

from tests.support.config import valid_config_data
from trafficlab.config import ExperimentConfig


def test_valid_config_builder_returns_independent_valid_mappings(tmp_path: Path) -> None:
    first = valid_config_data(tmp_path / "first")
    second = valid_config_data(tmp_path / "second")

    assert cast(dict[str, object], first["run"])["directory"] == str(tmp_path / "first" / "run")
    assert cast(dict[str, object], second["run"])["directory"] == str(tmp_path / "second" / "run")
    ExperimentConfig.model_validate(first)
    ExperimentConfig.model_validate(second)

    first_models = cast(dict[str, object], first["models"])
    first_enabled = cast(list[str], first_models["enabled"])
    first_enabled.remove("mmpp")

    assert cast(dict[str, object], second["models"])["enabled"] == [
        "poisson_empirical",
        "markov_renewal",
        "mmpp",
    ]
