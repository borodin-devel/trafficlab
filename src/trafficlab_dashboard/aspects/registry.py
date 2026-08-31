from __future__ import annotations

from trafficlab_dashboard.aspects.base import Aspect
from trafficlab_dashboard.aspects.dependence import (
    FrameSizeAutocorrelationAspect,
    FrameSizeIatHexbinAspect,
    IatAutocorrelationAspect,
)
from trafficlab_dashboard.aspects.direction import (
    DirectionalPacketRateAspect,
    DirectionalThroughputAspect,
    DirectionBalanceAspect,
)
from trafficlab_dashboard.aspects.distributions import (
    FrameSizeEcdfAspect,
    FrameSizeHistogramAspect,
    IatEcdfAspect,
    IatHistogramAspect,
    ThroughputEcdfAspect,
)
from trafficlab_dashboard.aspects.run_level import (
    GaFitnessHistoryAspect,
    MultiscaleDiscrepancyAspect,
    SimilarityScoresAspect,
)
from trafficlab_dashboard.aspects.time_domain import (
    CumulativeBytesAspect,
    CumulativePacketsAspect,
    FrameSizeTimelineAspect,
    IatTimelineAspect,
    PacketRateAspect,
    ThroughputAspect,
)

ASPECTS: tuple[Aspect, ...] = (
    ThroughputAspect(),
    PacketRateAspect(),
    CumulativeBytesAspect(),
    CumulativePacketsAspect(),
    FrameSizeTimelineAspect(),
    IatTimelineAspect(),
    FrameSizeEcdfAspect(),
    IatEcdfAspect(),
    FrameSizeHistogramAspect(),
    IatHistogramAspect(),
    ThroughputEcdfAspect(),
    DirectionalThroughputAspect(),
    DirectionalPacketRateAspect(),
    DirectionBalanceAspect(),
    FrameSizeAutocorrelationAspect(),
    IatAutocorrelationAspect(),
    FrameSizeIatHexbinAspect(),
    SimilarityScoresAspect(),
    MultiscaleDiscrepancyAspect(),
    GaFitnessHistoryAspect(),
)

_ASPECTS_BY_ID = {aspect.identifier: aspect for aspect in ASPECTS}


def aspect_by_id(identifier: str) -> Aspect:
    try:
        return _ASPECTS_BY_ID[identifier]
    except KeyError as error:
        raise KeyError(identifier) from error
