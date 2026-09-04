"""Lightweight public capture-stage result records."""

from dataclasses import dataclass
from pathlib import Path
from typing import cast


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """Published result of one successful reference capture."""

    run_directory: Path
    reference_path: Path
    packet_count: int
    target_status: int
    reused: bool = False

    def __post_init__(self) -> None:
        run_directory = cast(object, self.run_directory)
        reference_path = cast(object, self.reference_path)
        if not isinstance(run_directory, Path) or not run_directory.is_absolute():
            raise TypeError("run_directory must be an absolute Path")
        if not isinstance(reference_path, Path) or not reference_path.is_absolute():
            raise TypeError("reference_path must be an absolute Path")
        if type(self.packet_count) is not int:
            raise TypeError("packet_count must be a positive integer")
        if self.packet_count <= 0:
            raise ValueError("packet_count must be a positive integer")
        if type(self.target_status) is not int:
            raise TypeError("target_status must be an integer")
        if type(self.reused) is not bool:
            raise TypeError("reused must be a boolean")
