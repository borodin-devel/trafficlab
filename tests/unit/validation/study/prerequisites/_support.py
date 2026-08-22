"""Shared typed setup for this decomposed validation suite."""

from __future__ import annotations

import tempfile as tempfile
from pathlib import Path
from typing import cast

import scripts.validation_study.common as vs_common
import scripts.validation_study.prerequisites.codec as vs_prereq_codec
from tests.support.validation_study.builders import valid_prerequisite
from trafficlab.common.compatibility import identify_bytes


def write_legacy_prerequisite_root(
    repository: Path,
    *,
    study_id: str = "study-r4",
) -> tuple[Path, bytes]:
    """Create the schema-1 root and markers published before raw archives existed."""

    prerequisite = valid_prerequisite(study_id=study_id)
    content = vs_prereq_codec.render_prerequisite_results(prerequisite)
    root = repository / "examples" / "validation_study" / "prerequisites.json"
    root.parent.mkdir(parents=True, exist_ok=True)
    root.write_bytes(content)
    attempt = root.parent / ".study-work" / "attempts" / study_id
    attempt.mkdir(parents=True)
    (attempt / "prerequisites.json").write_bytes(
        vs_common.canonical_json(
            cast(
                vs_common.JsonObject,
                {"phase": "prerequisites", "study_id": study_id, "url": prerequisite.url},
            )
        )
    )
    (attempt / "prerequisites-success.json").write_bytes(
        vs_common.canonical_json(
            cast(
                vs_common.JsonObject,
                {
                    "phase": "prerequisites",
                    "prerequisites_identity": identify_bytes(content).as_dict(),
                    "study_id": study_id,
                    "url": prerequisite.url,
                },
            )
        )
    )
    assert not (attempt / "prerequisites.raw.json").exists()
    return root, content
