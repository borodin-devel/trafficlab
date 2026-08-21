from __future__ import annotations

from pathlib import Path

TESTS = Path(__file__).resolve().parents[2]

ALLOWED_SUBSYSTEMS = {
    "unit": {
        "capture",
        "common",
        "comparison",
        "fitting",
        "generation",
        "pipeline",
        "preflight",
        "tooling",
        "validation",
    },
    "integration": {"capture", "comparison", "fitting", "generation", "pipeline", "preflight", "validation"},
    "docker": {"capture", "pipeline"},
    "internet": {"capture"},
    "property": {"common", "comparison", "fitting", "generation"},
    "scientific": {"fitting", "generation"},
}


def test_behavior_tests_are_grouped_by_scope_then_subsystem() -> None:
    misplaced: dict[str, tuple[str, ...]] = {}

    for scope, allowed in ALLOWED_SUBSYSTEMS.items():
        scope_root = TESTS / scope
        invalid = tuple(
            sorted(
                path.relative_to(scope_root).as_posix()
                for path in scope_root.rglob("test_*.py")
                if path.relative_to(scope_root).parts[0] not in allowed
            )
        )
        if invalid:
            misplaced[scope] = invalid

    assert misplaced == {}
