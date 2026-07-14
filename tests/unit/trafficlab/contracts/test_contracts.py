import unittest

from trafficlab.contracts.dataset import DatasetManifest
from trafficlab.contracts.stage import StageKeyInput, compute_stage_key


class ContractTests(unittest.TestCase):
    def test_stage_key_is_deterministic_and_order_sensitive(self):
        base = StageKeyInput("convert", {"b": 2, "a": 1}, ("sha256:a",), "0.1")
        self.assertEqual(compute_stage_key(base), compute_stage_key(base))
        self.assertNotEqual(compute_stage_key(base), compute_stage_key(StageKeyInput("convert", base.normalized_config, ("sha256:b",), "0.1")))


    def test_dataset_identity_excludes_derived_files(self):
        manifest = DatasetManifest({"type": "pcapng", "sha256": "sha256:source"}, {
            "packets.parquet": "sha256:packets", "validation.json": "sha256:one", "summary.md": "sha256:two"
        })
        first = manifest.compute_artifact_id()
        manifest.files["validation.json"] = "sha256:changed"
        self.assertEqual(manifest.compute_artifact_id(), first)
