"""Offline behavior tests for the Internet smoke-test target client."""

from __future__ import annotations

import importlib.util
import urllib.request
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, TracebackType
from typing import cast

import pytest

_CLIENT_PATH = Path(__file__).parents[1] / "docker" / "images" / "client" / "client.py"
_USER_AGENT = "Trafficlab/0.1.0 (+https://github.com/borodin-devel/trafficlab)"


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status
        self.read_sizes: list[int] = []

    def __enter__(self) -> _Response:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exception_type, exception, traceback
        return False

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return b"body"


def _https_client() -> Callable[[str], None]:
    spec = importlib.util.spec_from_file_location("trafficlab_internet_client", _CLIENT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(Callable[[str], None], vars(module)["_https"])


@pytest.mark.parametrize(
    ("status", "error"),
    (
        (206, None),
        (403, "HTTPS endpoint returned status 403"),
    ),
)
def test_https_client_uses_the_descriptive_project_user_agent_and_checks_status(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    error: str | None,
) -> None:
    request_values: list[object] = []
    response = _Response(status)

    def urlopen(request: object, *, timeout: float) -> _Response:
        request_values.append(request)
        assert timeout == 15.0
        return response

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    if error is None:
        _https_client()("https://example.test/object.bin")
    else:
        with pytest.raises(RuntimeError, match=error):
            _https_client()("https://example.test/object.bin")

    assert len(request_values) == 1
    request = request_values[0]
    assert isinstance(request, urllib.request.Request)
    assert request.full_url == "https://example.test/object.bin"
    assert request.get_header("User-agent") == _USER_AGENT
    assert response.read_sizes == ([4096] if error is None else [])
