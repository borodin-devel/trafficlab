"""Offline behavior tests for the Internet smoke-test target client."""

from __future__ import annotations

import importlib.util
import json
import tomllib
import urllib.request
from argparse import ArgumentParser
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, TracebackType
from typing import cast

import pytest

from tests.fixtures.paths import DOCKER_FIXTURE_ROOT
from trafficlab import USER_AGENT
from trafficlab.capture.topology import ComposePaths
from trafficlab.common.config import ExperimentConfig
from trafficlab.preflight.probe import render_probe_compose

_CLIENT_PATH = DOCKER_FIXTURE_ROOT / "images" / "client" / "client.py"
_REPOSITORY_ROOT = Path(__file__).parents[3]


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


def _metadata_user_agent() -> str:
    with (_REPOSITORY_ROOT / "pyproject.toml").open("rb") as stream:
        document = cast(dict[str, object], tomllib.load(stream))
    project = cast(dict[str, object], document["project"])
    urls = cast(dict[str, object], project["urls"])
    return f"{cast(str, project['name'])}/{cast(str, project['version'])} (+{cast(str, urls['Repository'])})"


def _client_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("trafficlab_internet_client", _CLIENT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _https_client() -> Callable[[str, str], None]:
    module = _client_module()
    return cast(Callable[[str, str], None], vars(module)["_https"])


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
    user_agent = _metadata_user_agent()
    assert USER_AGENT == user_agent

    if error is None:
        _https_client()("https://example.test/object.bin", user_agent)
    else:
        with pytest.raises(RuntimeError, match=error):
            _https_client()("https://example.test/object.bin", user_agent)

    assert len(request_values) == 1
    request = request_values[0]
    assert isinstance(request, urllib.request.Request)
    assert request.full_url == "https://example.test/object.bin"
    assert request.get_header("User-agent") == user_agent
    assert response.read_sizes == ([4096] if error is None else [])


def test_https_command_requires_the_metadata_derived_user_agent() -> None:
    parser_factory = cast(Callable[[], ArgumentParser], vars(_client_module())["_parser"])
    parser = parser_factory()
    user_agent = _metadata_user_agent()

    with pytest.raises(SystemExit) as error:
        parser.parse_args(["https", "https://example.test/object.bin"])

    assert error.value.code == 2
    arguments = parser.parse_args(["https", "--user-agent", user_agent, "https://example.test/object.bin"])
    assert arguments.user_agent == user_agent
    assert USER_AGENT == user_agent


def test_probe_uses_the_project_metadata_user_agent(valid_config_data: dict[str, object], tmp_path: Path) -> None:
    assert USER_AGENT == _metadata_user_agent()
    config = ExperimentConfig.model_validate(valid_config_data)
    document = cast(
        dict[str, object],
        json.loads(
            render_probe_compose(
                config,
                ComposePaths("trafficlab-user-agent", tmp_path / "probe-output"),
                capture_image="sha256:" + "a" * 64,
            )
        ),
    )
    services = cast(dict[str, object], document["services"])
    target = cast(dict[str, object], services["target"])
    entrypoint = cast(list[str], target["entrypoint"])
    user_agent_position = entrypoint.index("--user-agent")
    assert entrypoint[user_agent_position + 1] == _metadata_user_agent()
