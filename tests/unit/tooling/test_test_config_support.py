from pathlib import Path
from typing import cast

from tests.support.config import valid_config_data
from trafficlab.common.config import ExperimentConfig


def test_valid_config_builder_returns_independent_valid_mappings(tmp_path: Path) -> None:
    first = valid_config_data(tmp_path / "first")
    second = valid_config_data(tmp_path / "second")

    assert cast(dict[str, object], first["run"])["directory"] == str(tmp_path / "first" / "run")
    assert cast(dict[str, object], second["run"])["directory"] == str(tmp_path / "second" / "run")
    ExperimentConfig.model_validate(first)
    ExperimentConfig.model_validate(second)

    first_models = cast(dict[str, object], first["models"])
    first_enabled = cast(list[str], first_models["enabled"])
    first_enabled.remove("packet_hmm")

    assert cast(dict[str, object], second["models"])["enabled"] == [
        "poisson_empirical",
        "markov_renewal",
        "mmpp",
        "nhpp",
        "acd",
        "markov_packet_train",
        "packet_hmm",
    ]


def test_valid_config_builder_has_complete_release_family_and_postfit_settings(tmp_path: Path) -> None:
    """Shared test data must exercise the complete schema-five configuration surface."""
    config = ExperimentConfig.model_validate(valid_config_data(tmp_path))

    assert config.models.enabled == (
        "poisson_empirical",
        "markov_renewal",
        "mmpp",
        "nhpp",
        "acd",
        "markov_packet_train",
        "packet_hmm",
    )
    assert config.genetic.population_size >= config.genetic.elite_count + len(config.models.enabled)
    assert config.models.nhpp is not None
    assert config.models.acd is not None
    assert config.models.markov_packet_train is not None
    assert config.models.packet_hmm is not None
    assert config.similarity.mmd_feature_count <= 65_536
    assert config.similarity.postfit.c2st.maximum_window_count <= 65_536
