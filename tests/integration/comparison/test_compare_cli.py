import builtins
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

import trafficlab
import trafficlab.capture as capture_package
from tests.fixtures.paths import PIPELINE_FIXTURE_ROOT
from trafficlab.artifacts.run_directory import create_run_directory
from trafficlab.cli import main
from trafficlab.common.config_io import load_experiment, render_effective_config
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import read_pcapng
from trafficlab.common.trace import Direction, TraceEvent, align_generated, load_capture_metadata, normalize_reference
from trafficlab.comparison.codec import load_comparison_result
from trafficlab.comparison.metrics import compare_traces
from trafficlab.comparison.stage import compare_experiment

pytestmark = pytest.mark.integration

_REPOSITORY = Path(__file__).parents[3]
_EXAMPLE_CONFIG = _REPOSITORY / "examples" / "configs" / "minimal.toml"
_EXAMPLE_DATA = PIPELINE_FIXTURE_ROOT

_REFERENCE_EVENTS = (
    TraceEvent(10.0, Direction.OUTBOUND, 60),
    TraceEvent(11.0, Direction.INBOUND, 100),
    TraceEvent(13.0, Direction.OUTBOUND, 140),
    TraceEvent(16.0, Direction.INBOUND, 100),
    TraceEvent(20.0, Direction.OUTBOUND, 60),
)
_GENERATED_EVENTS = (
    TraceEvent(0.0, Direction.OUTBOUND, 60),
    TraceEvent(3.522005, Direction.INBOUND, 100),
    TraceEvent(5.802501, Direction.INBOUND, 100),
)
_NORMALIZED_REFERENCE = (
    TraceEvent(0.0, Direction.OUTBOUND, 60),
    TraceEvent(1.0, Direction.INBOUND, 100),
    TraceEvent(3.0, Direction.OUTBOUND, 140),
    TraceEvent(6.0, Direction.INBOUND, 100),
    TraceEvent(10.0, Direction.OUTBOUND, 60),
)
_ALIGNED_GENERATED = (
    TraceEvent(0.0, Direction.OUTBOUND, 60),
    TraceEvent(3.522005, Direction.INBOUND, 100),
    TraceEvent(5.802501, Direction.INBOUND, 100),
)


def _prepare_existing_run(tmp_path: Path, name: str, *, include_similarity: bool = False) -> tuple[Path, Path]:
    base = load_experiment(_EXAMPLE_CONFIG)
    run_directory = tmp_path / name
    config = base.model_copy(update={"run": base.run.model_copy(update={"directory": run_directory})})
    caller_path = tmp_path / f"{name}.toml"
    caller_path.write_bytes(render_effective_config(config))
    create_run_directory(config)
    artifact_names = [
        "capture.json",
        "reference.pcapng",
        "best_model.json",
        "generated.pcapng",
    ]
    if include_similarity:
        artifact_names.append("similarity.json")
    for artifact_name in artifact_names:
        (run_directory / artifact_name).write_bytes((_EXAMPLE_DATA / artifact_name).read_bytes())
    return caller_path, run_directory


def _is_docker_adapter_module(name: str) -> bool:
    if name == "docker" or name.startswith("docker."):
        return True
    return name == "trafficlab.capture.docker_cli" or name.startswith("trafficlab.capture.docker_cli.")


def _is_docker_adapter_import(name: str, fromlist: tuple[str, ...] | None) -> bool:
    return _is_docker_adapter_module(name) or (name == "trafficlab.capture" and "docker_cli" in (fromlist or ()))


def _is_run_or_capture_import(name: str, fromlist: tuple[str, ...] | None) -> bool:
    return (
        name in {"trafficlab.pipeline.stage", "trafficlab.capture.stage"}
        or (name == "trafficlab.pipeline" and "stage" in (fromlist or ()))
        or (name == "trafficlab.capture" and "stage" in (fromlist or ()))
    )


@pytest.mark.parametrize(
    ("name", "fromlist", "expected"),
    [
        ("trafficlab.pipeline.stage", None, True),
        ("trafficlab.capture.stage", None, True),
        ("trafficlab.pipeline", ("stage",), True),
        ("trafficlab.capture", ("stage",), True),
        ("trafficlab.runner", None, False),
        ("trafficlab.capture.validation", None, False),
        ("trafficlab", ("runtime",), False),
        ("trafficlab", None, False),
    ],
)
def test_run_or_capture_import_guard_classifies_direct_fromlist_and_close_names(
    name: str,
    fromlist: tuple[str, ...] | None,
    expected: bool,
) -> None:
    """A guard that misses real imports or blocks close names cannot prove lazy CLI isolation."""
    assert _is_run_or_capture_import(name, fromlist) is expected


def _clear_docker_adapter_import_state(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(sys.modules):
        if name == "trafficlab.capture.docker_cli" or name.startswith("trafficlab.capture.docker_cli."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.delattr(capture_package, "docker_cli", raising=False)


def test_docker_adapter_guard_allows_pure_renderer_but_detects_preloaded_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Classifying the pure renderer as Docker code would make permanent import isolation order-dependent."""
    import trafficlab.capture.compose

    assert not _is_docker_adapter_module(trafficlab.capture.compose.__name__)

    adapter = ModuleType("trafficlab.capture.docker_cli")
    monkeypatch.setitem(sys.modules, "trafficlab.capture.docker_cli", adapter)
    monkeypatch.setitem(
        sys.modules, "trafficlab.capture.docker_cli.child", ModuleType("trafficlab.capture.docker_cli.child")
    )
    monkeypatch.setattr(capture_package, "docker_cli", adapter, raising=False)

    assert "trafficlab.capture.docker_cli" in {name for name in sys.modules if _is_docker_adapter_module(name)}
    assert capture_package.docker_cli is adapter  # type: ignore[attr-defined]
    assert _is_docker_adapter_import("trafficlab.capture", ("docker_cli",))
    assert not _is_docker_adapter_import("trafficlab.capture", ("compose",))

    _clear_docker_adapter_import_state(monkeypatch)

    assert {name for name in sys.modules if _is_docker_adapter_module(name)} == set()
    assert not hasattr(capture_package, "docker_cli")

    real_import = builtins.__import__

    def reject_docker_import(
        name: str,
        globals: Any = None,
        locals: Any = None,
        fromlist: tuple[str, ...] | None = None,
        level: int = 0,
    ) -> Any:
        if _is_docker_adapter_import(name, fromlist):
            raise AssertionError(f"guard intercepted Docker adapter import {name}")
        return real_import(name, globals, locals, fromlist or (), level)

    monkeypatch.setattr(builtins, "__import__", reject_docker_import)

    exec("import trafficlab", {})
    with pytest.raises(AssertionError, match="guard intercepted"):
        exec("from trafficlab.capture import docker_cli", {})


def _run_installed_compare(experiment_path: Path, *, working_directory: Path) -> subprocess.CompletedProcess[str]:
    installed_script = Path(sys.executable).with_name("trafficlab")
    environment = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT") if name in os.environ}
    return subprocess.run(
        [str(installed_script), "compare", str(experiment_path)],
        check=False,
        capture_output=True,
        text=True,
        cwd=working_directory,
        env=environment,
    )


def _isolated_installed_working_directory(root: Path) -> Path:
    working_directory = root / "installed-entry-cwd"
    source_shadow = working_directory / "src" / "trafficlab"
    source_shadow.mkdir(parents=True)
    (source_shadow / "__init__.py").write_text(
        'raise RuntimeError("installed entry point imported a working-directory source shadow")\n',
        encoding="utf-8",
    )
    return working_directory


def test_checked_in_fixture_round_trip_preserves_canonical_values_and_one_shared_window() -> None:
    """Changing fixture bytes, direction classification, alignment, or one metric's W must break this boundary."""
    metadata = load_capture_metadata(_EXAMPLE_DATA / "capture.json")
    reference_events = read_pcapng(_EXAMPLE_DATA / "reference.pcapng", metadata)
    generated_events = read_pcapng(_EXAMPLE_DATA / "generated.pcapng", metadata)

    assert metadata.target_mac == "02:42:ac:11:00:02"
    assert reference_events == _REFERENCE_EVENTS
    assert generated_events == _GENERATED_EVENTS
    assert {event.direction for event in reference_events} == {Direction.OUTBOUND, Direction.INBOUND}
    assert {event.direction for event in generated_events} == {Direction.OUTBOUND, Direction.INBOUND}

    normalized_reference, window = normalize_reference(reference_events)
    aligned_generated = align_generated(generated_events, window)
    result = compare_traces(
        normalized_reference,
        aligned_generated,
        window,
        load_experiment(_EXAMPLE_CONFIG).similarity,
    )

    assert window == 10.0
    assert normalized_reference == _NORMALIZED_REFERENCE
    assert aligned_generated == _ALIGNED_GENERATED
    assert normalized_reference[0].timestamp == aligned_generated[0].timestamp == 0.0
    assert normalized_reference[-1].timestamp == window
    assert aligned_generated[-1].timestamp <= window
    assert all(method.diagnostics["observation_window_seconds"] == window for method in result.methods.values())
    assert {name: method.score for name, method in result.methods.items()} == pytest.approx(
        {
            "autocorrelation": 0.756547619047619,
            "frame_size_ks": 0.8,
            "iat_ks": 0.5,
            "multiscale_rate": 0.20833333333333326,
        }
    )
    assert result.aggregate_score == pytest.approx(0.5662202380952381)


def test_checked_in_similarity_artifact_keeps_the_fixed_four_method_json_shape() -> None:
    """A weight choice must not remove a required method or its retained diagnostics from published JSON."""
    document = load_comparison_result(_EXAMPLE_DATA / "similarity.json").as_dict()

    assert tuple(document) == ("aggregate_score", "input_identities", "methods", "observation_window_seconds")
    methods = cast(dict[str, dict[str, object]], document["methods"])
    assert tuple(methods) == ("autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate")
    assert all(tuple(method) == ("diagnostics", "score", "weight") for method in methods.values())


def test_reversing_only_directions_has_maximum_multiscale_discrepancy() -> None:
    """Dropping direction-separated one-bin cells would make an all-outbound trace match its inbound reversal."""
    reference = (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(0.5, Direction.OUTBOUND, 100),
        TraceEvent(1.0, Direction.OUTBOUND, 140),
    )
    reversed_directions = tuple(
        TraceEvent(event.timestamp, Direction.INBOUND, event.frame_length) for event in reference
    )
    base = load_experiment(_EXAMPLE_CONFIG).similarity
    one_bin = base.model_copy(update={"multiscale_widths_seconds": (1.0,), "multiscale_scale_weights": (1.0,)})

    result = compare_traces(reference, reversed_directions, 1.0, one_bin)

    assert result.methods["frame_size_ks"].score == 1.0
    assert result.methods["iat_ks"].score == 1.0
    assert result.methods["autocorrelation"].score == 1.0
    assert result.methods["multiscale_rate"].score == 0.0
    assert result.methods["multiscale_rate"].diagnostics["discrepancy"] == 1.0


def test_in_process_compare_matches_api_without_internal_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Routing compare through run preparation, Docker, or a subprocess would violate the one-process stage contract."""
    api_experiment, _ = _prepare_existing_run(tmp_path, "api-run")
    cli_experiment, cli_run = _prepare_existing_run(tmp_path, "cli-run")
    snapshot_before = (cli_run / "experiment.toml").read_bytes()
    expected = compare_experiment(api_experiment)
    real_import = builtins.__import__

    def reject_subprocess(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("compare invoked a subprocess")

    def reject_eager_import(
        name: str,
        globals: Any = None,
        locals: Any = None,
        fromlist: tuple[str, ...] | None = None,
        level: int = 0,
    ) -> Any:
        if _is_docker_adapter_import(name, fromlist) or _is_run_or_capture_import(name, fromlist):
            raise AssertionError(f"compare imported forbidden module {name}")
        return real_import(name, globals, locals, fromlist or (), level)

    _clear_docker_adapter_import_state(monkeypatch)
    monkeypatch.delitem(sys.modules, "trafficlab.capture.stage", raising=False)
    monkeypatch.delattr(capture_package, "stage", raising=False)
    monkeypatch.delitem(sys.modules, "trafficlab.pipeline.stage", raising=False)
    monkeypatch.delattr(trafficlab, "run", raising=False)
    assert {name for name in sys.modules if _is_docker_adapter_module(name)} == set()
    assert not hasattr(capture_package, "docker_cli")
    monkeypatch.setattr(subprocess, "run", reject_subprocess)
    monkeypatch.setattr(subprocess, "Popen", reject_subprocess)
    monkeypatch.setattr(builtins, "__import__", reject_eager_import)

    assert main(["compare", str(cli_experiment)]) == 0

    captured = capsys.readouterr()
    assert (
        captured.out
        == f"compare: aggregate_score={expected.aggregate_score:.6f} output={cli_run / 'similarity.json'}\n"
    )
    assert captured.err == ""
    assert (cli_run / "experiment.toml").read_bytes() == snapshot_before
    assert load_comparison_result(cli_run / "similarity.json") == expected
    assert (cli_run / "similarity.json").read_bytes() == (_EXAMPLE_DATA / "similarity.json").read_bytes()
    assert {name for name in sys.modules if _is_docker_adapter_module(name)} == set()
    assert "trafficlab.pipeline.stage" not in sys.modules
    assert "trafficlab.capture.stage" not in sys.modules
    assert not hasattr(capture_package, "docker_cli")


def test_compare_cli_starts_from_a_fresh_interpreter_without_loading_run_capture_or_docker(tmp_path: Path) -> None:
    """A cached CLI dependency must not hide cold compare contamination by capture or Docker code."""
    experiment_path, run_directory = _prepare_existing_run(tmp_path, "cold-compare")
    expected = load_comparison_result(_EXAMPLE_DATA / "similarity.json")
    evidence_path = tmp_path / "cold-compare.json"
    script = """
import json
import subprocess
import sys
from pathlib import Path

def reject_subprocess(*_args, **_kwargs):
    raise AssertionError("cold compare invoked a subprocess")

subprocess.run = reject_subprocess
subprocess.Popen = reject_subprocess
from trafficlab.cli import main

status = main(["compare", sys.argv[1]])
forbidden = sorted(
    name for name in sys.modules
    if name in {"trafficlab.pipeline.stage", "trafficlab.capture.stage", "trafficlab.capture.docker_cli"}
    or name.startswith("trafficlab.capture.docker_cli.")
)
Path(sys.argv[2]).write_text(json.dumps({"forbidden": forbidden, "status": status}), encoding="utf-8")
"""

    result = subprocess.run(
        [sys.executable, "-c", script, str(experiment_path), str(evidence_path)],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        f"compare: aggregate_score={expected.aggregate_score:.6f} output={run_directory / 'similarity.json'}\n"
    )
    assert result.stderr == ""
    assert json.loads(evidence_path.read_text(encoding="utf-8")) == {"forbidden": [], "status": 0}


def test_installed_compare_publishes_expected_result(tmp_path: Path) -> None:
    """A source-only parser registration would leave the installed public command unusable."""
    experiment_path, run_directory = _prepare_existing_run(tmp_path, "installed-run")
    expected = load_comparison_result(_EXAMPLE_DATA / "similarity.json")
    working_directory = _isolated_installed_working_directory(tmp_path)

    result = _run_installed_compare(experiment_path, working_directory=working_directory)

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        f"compare: aggregate_score={expected.aggregate_score:.6f} output={run_directory / 'similarity.json'}\n"
    )
    assert result.stderr == ""
    assert load_comparison_result(run_directory / "similarity.json") == expected


@pytest.mark.parametrize("failure", ["missing-capture", "corrupt-similarity"])
def test_installed_compare_reports_the_exact_production_error(failure: str, tmp_path: Path) -> None:
    """Changing or hiding API correction text at the CLI would make a failed offline run harder to repair."""
    experiment_path, run_directory = _prepare_existing_run(
        tmp_path,
        f"error-{failure}",
        include_similarity=failure == "corrupt-similarity",
    )
    if failure == "missing-capture":
        (run_directory / "capture.json").unlink()
    else:
        (run_directory / "similarity.json").write_bytes(b"{}")
    working_directory = _isolated_installed_working_directory(tmp_path)

    with pytest.raises(TrafficlabError) as raised:
        compare_experiment(experiment_path)

    result = _run_installed_compare(experiment_path, working_directory=working_directory)

    assert result.returncode == raised.value.exit_code
    assert result.stdout == ""
    assert result.stderr == f"compare: {raised.value}; {raised.value.corrective_action}\n"
    assert "Traceback" not in result.stderr
