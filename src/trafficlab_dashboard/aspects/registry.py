from __future__ import annotations

from dataclasses import dataclass

from trafficlab_dashboard.aspects.base import Aspect, CalculationSettings, PlotData
from trafficlab_dashboard.aspects.distributions import (
    FrameSizeEcdfAspect,
    FrameSizeHistogramAspect,
    IatEcdfAspect,
    IatHistogramAspect,
    ThroughputEcdfAspect,
)
from trafficlab_dashboard.aspects.time_domain import (
    CumulativeBytesAspect,
    CumulativePacketsAspect,
    FrameSizeTimelineAspect,
    IatTimelineAspect,
    PacketRateAspect,
    ThroughputAspect,
)
from trafficlab_dashboard.run_data import DashboardRun


def _not_implemented(identifier: str) -> PlotData:
    raise NotImplementedError(f"aspect {identifier} is not implemented yet")


@dataclass(frozen=True, slots=True)
class _RegisteredAspect:
    identifier: str
    label: str
    category: str
    trace_controls: bool = True

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> PlotData:
        del run, settings
        return _not_implemented(self.identifier)


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
    _RegisteredAspect("directional_throughput", "Uplink/downlink throughput", "Direction"),
    _RegisteredAspect("directional_packet_rate", "Uplink/downlink packet rate", "Direction"),
    _RegisteredAspect("direction_balance", "Direction balance", "Direction"),
    _RegisteredAspect("frame_size_acf", "Frame-size autocorrelation", "Dependence"),
    _RegisteredAspect("iat_acf", "IAT autocorrelation", "Dependence"),
    _RegisteredAspect("frame_size_iat_hexbin", "Frame size versus IAT", "Dependence"),
)

_ASPECTS_BY_ID = {aspect.identifier: aspect for aspect in ASPECTS}


def aspect_by_id(identifier: str) -> Aspect:
    try:
        return _ASPECTS_BY_ID[identifier]
    except KeyError as error:
        raise KeyError(identifier) from error
