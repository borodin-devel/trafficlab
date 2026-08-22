"""Schema owner for Validation Study tooling."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

from scripts.validation_study.common import (
    JsonObject,
    canonical_json,
    exact_object,
    load_json,
    repository_relative_path,
    require,
    retained_identity,
    strict_bool,
    strict_int,
    strict_string,
    validate_study_id,
)


@dataclass(slots=True)
class PrerequisiteRotationTarget:
    """One owned file in a marker-last prerequisite rotation."""

    kind: str
    destination: Path
    stage: Path | None
    backup: Path | None
    before_identity: JsonObject | None
    target_identity: JsonObject
    must_be_absent: bool


def collection_attempt_root(repository_root: Path, study_id: str) -> Path:
    return repository_root / "examples" / "validation_study" / ".study-work" / "attempts" / study_id


def prerequisite_raw_archive_path(repository_root: Path, study_id: str) -> Path:
    return collection_attempt_root(repository_root, study_id) / "prerequisites.raw.json"


def prerequisite_rotation_journal_path(repository_root: Path, study_id: str) -> Path:
    return collection_attempt_root(repository_root, study_id) / "prerequisites-rotation.json"


def prerequisite_rotation_expected_targets(repository_root: Path, study_id: str) -> tuple[tuple[str, Path, bool], ...]:
    study_root = repository_root / "examples" / "validation_study"
    attempt = collection_attempt_root(repository_root, study_id)
    return (
        ("archive", attempt / "prerequisites.raw.json", True),
        ("config-short", study_root / "configs" / "short.toml", False),
        ("config-streaming", study_root / "configs" / "streaming.toml", False),
        ("config-bursty", study_root / "configs" / "bursty.toml", False),
        ("root", study_root / "prerequisites.json", False),
        ("marker", attempt / "prerequisites-success.json", True),
    )


def _prerequisite_rotation_relative_path(repository_root: Path, path: Path, *, name: str) -> str:
    try:
        return path.relative_to(repository_root).as_posix()
    except ValueError as error:
        raise ValueError(f"{name} must be beneath the repository root") from error


def _prerequisite_rotation_sibling_path(
    repository_root: Path, value: object, *, destination: Path, suffix: str, name: str
) -> Path:
    relative = repository_relative_path(value, repository_root=repository_root, name=name)
    path = repository_root / Path(*PurePosixPath(relative).parts)
    require(
        path.parent == destination.parent
        and path.name.startswith(f".{destination.name}.")
        and path.name.endswith(suffix),
        f"{name} must be an exact prerequisite rotation sibling path",
    )
    return path


def render_prerequisite_rotation_journal(
    repository_root: Path, *, study_id: str, targets: Sequence[PrerequisiteRotationTarget]
) -> bytes:
    entries: list[object] = []
    for target in targets:
        stage = target.stage
        backup = target.backup
        if stage is None:
            raise ValueError("prerequisite rotation journal requires every staged target")
        entries.append(
            {
                "backup": _prerequisite_rotation_relative_path(repository_root, backup, name="rotation backup")
                if backup is not None
                else None,
                "before_identity": target.before_identity,
                "destination": _prerequisite_rotation_relative_path(
                    repository_root, target.destination, name="rotation destination"
                ),
                "kind": target.kind,
                "must_be_absent": target.must_be_absent,
                "stage": _prerequisite_rotation_relative_path(repository_root, stage, name="rotation stage"),
                "target_identity": target.target_identity,
            }
        )
    return canonical_json(
        cast(
            JsonObject,
            {"phase": "prerequisite-rotation", "schema_version": 1, "study_id": study_id, "targets": entries},
        )
    )


def parse_prerequisite_rotation_journal(
    content: bytes, *, repository_root: Path, journal: Path
) -> tuple[str, list[PrerequisiteRotationTarget]]:
    document = exact_object(
        load_json(content), ("phase", "schema_version", "study_id", "targets"), name="prerequisite rotation journal"
    )
    require(canonical_json(cast(JsonObject, document)) == content, "prerequisite rotation journal must be canonical")
    require(document["phase"] == "prerequisite-rotation", "prerequisite rotation journal phase is invalid")
    require(
        strict_int(document["schema_version"], name="prerequisite rotation journal schema_version") == 1,
        "prerequisite rotation journal schema_version is unsupported",
    )
    study_id = validate_study_id(strict_string(document["study_id"], name="prerequisite rotation journal study_id"))
    require(
        journal == prerequisite_rotation_journal_path(repository_root, study_id),
        "prerequisite rotation journal must use its exact attempt path",
    )
    raw_targets_value = document["targets"]
    require(type(raw_targets_value) is list, "prerequisite rotation journal targets must be an array")
    raw_targets = cast(list[object], raw_targets_value)
    expected = prerequisite_rotation_expected_targets(repository_root, study_id)
    require(len(raw_targets) == len(expected), "prerequisite rotation journal must contain its exact target count")
    parsed: list[PrerequisiteRotationTarget] = []
    for raw_target, (kind, destination, must_be_absent) in zip(raw_targets, expected, strict=True):
        target = exact_object(
            raw_target,
            ("backup", "before_identity", "destination", "kind", "must_be_absent", "stage", "target_identity"),
            name="prerequisite rotation journal target",
        )
        require(target["kind"] == kind, "prerequisite rotation journal target order is invalid")
        require(
            strict_bool(target["must_be_absent"], name="prerequisite rotation target must_be_absent") == must_be_absent,
            "prerequisite rotation journal target absence policy is invalid",
        )
        destination_relative = repository_relative_path(
            target["destination"], repository_root=repository_root, name="prerequisite rotation destination"
        )
        require(
            destination_relative
            == _prerequisite_rotation_relative_path(
                repository_root, destination, name="expected prerequisite rotation destination"
            ),
            "prerequisite rotation journal destination is invalid",
        )
        stage = _prerequisite_rotation_sibling_path(
            repository_root, target["stage"], destination=destination, suffix=".tmp", name="prerequisite rotation stage"
        )
        before_value = target["before_identity"]
        backup_value = target["backup"]
        before_identity = (
            None
            if before_value is None
            else retained_identity(before_value, name="prerequisite rotation prior identity")
        )
        backup = (
            None
            if backup_value is None
            else _prerequisite_rotation_sibling_path(
                repository_root,
                backup_value,
                destination=destination,
                suffix=".bak",
                name="prerequisite rotation backup",
            )
        )
        require(
            (before_identity is None) == (backup is None),
            "prerequisite rotation journal backup and prior identity must agree",
        )
        if must_be_absent:
            require(
                before_identity is None and backup is None,
                "prerequisite rotation absent target cannot have prior bytes",
            )
        parsed.append(
            PrerequisiteRotationTarget(
                kind=kind,
                destination=destination,
                stage=stage,
                backup=backup,
                before_identity=before_identity,
                target_identity=retained_identity(
                    target["target_identity"], name="prerequisite rotation target identity"
                ),
                must_be_absent=must_be_absent,
            )
        )
    return (study_id, parsed)
