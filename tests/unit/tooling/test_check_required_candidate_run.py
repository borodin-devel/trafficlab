"""Tests for the required-candidate saved-run verifier."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import scripts.check_required_candidate_run as checker
from tests.fixtures.paths import VALIDATION_STUDY_CANDIDATE
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.config_io import load_experiment, render_effective_config
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.json import render_json_document

_VALID_RUN = VALIDATION_STUDY_CANDIDATE / "training" / "short" / "r1"


def _effective_fixture(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    shutil.copytree(_VALID_RUN, run)
    config = load_experiment(run / "experiment.toml")
    config = config.model_copy(update={"run": config.run.model_copy(update={"directory": run.resolve()})})
    snapshot = render_effective_config(config)
    (run / "experiment.toml").write_bytes(snapshot)
    checkpoint = json.loads((run / "checkpoint.json").read_text(encoding="utf-8"))
    checkpoint["experiment_identity"] = identify_bytes(snapshot).as_dict()
    (run / "checkpoint.json").write_bytes(render_json_document(checkpoint))
    records = [
        {
            "event": "effective_config_published",
            "path": str(run / "experiment.toml"),
            "stage": "preflight",
        },
        {"event": "run_prepared", "path": str(run), "stage": "preflight"},
    ]
    records.extend(json.loads(line) for line in (run / "run.log").read_text().splitlines()[2:])
    (run / "run.log").write_text(
        "".join(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for record in records)
    )
    return run


def test_check_run_validates_saved_model_reproduction_and_final_artifacts(tmp_path: Path) -> None:
    run = _effective_fixture(tmp_path)
    result = checker.check_run(run)

    assert result.run_directory == run.resolve()
    assert result.reference_packet_count == 21
    assert result.generated_packet_count == 18
    assert result.fitness_method_count == 8
    assert result.postfit_diagnostic_count == 3
    assert result.generated_bytes_reproduced is True
    assert result.comparison_reproduced is True


def test_main_accepts_multiple_run_directories(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = _effective_fixture(tmp_path)
    assert checker.main([str(run), str(run)]) == 0

    output = capsys.readouterr().out
    assert output.count("strict_artifacts=pass") == 2
    assert output.count("reproduction=pass") == 2


def test_check_run_rejects_foreign_generated_capture(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run = _effective_fixture(tmp_path)
    generated = run / "generated.pcapng"
    generated.write_bytes(generated.read_bytes() + b"foreign")

    with pytest.raises(TrafficlabError, match="generated.pcapng"):
        checker.check_run(run)
