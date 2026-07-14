"""Минимальное представление Directed Acyclic Graph (ацикличного графа) стадий."""

from dataclasses import dataclass, field


@dataclass
class StageGraph:
    stages: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def add(self, stage: str, dependencies: tuple[str, ...] = ()) -> None:
        self.stages[stage] = dependencies

    def dependencies_of(self, stage: str) -> tuple[str, ...]:
        return self.stages[stage]
