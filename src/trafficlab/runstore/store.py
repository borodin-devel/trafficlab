"""Файловое хранилище неизменяемых результатов запусков и стадий."""

from collections.abc import Callable
from pathlib import Path
import shutil
from typing import Any

from trafficlab.contracts.stage import StageStatus
from trafficlab.contracts.stage_state import StageState, utc_now
from trafficlab.runstore.atomic import publish_directory
from trafficlab.runstore.hashes import hash_file
from trafficlab.runstore.manifest import read_json, write_json
from trafficlab.runstore.paths import new_run_id


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return "\"" + str(value).replace("\\", "\\\\").replace("\"", "\\\"") + "\""


def _write_flat_toml(path: Path, value: dict[str, Any]) -> None:
    lines = [f"{key} = {_toml_scalar(item)}" for key, item in sorted(value.items())]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")



class RunStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def stage_path(self, stage_type: str, stage_key: str) -> Path:
        return self.root / stage_type / stage_key.removeprefix("sha256:")

    def run_path(self, run_id: str) -> Path:
        return self.root / run_id

    def create_run(
        self,
        *,
        config_resolved: dict[str, Any] | None = None,
        environment: dict[str, Any] | None = None,
        lineage: dict[str, Any] | None = None,
        config_input: str | None = None,
        run_id: str | None = None,
    ) -> Path:
        """Create a minimal run directory with required root manifests."""
        actual_run_id = run_id or new_run_id()
        destination = self.run_path(actual_run_id)
        resolved = config_resolved or {}
        actual_environment = environment or {}
        actual_lineage = lineage or {}

        def write_run(target: Path) -> None:
            if config_input is not None:
                (target / "config.input.toml").write_text(config_input, encoding="utf-8")
            _write_flat_toml(target / "config.resolved.toml", resolved)
            write_json(target / "environment.json", actual_environment)
            write_json(target / "lineage.json", actual_lineage)
            (target / "stages").mkdir()
            write_json(
                target / "run.json",
                {
                    "artifact_type": "trafficlab_run",
                    "artifact_version": "1.0.0",
                    "schema_version": "1.0.0",
                    "run_id": actual_run_id,
                    "created_at": utc_now(),
                    "status": "running",
                    "resolved_config_sha256": hash_file(target / "config.resolved.toml"),
                    "environment_sha256": hash_file(target / "environment.json"),
                    "lineage_sha256": hash_file(target / "lineage.json"),
                },
            )

        return publish_directory(destination, write_run)

    def has_completed(self, stage_type: str, stage_key: str) -> bool:
        path = self.stage_path(stage_type, stage_key) / "stage.json"
        if not path.is_file():
            return False
        try:
            return read_json(path).get("status") == StageStatus.COMPLETED.value
        except (OSError, ValueError):
            return False

    def publish(self, state: StageState, write_outputs: Callable[[Path], None]) -> Path:
        destination = self.stage_path(state.stage_type, state.stage_key)
        if self.has_completed(state.stage_type, state.stage_key):
            return destination

        def write_stage(target: Path) -> None:
            state.start()
            try:
                write_outputs(target)
                state.complete({})
                write_json(target / "stage.json", state.to_dict())
            except Exception as error:
                state.fail(type(error).__name__, str(error))
                write_json(target / "stage.json", state.to_dict())
                raise

        try:
            return publish_directory(destination, write_stage)
        except Exception:
            if destination.exists() and not self.has_completed(state.stage_type, state.stage_key):
                shutil.rmtree(destination)
            raise
