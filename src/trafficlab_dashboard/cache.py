from __future__ import annotations

from trafficlab_dashboard.aspects.base import CalculationSettings, PlotData
from trafficlab_dashboard.run_data import ArtifactIdentities, DashboardRun

type CacheKey = tuple[ArtifactIdentities, str, CalculationSettings]


class AspectCache:
    def __init__(self) -> None:
        self._entries: dict[CacheKey, PlotData] = {}

    def __len__(self) -> int:
        return len(self._entries)

    @staticmethod
    def key(run: DashboardRun, aspect_id: str, settings: CalculationSettings) -> CacheKey:
        if type(aspect_id) is not str or not aspect_id:
            raise TypeError("aspect_id must be a non-empty string")
        if type(settings) is not CalculationSettings:
            raise TypeError("settings must be a CalculationSettings value")
        return run.identities, aspect_id, settings

    def get(self, key: CacheKey) -> PlotData | None:
        return self._entries.get(key)

    def keys(self) -> tuple[CacheKey, ...]:
        return tuple(self._entries)

    def put(self, key: CacheKey, value: PlotData) -> None:
        self._entries[key] = value

    def clear(self) -> None:
        self._entries.clear()
