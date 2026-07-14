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
