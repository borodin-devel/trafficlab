"""Distributable scientific-stack full-workflow example contract."""

from pathlib import Path

from trafficlab.common.config_io import load_experiment
from trafficlab.generation.models.common import make_rng

_ROOT = Path(__file__).parents[2]
_EXAMPLE = _ROOT / "examples" / "scientific_stack" / "experiment.toml"


def test_scientific_stack_example_is_small_locked_and_exercises_every_family() -> None:
    """A partial or nondeterministic example would not demonstrate the adopted production stack."""
    config = load_experiment(_EXAMPLE)

    assert config.models.enabled == ("poisson_empirical", "markov_renewal", "mmpp")
    assert config.genetic.population_size == 6
    assert config.genetic.generation_count == 1
    assert config.genetic.trial_seeds == (17,)
    assert config.run.master_seed == 20260819
    assert config.run.final_seed == 97
    assert type(make_rng(config.run.master_seed).bit_generator).__name__ == "PCG64"
    assert config.target.image.startswith("curlimages/curl@sha256:")
    assert config.capture.network_probe_url.startswith("https://")
    assert config.generation.trial.max_packets <= 2_000
    assert config.generation.final.max_packets <= 5_000
    assert config.similarity.method_weights.model_dump() == {
        "frame_size_ks": 0.25,
        "iat_ks": 0.25,
        "autocorrelation": 0.25,
        "multiscale_rate": 0.25,
    }
