"""Constants owner for Validation Study tooling."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

from tests.fixtures.paths import PIPELINE_FIXTURE_ROOT

HASH = "a" * 64

IMAGE_ID = f"sha256:{'b' * 64}"

ROOT = Path(__file__).resolve().parents[3]

FIT_FIXTURE = PIPELINE_FIXTURE_ROOT / "fit"

CAPTURE_BYTES = (FIT_FIXTURE / "capture.json").read_bytes()

REFERENCE_BYTES = (FIT_FIXTURE / "reference.pcapng").read_bytes()

CAPTURE_DOCKERFILE = (ROOT / "docker" / "capture" / "Dockerfile").read_bytes()

CAPTURE_SCRIPT = (ROOT / "docker" / "capture" / "capture.sh").read_bytes()

_CAPTURE_IMAGE_LOCK = json.loads((ROOT / "docker" / "capture" / "image-lock.json").read_text(encoding="utf-8"))

CAPTURE_IMAGE_ID = cast(str, _CAPTURE_IMAGE_LOCK["expected_capture_image_id"])

REAL_SUBPROCESS_RUN = subprocess.run
