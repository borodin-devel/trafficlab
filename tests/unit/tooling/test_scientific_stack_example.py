"""Frozen scientific-stack full-workflow evidence contract."""

import tomllib
from pathlib import Path

from trafficlab.generation.models.common import make_rng

_ROOT = Path(__file__).parents[3]
_EXAMPLE = _ROOT / "examples" / "scientific_stack" / "experiment.toml"


def test_scientific_stack_example_preserves_the_small_schema_four_experiment() -> None:
    """Historical real-run evidence must retain its original, non-current configuration bytes."""
    config = tomllib.loads(_EXAMPLE.read_text(encoding="utf-8"))
    run = config["run"]
    genetic = config["genetic"]
    models = config["models"]
    similarity = config["similarity"]

    assert models["enabled"] == ["poisson_empirical", "markov_renewal", "mmpp"]
    assert genetic["population_size"] == 6
    assert genetic["generation_count"] == 1
    assert genetic["trial_seeds"] == [17]
    assert run["master_seed"] == 20260819
    assert run["final_seed"] == 97
    assert type(make_rng(run["master_seed"]).bit_generator).__name__ == "PCG64"
    assert similarity["method_weights"] == {
        "frame_size_ks": 0.25,
        "iat_ks": 0.25,
        "autocorrelation": 0.25,
        "multiscale_rate": 0.25,
    }
