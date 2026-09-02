"""Deterministic packet-HMM fixtures and scripted random stream."""

from __future__ import annotations

from dataclasses import dataclass, field

from trafficlab.common.trace import Direction
from trafficlab.generation.models.common import MarkCount, MarkDistribution
from trafficlab.generation.models.packet_hmm.model import (
    BaumWelchDiagnostics,
    PacketCategory,
    PacketHmmModel,
    PacketSample,
)


def two_state_model() -> PacketHmmModel:
    """Return a hand-built canonical two-state model with distinguishable raw members."""
    return PacketHmmModel(
        additive_smoothing=0.001,
        convergence_tolerance=1e-8,
        diagnostics=BaumWelchDiagnostics(
            converged=True,
            iterations=2,
            log_likelihoods=(-4.0, -3.5, -3.5),
        ),
        emission_rows=((0.8, 0.2), (0.1, 0.9)),
        iat_quantiles=(1.0 / 3.0, 2.0 / 3.0),
        iat_thresholds=(5.0 / 3.0, 7.0 / 3.0),
        initial_marks=MarkDistribution((MarkCount(Direction.OUTBOUND, 60, 1),)),
        initial_probabilities=(0.75, 0.25),
        initialization="fixed_cyclic_v1",
        maximum_iterations=100,
        reservoirs=(
            (PacketSample(iat=1.0, frame_length=70),),
            (PacketSample(iat=3.0, frame_length=130),),
        ),
        size_quantiles=(1.0 / 3.0, 2.0 / 3.0),
        size_thresholds=(90.0, 110.0),
        state_count=2,
        transition_rows=((0.75, 0.25), (0.25, 0.75)),
        vocabulary=(
            PacketCategory(iat_bin=1, direction=Direction.INBOUND, size_bin=0),
            PacketCategory(iat_bin=3, direction=Direction.OUTBOUND, size_bin=2),
        ),
    )


@dataclass
class ScriptedHmmRng:
    """Record exact scalar calls while returning predeclared endpoint-sensitive draws."""

    randoms: tuple[float, ...]
    choices: tuple[int, ...]
    calls: list[tuple[str, int | None]] = field(default_factory=lambda: [])
    _random_index: int = 0
    _choice_index: int = 0

    def random(self) -> float:
        self.calls.append(("random", None))
        value = self.randoms[self._random_index]
        self._random_index += 1
        return value

    def choice(self, a: int) -> int:
        self.calls.append(("choice", a))
        value = self.choices[self._choice_index]
        self._choice_index += 1
        return value
