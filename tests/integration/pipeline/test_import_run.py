"""Complete in-process imported-reference workflow evidence."""

from __future__ import annotations

import builtins
import copy
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, cast

import pytest
import tomli_w

from trafficlab.common.compatibility import identify_file
from trafficlab.common.errors import TrafficlabError

pytestmark = pytest.mark.integration

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "data" / "import_run"
_NINE_ARTIFACTS = (
    "best_model.json",
    "capture.json",
    "checkpoint.json",
    "experiment.toml",
    "ga_history.csv",
    "generated.pcapng",
    "reference.pcapng",
    "run.log",
    "similarity.json",
)
_SCIENTIFIC_ARTIFACTS = tuple(name for name in _NINE_ARTIFACTS if name != "run.log")


def _small_config(valid_config_data: dict[str, object], run_directory: Path) -> dict[str, object]:
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    cast(dict[str, object], data["generation"])["trial"] = {
        "max_packets": 100,
        "max_output_bytes": 100_000,
        "max_wall_seconds": 5.0,
    }
    cast(dict[str, object], data["generation"])["final"] = {
        "max_packets": 100,
        "max_output_bytes": 100_000,
        "max_wall_seconds": 5.0,
    }
    cast(dict[str, object], data["genetic"]).update(
        population_size=2,
        generation_count=0,
        tournament_size=2,
        elite_count=1,
        trial_seeds=[101],
        resume=True,
    )
    models = cast(dict[str, object], data["models"])
    poisson = copy.deepcopy(cast(dict[str, object], models["poisson_empirical"]))
    poisson["c_lambda"] = {"lower": 3.0, "upper": 4.0}
    data["models"] = {
        "enabled": ["poisson_empirical"],
        "poisson_empirical": poisson,
    }
    return data


def _records(run_directory: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (run_directory / "run.log").read_text(encoding="utf-8").splitlines()]


def _forbid_external_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def reject_process(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("import-run attempted external process execution")

    def guarded_import(
        name: str,
        globals: Any = None,
        locals: Any = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        docker_adapter = name.startswith("trafficlab.capture.docker.") or (
            name == "trafficlab.capture.docker"
            and bool({"compose", "image", "process", "types"}.intersection(fromlist))
        )
        repository_script = name == "scripts" or name.startswith("scripts.")
        if docker_adapter or repository_script:
            raise AssertionError(f"import-run imported forbidden module {name}")
        return real_import(name, globals, locals, fromlist, level)

    for name in tuple(sys.modules):
        if name.startswith("trafficlab.capture.docker."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(subprocess, "run", reject_process)
    monkeypatch.setattr(subprocess, "Popen", reject_process)
    monkeypatch.setattr(builtins, "__import__", guarded_import)


def test_fresh_interpreter_guards_before_importing_imported_coordinator() -> None:
    """An eager subprocess, Docker-adapter, or repository-script import must fail in a clean process."""
    script = textwrap.dedent(
        """
        import builtins
        import pathlib
        import sys

        original_import = builtins.__import__
        blocked = []
        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            forbidden = (
                name == "subprocess"
                or name.startswith("subprocess.")
                or name.startswith("trafficlab.capture.docker")
                or name == "scripts"
                or name.startswith("scripts.")
            )
            if forbidden:
                blocked.append(name)
                raise AssertionError(f"forbidden import: {name}")
            return original_import(name, globals, locals, fromlist, level)

        builtins.__import__ = guarded_import
        import trafficlab.pipeline.imported as imported
        assert callable(imported.run_imported_experiment)
        assert "subprocess" not in sys.modules
        assert not any(name.startswith("trafficlab.capture.docker") for name in sys.modules)
        assert not any(name == "scripts" or name.startswith("scripts.") for name in sys.modules)

        for forbidden_name in (
            "subprocess",
            "trafficlab.capture.docker.compose",
            "scripts.prepare_traffic_dumps",
        ):
            try:
                __import__(forbidden_name)
            except AssertionError:
                pass
            else:
                raise AssertionError(f"guard accepted {forbidden_name}")
        assert blocked == [
            "subprocess",
            "trafficlab.capture.docker.compose",
            "scripts.prepare_traffic_dumps",
        ]
        print(pathlib.Path(imported.__file__).resolve())
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[3],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == str(
        Path(__file__).resolve().parents[3] / "src" / "trafficlab" / "pipeline" / "imported.py"
    )


def test_imported_run_normalizes_and_completes_real_pipeline_with_compatible_retry(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bypassing real stages, process guards, exact reuse, or final validation must break this workflow."""
    source_directory = tmp_path / "source"
    shutil.copytree(_FIXTURES / "classic-pcap-source", source_directory)
    source_paths = (source_directory / "capture.json", source_directory / "source.pcap")
    original_source_bytes = {path.name: path.read_bytes() for path in source_paths}
    run_directory = tmp_path / "run"
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_text(
        tomli_w.dumps(_small_config(valid_config_data, run_directory)),
        encoding="utf-8",
    )
    assert "run_imported_experiment" not in globals()
    _forbid_external_execution(monkeypatch)

    from trafficlab.pipeline.imported import run_imported_experiment

    first = run_imported_experiment(experiment_path, source_directory)

    assert {path.name: path.read_bytes() for path in source_paths} == original_source_bytes
    records = _records(run_directory)
    assert [record["event"] for record in records[:2]] == ["effective_config_published", "run_prepared"]
    publication = next(record for record in records if record.get("event") == "reference_imported")
    assert publication["reused"] is False
    assert publication["source_capture_identity"] == identify_file(source_directory / "source.pcap").as_dict()
    assert publication["source_metadata_identity"] == identify_file(source_directory / "capture.json").as_dict()
    assert publication["reference_identity"] == identify_file(run_directory / "reference.pcapng").as_dict()
    assert publication["capture_identity"] == identify_file(run_directory / "capture.json").as_dict()
    assert first.capture.packet_count == 4
    assert first.capture.reused is False
    assert first.fit.reused_best_model is False
    assert first.generation.reused is False
    assert first.comparison.input_sha256 is not None
    assert records[-1]["event"] == "run_completed"
    assert tuple(sorted(path.name for path in run_directory.iterdir())) == _NINE_ARTIFACTS
    first_scientific = {name: (run_directory / name).read_bytes() for name in _SCIENTIFIC_ARTIFACTS}

    retried = run_imported_experiment(experiment_path, source_directory)

    assert {path.name: path.read_bytes() for path in source_paths} == original_source_bytes
    retry_records = _records(run_directory)
    assert retried.capture.reused is True
    assert retried.fit.reused_best_model is True
    assert retried.generation.reused is True
    assert [record["reused"] for record in retry_records if record.get("event") == "reference_imported"] == [
        False,
        True,
    ]
    assert any(
        record.get("event") == "comparison_succeeded" and record.get("reused") is True for record in retry_records
    )
    assert retry_records[-1]["event"] == "run_completed"
    assert {name: (run_directory / name).read_bytes() for name in _SCIENTIFIC_ARTIFACTS} == first_scientific

    capture_path = source_directory / "source.pcap"
    changed_source = bytearray(capture_path.read_bytes())
    changed_source[-1] ^= 1
    capture_path.write_bytes(changed_source)
    changed_source_bytes = {path.name: path.read_bytes() for path in source_paths}

    with pytest.raises(TrafficlabError, match="not an exact imported-reference reuse"):
        run_imported_experiment(experiment_path, source_directory)

    assert {name: (run_directory / name).read_bytes() for name in _SCIENTIFIC_ARTIFACTS} == first_scientific
    assert {path.name: path.read_bytes() for path in source_paths} == changed_source_bytes
    assert _records(run_directory)[-1]["event"] == "run_failed"
    assert _records(run_directory)[-1]["failed_stage"] == "capture"
