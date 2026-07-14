"""Файловое хранилище неизменяемых результатов стадий."""

from pathlib import Path
import shutil
import tempfile
from typing import Callable

from trafficlab.contracts.stage import StageStatus
from trafficlab.contracts.stage_state import StageState
from trafficlab.runstore.manifest import read_json, write_json


class RunStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def stage_path(self, stage_type: str, stage_key: str) -> Path:
        return self.root / stage_type / stage_key.removeprefix("sha256:")

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
        parent = destination.parent
        parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=parent))
        try:
            state.start()
            write_outputs(temporary)
            state.complete({})
            write_json(temporary / "stage.json", state.to_dict())
            temporary.replace(destination)
            return destination
        except Exception as error:
            state.fail(type(error).__name__, str(error))
            write_json(temporary / "stage.json", state.to_dict())
            raise
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
