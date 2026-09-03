"""Locked production PCG64 construction and scientific-schema behavior."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scientific_schema import SCIENTIFIC_ARTIFACT_SCHEMA_VERSION
from trafficlab.fitting.genetic.checkpoint import decode_rng_state, encode_rng_state
from trafficlab.generation.models import common
from trafficlab.generation.models.fitted_model import load_best_model


def test_named_pcg64_generator_has_locked_scalar_array_and_endpoint_draws() -> None:
    """A different constructor or primitive sequence would change every seeded artifact."""
    factory = cast(Callable[[int], np.random.Generator] | None, getattr(common, "make_rng", None))
    assert factory is not None

    rng = factory(20260819)
    assert type(rng) is np.random.Generator
    assert type(rng.bit_generator) is np.random.PCG64
    assert rng.random() == 0.6523163084985453
    assert int(rng.integers(0, 7, endpoint=False)) == 2
    assert int(rng.integers(2, 8, endpoint=True)) == 4
    assert int(rng.choice(5)) == 0
    assert tuple(
        str(item) for item in rng.permutation(np.array(("markov_renewal", "mmpp", "poisson_empirical"), dtype=np.str_))
    ) == ("poisson_empirical", "markov_renewal", "mmpp")
    assert rng.exponential(scale=0.5) == 0.21835907630466694
    assert rng.normal(loc=0.0, scale=0.25) == -0.18705430280070065


def test_pcg64_state_is_exact_json_and_restore_replays_all_next_primitives() -> None:
    """Dropping a PCG64 cache field or restoring another engine would fork resumed search."""
    factory = cast(Callable[[int], np.random.Generator] | None, getattr(common, "make_rng", None))
    assert factory is not None
    rng = factory(73)
    assert (rng.random(), int(rng.integers(0, 9, endpoint=False)), rng.normal(loc=0.0, scale=0.1)) == (
        0.49263618928819286,
        7,
        -0.03816103055215293,
    )

    encoded = encode_rng_state(rng)
    assert encoded.model_dump(mode="json") == {
        "bit_generator": "PCG64",
        "state": {
            "state": 65616276318730853556117230867978790572,
            "inc": 173597405176857737877318840511332489317,
        },
        "has_uint32": 1,
        "uinteger": 1302953653,
    }
    assert json.loads(json.dumps(encoded.model_dump(mode="json"))) == encoded.model_dump(mode="json")

    restored = decode_rng_state(encoded)
    assert type(restored) is np.random.Generator
    assert type(restored.bit_generator) is np.random.PCG64
    assert (restored.random(), int(restored.integers(0, 9, endpoint=False)), restored.normal(0.0, 0.1)) == (
        rng.random(),
        int(rng.integers(0, 9, endpoint=False)),
        rng.normal(0.0, 0.1),
    )


@pytest.mark.parametrize(
    "legacy",
    [
        Path("examples/validation_study/evidence/2026-08-20-stack-adoption-r6/training/bursty/r1/best_model.json"),
        Path("examples/validation_study/evidence/2026-08-18-research-fitness-r21/training/bursty/r1/best_model.json"),
    ],
)
def test_schema_v5_is_current_and_older_best_models_require_refit(legacy: Path) -> None:
    """Custom-codec fitted artifacts must not silently enter the Scapy workflow."""
    assert SCIENTIFIC_ARTIFACT_SCHEMA_VERSION == 5
    with pytest.raises(TrafficlabError, match="best model schema is incompatible") as caught:
        load_best_model(legacy.read_bytes(), source=legacy)
    assert caught.value.corrective_action == "refit under the current schema"
