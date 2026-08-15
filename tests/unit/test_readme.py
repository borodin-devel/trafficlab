from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
README_PATH = REPOSITORY_ROOT / "README.md"

REQUIRED_HEADINGS = {
    "## Purpose",
    "## Requirements",
    "## Installation",
    "## Quick start",
    "## Workflow stages",
    "## Traffic models",
    "## Similarity methods",
    "## Genetic models",
    "## Testing",
    "## Project documentation",
}

REQUIRED_LINKS = {
    "architecture/SYSTEM.md#preflight",
    "architecture/SYSTEM.md#capture",
    "architecture/SYSTEM.md#fit",
    "architecture/SYSTEM.md#generate",
    "architecture/SYSTEM.md#compare",
    "architecture/SYSTEM.md#run",
    "architecture/traffic_models/poisson_empirical.md",
    "architecture/traffic_models/markov_renewal.md",
    "architecture/traffic_models/mmpp.md",
    "architecture/similarity_methods/frame_size_ks.md",
    "architecture/similarity_methods/iat_ks.md",
    "architecture/similarity_methods/autocorrelation.md",
    "architecture/similarity_methods/multiscale_rate.md",
    "architecture/genetic_models/basic_generational.md",
}


def _links(document: str) -> set[str]:
    return set(re.findall(r"(?<!!)\[[^]]+\]\(([^)]+)\)", document))


def _heading_anchors(document: str) -> set[str]:
    anchors: set[str] = set()
    for line in document.splitlines():
        match = re.fullmatch(r"#{1,6} +(.+?) *#*", line)
        if match is None:
            continue
        normalized = re.sub(r"[^\w -]", "", match.group(1).lower())
        anchors.add(normalized.replace(" ", "-"))
    return anchors


def test_readme_has_required_sections_and_architecture_links() -> None:
    document = README_PATH.read_text(encoding="utf-8")
    headings = {line for line in document.splitlines() if line.startswith("## ")}

    assert REQUIRED_HEADINGS <= headings
    assert REQUIRED_LINKS <= _links(document)
    assert "docs/stages" not in document
    assert "docs/traffic_models" not in document
    assert "docs/similarity_methods" not in document
    assert "docs/genetic_models" not in document


def test_every_relative_readme_link_resolves() -> None:
    document = README_PATH.read_text(encoding="utf-8")

    for link in sorted(_links(document)):
        if "://" in link or link.startswith("#"):
            continue
        relative_target, separator, anchor = unquote(link).partition("#")
        target = REPOSITORY_ROOT / relative_target
        assert target.is_file(), link
        if separator:
            assert anchor in _heading_anchors(target.read_text(encoding="utf-8")), link


def _git_ignores(relative_path: str) -> bool:
    result = subprocess.run(
        ("git", "check-ignore", "--no-index", "--quiet", relative_path),
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    assert result.returncode in {0, 1}
    return result.returncode == 0


def test_only_completed_accepted_evidence_is_trackable() -> None:
    assert _git_ignores("runs/scratch/result.json")
    assert _git_ignores("examples/validation_study/.study-work/candidate/manifest.json")
    assert _git_ignores("examples/validation_study/evidence/.candidates/study-1/manifest.json")
    assert _git_ignores("examples/validation_study/evidence/.study-1.random.tmp/manifest.json")
    assert not _git_ignores("examples/validation_study/evidence/study-1/manifest.json")
