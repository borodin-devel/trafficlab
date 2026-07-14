import unittest

from trafficlab.contracts.stage import StageStatus
from trafficlab.contracts.stage_state import StageState


class StageStateTests(unittest.TestCase):
    def test_lifecycle(self):
        state = StageState("convert", "sha256:key", {}, [], "0.1")
        state.start()
        self.assertEqual(state.status, StageStatus.RUNNING)
        state.complete({"dataset": "sha256:out"})
        self.assertEqual(state.status, StageStatus.COMPLETED)
        self.assertIsNotNone(state.finished_at)
