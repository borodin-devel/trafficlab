import tempfile
import unittest
from pathlib import Path

from trafficlab.contracts.stage_state import StageState
from trafficlab.runstore.store import RunStore


class RunStoreTests(unittest.TestCase):
    def test_publish_is_atomic_and_reusable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory))
            state = StageState("convert", "sha256:key", {}, [], "0.1")
            path = store.publish(state, lambda target: (target / "output.txt").write_text("ok", encoding="utf-8"))
            self.assertTrue((path / "stage.json").is_file())
            self.assertTrue(store.has_completed("convert", "sha256:key"))
            self.assertEqual(store.publish(state, lambda target: (_ for _ in ()).throw(AssertionError())), path)

    def test_create_run_writes_required_contract_files(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory))
            path = store.create_run(config_resolved={"seed": 1}, environment={"python": "3.12"}, lineage={})
            self.assertTrue((path / "run.json").is_file())
            self.assertTrue((path / "config.resolved.toml").is_file())
            self.assertTrue((path / "environment.json").is_file())
            self.assertTrue((path / "lineage.json").is_file())
            self.assertTrue((path / "stages").is_dir())
