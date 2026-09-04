from pathlib import Path
from typing import Any, cast

import pytest

from trafficlab.capture.types import CaptureResult


def test_capture_result_accepts_exact_absolute_public_values() -> None:
    result = CaptureResult(Path("/run"), Path("/run/reference.pcapng"), 1, 0)

    assert result.reused is False


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        (("run", Path("/reference"), 1, 0), "run_directory"),
        ((Path("run"), Path("/reference"), 1, 0), "run_directory"),
        ((Path("/run"), "reference", 1, 0), "reference_path"),
        ((Path("/run"), Path("reference"), 1, 0), "reference_path"),
        ((Path("/run"), Path("/reference"), True, 0), "packet_count"),
        ((Path("/run"), Path("/reference"), 0, 0), "packet_count"),
        ((Path("/run"), Path("/reference"), -1, 0), "packet_count"),
        ((Path("/run"), Path("/reference"), 1, True), "target_status"),
    ],
)
def test_capture_result_strictly_rejects_invalid_public_values(arguments: tuple[object, ...], error: str) -> None:
    """Accepting coerced or relative result fields would break the public capture contract."""
    with pytest.raises((TypeError, ValueError), match=error):
        CaptureResult(*cast(tuple[Any, Any, Any, Any], arguments))


def test_capture_result_rejects_a_non_boolean_reuse_flag() -> None:
    """Truthiness coercion would make capture ownership ambiguous to the coordinator."""
    with pytest.raises(TypeError, match="reused"):
        CaptureResult(Path("/run"), Path("/run/reference.pcapng"), 1, 0, cast(Any, 1))
