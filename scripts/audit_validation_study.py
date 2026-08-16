#!/usr/bin/env python3
"""Audit one retained Validation Study bundle without Docker, network, or mutation."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import stat
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from statistics import fmean
from typing import NoReturn, cast

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import run_validation_study as study
from trafficlab.capture_validation import validate_capture_pair
from trafficlab.comparison import ComparisonResult, parse_comparison_result, render_comparison_result
from trafficlab.compatibility import ContentIdentity
from trafficlab.config import ExperimentConfig
from trafficlab.config_io import load_configuration_pair, render_effective_config
from trafficlab.errors import FailureOutcome, TrafficlabError
from trafficlab.genetic.checkpoint import CheckpointState, parse_checkpoint, render_history_csv
from trafficlab.genetic.evaluation import evaluate_final, validate_evaluation_context
from trafficlab.genetic.strategy import StrategyContext, make_strategy_context
from trafficlab.models.registry import BestModel, load_best_model, render_best_model
from trafficlab.pcapng import parse_pcapng_bytes
from trafficlab.trace import CaptureMetadata, TraceEvent, normalize_reference, parse_capture_metadata

_MANIFEST = "manifest.json"
_INDEX = "index.json"
_SCHEMA = 1
_HEX40 = re.compile(r"[0-9a-f]{40}", flags=re.ASCII)
_HEX64 = re.compile(r"[0-9a-f]{64}", flags=re.ASCII)
_TEMP_SUFFIXES = (".tmp", ".partial", ".swp")


@dataclass(frozen=True, slots=True)
class AuditResult:
    bundle: Path
    run_directory: Path
    manifest_sha256: str
    file_count: int


@dataclass(frozen=True, slots=True)
class _Entry:
    path: str
    size: int
    sha256: str
    owner: str
    lineage: object


@dataclass(frozen=True, slots=True)
class _Issue(Exception):
    kind: str
    affected: str
    detail: str
    action: str


def _fail(kind: str, affected: str, detail: str, action: str) -> NoReturn:
    raise _Issue(kind, affected, detail, action)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        + b"\n"
    )


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _read_regular(path: Path, *, affected: str) -> bytes:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        _fail(
            "artifact_missing",
            affected,
            f"{affected} is missing",
            "restore the exact retained artifact before publication",
        )
    except OSError as error:
        _fail("artifact_corrupt", affected, f"could not inspect {affected}: {error}", "repair the retained artifact")
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        _fail(
            "artifact_foreign", affected, f"{affected} must be a regular non-symlink file", "replace the foreign entry"
        )
    try:
        return path.read_bytes()
    except OSError as error:
        _fail("artifact_corrupt", affected, f"could not read {affected}: {error}", "repair the retained artifact")


def _json(content: bytes, *, name: str, canonical: bool) -> dict[str, object]:
    try:
        parsed = json.loads(content.decode("utf-8"), object_pairs_hook=_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        _fail("artifact_corrupt", name, f"{name} is not strict UTF-8 JSON: {error}", "restore canonical retained JSON")
    if type(parsed) is not dict:
        _fail("artifact_corrupt", name, f"{name} must contain one JSON object", "restore canonical retained JSON")
    document = cast(dict[str, object], parsed)
    if canonical and _canonical(document) != content:
        _fail("artifact_corrupt", name, f"{name} is not canonical JSON", "restore canonical retained JSON")
    return document


def _exact(value: object, keys: tuple[str, ...], *, name: str) -> dict[str, object]:
    if type(value) is not dict or set(cast(dict[str, object], value)) != set(keys):
        _fail(
            "artifact_corrupt",
            name,
            f"{name} must contain exactly {', '.join(keys)}",
            "restore the canonical evidence index",
        )
    return cast(dict[str, object], value)


def _string(value: object, *, name: str) -> str:
    if type(value) is not str or not cast(str, value).strip():
        _fail("artifact_corrupt", name, f"{name} must be a nonempty string", "restore the canonical evidence index")
    return cast(str, value)


def _integer(value: object, *, name: str, minimum: int = 0) -> int:
    if type(value) is not int or cast(int, value) < minimum:
        _fail(
            "artifact_corrupt",
            name,
            f"{name} must be an integer at least {minimum}",
            "restore the canonical evidence index",
        )
    return cast(int, value)


def _relative(value: object, *, name: str) -> str:
    text = _string(value, name=name)
    path = PurePosixPath(text)
    if path.is_absolute() or "\\" in text or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        _fail(
            "artifact_foreign",
            name,
            f"{name} must be a safe bundle-relative POSIX path",
            "restore the canonical evidence index",
        )
    return path.as_posix()


def _directory(value: object, *, name: str) -> Path:
    if not isinstance(value, Path):
        raise TypeError(f"{name} must be a pathlib.Path")
    try:
        mode = value.lstat().st_mode
    except FileNotFoundError:
        _fail("artifact_missing", name, f"{name} is missing", "restore the retained candidate directory")
    except OSError as error:
        _fail("artifact_corrupt", name, f"could not inspect {name}: {error}", "repair the local filesystem entry")
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        _fail(
            "artifact_foreign", name, f"{name} must be a regular non-symlink directory", "replace the foreign directory"
        )
    return value.resolve()


def _inside(path: Path, root: Path, *, name: str) -> None:
    try:
        path.relative_to(root)
    except ValueError:
        _fail(
            "artifact_foreign",
            name,
            f"{name} must remain within the selected repository",
            "audit only a retained local candidate",
        )


def _temporary(relative: str) -> bool:
    return any(part.startswith(".") or part.endswith(_TEMP_SUFFIXES) for part in PurePosixPath(relative).parts)


def _files(root: Path, *, include_manifest: bool) -> dict[str, Path]:
    found: dict[str, Path] = {}

    def visit(directory: Path) -> None:
        try:
            children = tuple(sorted(directory.iterdir(), key=lambda item: item.name))
        except OSError as error:
            _fail(
                "artifact_corrupt",
                "candidate accepted evidence bundle",
                f"could not enumerate evidence: {error}",
                "repair the candidate",
            )
        for child in children:
            relative = child.relative_to(root).as_posix()
            try:
                mode = child.lstat().st_mode
            except OSError as error:
                _fail("artifact_corrupt", relative, f"could not inspect {relative}: {error}", "repair the candidate")
            if stat.S_ISLNK(mode):
                _fail(
                    "artifact_foreign",
                    relative,
                    f"{relative} must not be a symlink",
                    "replace the symlink with retained evidence",
                )
            if stat.S_ISDIR(mode):
                if _temporary(relative):
                    _fail(
                        "artifact_foreign",
                        relative,
                        f"temporary directory {relative} is not accepted",
                        "remove the temporary entry",
                    )
                visit(child)
            elif stat.S_ISREG(mode):
                if _temporary(relative):
                    _fail(
                        "artifact_foreign",
                        relative,
                        f"temporary file {relative} is not accepted",
                        "remove the temporary entry",
                    )
                if include_manifest or relative != _MANIFEST:
                    found[relative] = child
            else:
                _fail(
                    "artifact_foreign",
                    relative,
                    f"{relative} must be a regular file",
                    "replace the foreign filesystem entry",
                )

    visit(root)
    return found


def _entries(content: bytes) -> tuple[_Entry, ...]:
    document = _exact(_json(content, name=_MANIFEST, canonical=True), ("files", "schema_version"), name=_MANIFEST)
    if _integer(document["schema_version"], name="manifest schema_version", minimum=1) != _SCHEMA:
        _fail(
            "scientific_semantics_incompatible",
            _MANIFEST,
            "manifest schema is incompatible",
            "rebuild the candidate with the current auditor",
        )
    values = document["files"]
    if type(values) is not list:
        _fail("artifact_corrupt", _MANIFEST, "manifest files must be an array", "restore the canonical manifest")
    parsed: list[_Entry] = []
    seen: set[str] = set()
    for value in cast(list[object], values):
        entry = _exact(value, ("lineage", "owner", "path", "sha256", "size"), name="manifest file entry")
        relative = _relative(entry["path"], name="manifest file path")
        if relative == _MANIFEST or relative in seen:
            _fail(
                "artifact_corrupt",
                _MANIFEST,
                f"manifest path {relative!r} is duplicated or recursive",
                "restore the canonical manifest",
            )
        seen.add(relative)
        sha256 = _string(entry["sha256"], name=f"manifest SHA-256 for {relative}")
        if _HEX64.fullmatch(sha256) is None:
            _fail(
                "artifact_corrupt",
                _MANIFEST,
                f"manifest SHA-256 for {relative} is invalid",
                "restore the canonical manifest",
            )
        parsed.append(
            _Entry(
                relative,
                _integer(entry["size"], name=f"manifest size for {relative}"),
                sha256,
                _string(entry["owner"], name=f"manifest owner for {relative}"),
                entry["lineage"],
            )
        )
    if tuple(entry.path for entry in parsed) != tuple(sorted(seen)):
        _fail(
            "artifact_corrupt", _MANIFEST, "manifest file entries must be path-sorted", "restore the canonical manifest"
        )
    return tuple(parsed)


def write_manifest(candidate: Path, ownership: Mapping[str, str], lineage: Mapping[str, object]) -> Path:
    """Write the canonical manifest for a completed local candidate tree."""

    root = _directory(candidate, name="candidate")
    files = _files(root, include_manifest=False)
    if set(ownership) != set(files) or set(lineage) != set(files):
        raise ValueError("ownership and lineage keys must equal the regular-file inventory")
    entries: list[dict[str, object]] = []
    for relative, path in sorted(files.items()):
        owner = ownership[relative]
        if type(owner) is not str or not owner.strip():
            raise ValueError(f"manifest owner for {relative} must be a nonempty string")
        content = _read_regular(path, affected=relative)
        entries.append(
            {
                "lineage": lineage[relative],
                "owner": owner,
                "path": relative,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        )
    manifest = root / _MANIFEST
    manifest.write_bytes(_canonical({"files": entries, "schema_version": _SCHEMA}))
    return manifest


def _verify_inventory(root: Path, manifest: bytes) -> tuple[_Entry, ...]:
    entries = _entries(manifest)
    actual = _files(root, include_manifest=False)
    expected = {entry.path: entry for entry in entries}
    for relative in sorted(expected):
        if relative not in actual:
            _fail(
                "artifact_missing",
                relative,
                f"{relative} is missing from the retained bundle",
                "restore the exact retained artifact",
            )
    for relative in sorted(set(actual) - set(expected)):
        _fail(
            "artifact_foreign",
            relative,
            f"{relative} is not listed by the manifest",
            "remove the unlisted artifact and rebuild the manifest",
        )
    for relative, entry in sorted(expected.items()):
        content = _read_regular(actual[relative], affected=relative)
        if len(content) != entry.size or hashlib.sha256(content).hexdigest() != entry.sha256:
            _fail(
                "artifact_corrupt",
                relative,
                f"{relative} does not match its manifest identity",
                "restore the exact retained artifact",
            )
    return entries


def _owner(relative: str) -> str:
    parts = PurePosixPath(relative).parts
    if relative == _INDEX:
        return "study-index"
    if len(parts) == 3 and parts[0] == "runs" and parts[2] in study.ARTIFACT_NAMES:
        return f"run:{parts[1]}"
    _fail("artifact_foreign", relative, f"{relative} has no documented owner", "rebuild the candidate inventory")


def _lineage(relative: str) -> dict[str, str]:
    parts = PurePosixPath(relative).parts
    if relative == _INDEX:
        return {"relation": "study-index"}
    if len(parts) == 3 and parts[0] == "runs" and parts[2] in study.ARTIFACT_NAMES:
        return {"relation": parts[2], "run": parts[1]}
    _fail("artifact_foreign", relative, f"{relative} has no documented lineage", "rebuild the candidate inventory")


def _metadata(index: dict[str, object], entries: tuple[_Entry, ...]) -> None:
    ownership = index["ownership"]
    lineage = index["lineage"]
    if type(ownership) is not dict or type(lineage) is not dict:
        _fail(
            "artifact_corrupt",
            _INDEX,
            "ownership and lineage must be JSON objects",
            "restore the canonical evidence index",
        )
    expected_owners = {entry.path: entry.owner for entry in entries}
    expected_lineage = {entry.path: entry.lineage for entry in entries}
    if ownership != expected_owners or lineage != expected_lineage:
        _fail(
            "artifact_foreign",
            _INDEX,
            "index ownership or lineage does not match the manifest",
            "restore matching manifest and index",
        )
    for relative, owner in expected_owners.items():
        if owner != _owner(relative) or expected_lineage[relative] != _lineage(relative):
            _fail(
                "artifact_foreign",
                relative,
                f"{relative} has invalid owner or lineage",
                "restore documented ownership and lineage",
            )


def _identity(content: bytes) -> dict[str, object]:
    return {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}


def _mean(scores: Sequence[dict[str, object]]) -> dict[str, object]:
    if not scores:
        _fail("artifact_corrupt", _INDEX, "report arithmetic requires scores", "restore the canonical evidence index")
    methods = [cast(dict[str, object], score["methods"]) for score in scores]
    return {
        "aggregate": fmean(cast(float, score["aggregate"]) for score in scores),
        "methods": {
            method: fmean(cast(float, values[method]) for values in methods) for method in study.PUBLISHED_METHOD_ORDER
        },
    }


@dataclass(frozen=True, slots=True)
class _Evidence:
    config: ExperimentConfig
    context: StrategyContext
    metadata: CaptureMetadata
    contents: Mapping[str, bytes]
    reference: tuple[TraceEvent, ...]
    generated: tuple[TraceEvent, ...]
    checkpoint: CheckpointState
    best_model: BestModel
    comparison: ComparisonResult


@dataclass(frozen=True, slots=True)
class _Run:
    name: str
    directory: Path
    evidence: _Evidence
    winner: dict[str, object]
    held_out: dict[str, object]
    published: dict[str, object]
    training: float


def _strict_artifacts(contents: Mapping[str, bytes], *, name: str) -> None:
    try:
        tomllib.loads(contents["experiment.toml"].decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        _fail(
            "artifact_corrupt",
            f"{name}/experiment.toml",
            f"experiment.toml is not strict TOML: {error}",
            "restore canonical configuration",
        )
    for artifact, canonical in (
        ("capture.json", False),
        ("checkpoint.json", True),
        ("best_model.json", True),
        ("similarity.json", True),
    ):
        _json(contents[artifact], name=f"{name}/{artifact}", canonical=canonical)
    try:
        lines = contents["run.log"].decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        _fail(
            "artifact_corrupt", f"{name}/run.log", f"run.log must be UTF-8 JSONL: {error}", "restore canonical run log"
        )
    if not lines or not contents["run.log"].endswith(b"\n"):
        _fail(
            "artifact_corrupt",
            f"{name}/run.log",
            "run.log must have newline-terminated records",
            "restore canonical run log",
        )
    for line in lines:
        _json(line.encode("utf-8"), name=f"{name}/run.log", canonical=False)


def _load_relocated_evidence(directory: Path, *, name: str) -> _Evidence:
    try:
        entries = tuple(directory.iterdir())
    except OSError as error:
        _fail(
            "artifact_corrupt",
            f"run {name}",
            f"could not enumerate run artifacts: {error}",
            "repair retained run evidence",
        )
    if {entry.name for entry in entries} != set(study.ARTIFACT_NAMES):
        _fail(
            "artifact_foreign",
            f"run {name}",
            "run must retain exactly the documented nine artifacts",
            "restore the complete retained run",
        )
    contents = {
        artifact: _read_regular(directory / artifact, affected=f"{name}/{artifact}")
        for artifact in study.ARTIFACT_NAMES
    }
    pair = load_configuration_pair(directory / "experiment.toml")
    if contents["experiment.toml"] != render_effective_config(pair.portable):
        _fail(
            "artifact_corrupt",
            f"{name}/experiment.toml",
            "portable configuration is not canonical",
            "restore the canonical portable configuration",
        )
    config = pair.realized
    capture_path = directory / "capture.json"
    reference_path = directory / "reference.pcapng"
    inspection = validate_capture_pair(capture_path, reference_path, deadline=None)
    metadata = parse_capture_metadata(contents["capture.json"], source=capture_path)
    captured = parse_pcapng_bytes(contents["reference.pcapng"], metadata, source=reference_path)
    reference, window = normalize_reference(captured)
    if inspection.packet_count != len(captured):
        _fail(
            "artifact_corrupt",
            f"run {name}",
            "capture packet count differs from parsed reference evidence",
            "restore the matching capture pair",
        )
    context = make_strategy_context(
        config,
        reference,
        window,
        directory,
        experiment_identity=ContentIdentity(
            size=len(contents["experiment.toml"]), sha256=hashlib.sha256(contents["experiment.toml"]).hexdigest()
        ),
        reference_identity=ContentIdentity(
            size=len(contents["reference.pcapng"]), sha256=hashlib.sha256(contents["reference.pcapng"]).hexdigest()
        ),
        capture_identity=ContentIdentity(
            size=len(contents["capture.json"]), sha256=hashlib.sha256(contents["capture.json"]).hexdigest()
        ),
    )
    checkpoint = parse_checkpoint(contents["checkpoint.json"], context.compatibility)
    if contents["ga_history.csv"] != render_history_csv(checkpoint):
        _fail(
            "artifact_corrupt",
            f"{name}/ga_history.csv",
            "history is not the exact checkpoint projection",
            "restore matching checkpoint and history",
        )
    best = load_best_model(contents["best_model.json"], source=directory / "best_model.json")
    if contents["best_model.json"] != render_best_model(best):
        _fail(
            "artifact_corrupt",
            f"{name}/best_model.json",
            "best model is not canonical",
            "restore canonical best-model evidence",
        )
    generated = parse_pcapng_bytes(contents["generated.pcapng"], metadata, source=directory / "generated.pcapng")
    comparison = parse_comparison_result(contents["similarity.json"])
    if contents["similarity.json"] != render_comparison_result(comparison):
        _fail(
            "artifact_corrupt",
            f"{name}/similarity.json",
            "similarity result is not canonical",
            "restore canonical comparison evidence",
        )
    return _Evidence(config, context, metadata, contents, reference, generated, checkpoint, best, comparison)


def _run(bundle: Path, value: object, *, environment: dict[str, object]) -> _Run:
    document = _exact(
        value,
        ("artifact_identities", "directory", "evaluation_window", "final", "name", "repeat", "winner"),
        name="run index record",
    )
    name = _string(document["name"], name="run name")
    relative = _relative(document["directory"], name=f"run directory for {name}")
    parts = PurePosixPath(relative).parts
    if len(parts) != 2 or parts != ("runs", name):
        _fail(
            "artifact_foreign",
            f"run {name}",
            "run directory must be runs/<name>",
            "restore the canonical evidence index",
        )
    directory = bundle.joinpath(*parts)
    _directory(directory, name=f"run {name}")
    _integer(document["repeat"], name=f"repeat for {name}", minimum=1)
    try:
        evidence = _load_relocated_evidence(directory, name=name)
        _strict_artifacts(evidence.contents, name=name)
        checkpoint = _json(evidence.contents["checkpoint.json"], name=f"{name}/checkpoint.json", canonical=True)
        best = _json(evidence.contents["best_model.json"], name=f"{name}/best_model.json", canonical=True)
        if checkpoint.get("scientific_artifact_schema") != 2 or best.get("scientific_artifact_schema") != 2:
            _fail(
                "scientific_semantics_incompatible",
                f"run {name}",
                "checkpoint and best model must use schema 2",
                "recreate the retained run under schema 2",
            )
        config = evidence.config
        if config.run.directory != directory:
            _fail(
                "artifact_foreign",
                f"run {name}",
                "effective run directory does not resolve to retained evidence",
                "restore its config pair",
            )
        if config.target.image != environment["target_image"] or config.capture.image != environment["capture_image"]:
            _fail(
                "artifact_foreign",
                f"run {name}",
                "configuration image controls disagree with environment",
                "restore matching retained environment evidence",
            )
        loaded = cast(study._LoadedRunEvidence, evidence)  # pyright: ignore[reportPrivateUsage]
        candidate = study._checkpoint_winner(loaded)  # pyright: ignore[reportPrivateUsage]
        final_seed = config.run.final_seed
        if final_seed != 97:
            _fail(
                "scientific_semantics_incompatible",
                f"run {name}",
                "final seed must equal 97",
                "recreate the retained run under frozen controls",
            )
        held_trial = study._sole_final_trial(  # pyright: ignore[reportPrivateUsage]
            evaluate_final(candidate, validate_evaluation_context(evidence.context.evaluation), final_seed)
        )
        science = study._reconstruct_science(  # pyright: ignore[reportPrivateUsage]
            loaded, held_trial, generated_path=directory / "generated.pcapng"
        )
        winner = cast(dict[str, object], study._winner(evidence.checkpoint, evidence.best_model))  # pyright: ignore[reportPrivateUsage]
        held = cast(dict[str, object], study._score_from_trial(science.held_out))  # pyright: ignore[reportPrivateUsage]
        published = cast(dict[str, object], study._score_from_comparison(science.published))  # pyright: ignore[reportPrivateUsage]
    except _Issue:
        raise
    except TrafficlabError:
        raise
    except (OSError, TypeError, ValueError) as error:
        _fail(
            "artifact_foreign",
            f"run {name}",
            f"run reconstruction failed: {error}",
            "restore matching retained artifacts and lineage",
        )
    identities = {artifact: _identity(evidence.contents[artifact]) for artifact in study.ARTIFACT_NAMES}
    final = {"held_out": held, "published": published, "seed": final_seed}
    if (
        document["artifact_identities"] != identities
        or document["evaluation_window"] != config.genetic.population_size * len(config.genetic.trial_seeds)
        or document["winner"] != winner
        or document["final"] != final
    ):
        _fail(
            "artifact_foreign",
            f"run {name}",
            "index does not match reconstructed run artifacts or final controls",
            "restore matching retained index evidence",
        )
    return _Run(name, directory, evidence, winner, held, published, cast(float, winner["selection_fitness"]))


def _environment(value: object, *, repository: Path) -> dict[str, object]:
    document = _exact(
        value,
        (
            "capture_image",
            "python_implementation",
            "python_version",
            "scientific_artifact_schema",
            "source_commit",
            "source_tree",
            "target_image",
            "uv_lock_sha256",
        ),
        name="environment",
    )
    if document["scientific_artifact_schema"] != 2:
        _fail(
            "scientific_semantics_incompatible",
            "environment",
            "environment must record artifact schema 2",
            "rebuild under current scientific semantics",
        )
    if document["python_implementation"] != "CPython" or document["python_version"] != platform.python_version():
        _fail(
            "scientific_semantics_incompatible",
            "environment",
            "environment CPython patch differs from this audit",
            "audit with the recorded CPython patch",
        )
    for field, pattern in (("source_commit", _HEX40), ("source_tree", _HEX64), ("uv_lock_sha256", _HEX64)):
        if pattern.fullmatch(_string(document[field], name=f"environment {field}")) is None:
            _fail(
                "artifact_corrupt",
                "environment",
                f"environment {field} is not canonical hexadecimal evidence",
                "restore the environment record",
            )
    _string(document["target_image"], name="environment target_image")
    _string(document["capture_image"], name="environment capture_image")
    lock = _read_regular(repository / "uv.lock", affected="uv.lock")
    if hashlib.sha256(lock).hexdigest() != document["uv_lock_sha256"]:
        _fail(
            "artifact_foreign",
            "environment",
            "environment uv.lock identity differs from the relocated repository",
            "audit with the matching locked repository",
        )
    return document


def _report(index: dict[str, object], runs: tuple[_Run, ...]) -> None:
    natural = _exact(index["natural_variation"], ("mean", "pairs"), name="natural variation")
    summary = _exact(index["summary"], ("held_out_mean", "published_mean", "training_mean"), name="report summary")
    pairs: list[dict[str, object]] = []
    scores: list[dict[str, object]] = []
    for left_index, left in enumerate(runs):
        for right in runs[left_index + 1 :]:
            try:
                score = cast(
                    dict[str, object],
                    study._symmetric_reference_score(  # pyright: ignore[reportPrivateUsage]
                        left.evidence.reference, right.evidence.reference, left.evidence.config.similarity
                    ),
                )
            except (TrafficlabError, TypeError, ValueError) as error:
                _fail(
                    "metric_infeasible",
                    "natural variation",
                    f"natural variation reconstruction failed: {error}",
                    "restore compatible references and settings",
                )
            pairs.append({"left": left.name, "right": right.name, "score": score})
            scores.append(score)
    expected_summary = {
        "held_out_mean": _mean([run.held_out for run in runs]),
        "published_mean": _mean([run.published for run in runs]),
        "training_mean": fmean(run.training for run in runs),
    }
    if natural["pairs"] != pairs or natural["mean"] != _mean(scores) or summary != expected_summary:
        _fail(
            "artifact_foreign",
            "report arithmetic",
            "natural variation or train/held-out report arithmetic does not reconstruct",
            "restore the report index from retained evidence",
        )


def audit_bundle(bundle: Path, *, repository: Path) -> AuditResult:
    """Read-only exact-inventory audit of a candidate accepted evidence bundle."""

    try:
        root = _directory(bundle, name="candidate bundle")
        repository_root = _directory(repository, name="repository")
        _inside(root, repository_root, name="candidate bundle")
        manifest = _read_regular(root / _MANIFEST, affected=_MANIFEST)
        entries = _verify_inventory(root, manifest)
        index = _exact(
            _json(_read_regular(root / _INDEX, affected=_INDEX), name=_INDEX, canonical=True),
            ("environment", "lineage", "natural_variation", "ownership", "runs", "schema_version", "summary"),
            name=_INDEX,
        )
        if _integer(index["schema_version"], name="index schema_version", minimum=1) != _SCHEMA:
            _fail(
                "scientific_semantics_incompatible",
                _INDEX,
                "evidence index schema is incompatible",
                "rebuild the candidate with the current auditor",
            )
        _metadata(index, entries)
        environment = _environment(index["environment"], repository=repository_root)
        values = index["runs"]
        if type(values) is not list or len(values) != 3:
            _fail(
                "artifact_corrupt",
                _INDEX,
                "index must retain exactly three natural-variation runs",
                "restore the canonical evidence index",
            )
        runs = tuple(_run(root, value, environment=environment) for value in cast(list[object], values))
        if tuple(run.name for run in runs) != ("short-r1", "short-r2", "short-r3"):
            _fail(
                "artifact_foreign",
                _INDEX,
                "runs must be ordered short-r1 through short-r3",
                "restore the canonical evidence index",
            )
        _report(index, runs)
        return AuditResult(root, runs[0].directory, hashlib.sha256(manifest).hexdigest(), len(entries))
    except _Issue as issue:
        raise TrafficlabError(
            f"Validation Study bundle audit failed: {issue.detail}",
            corrective_action=issue.action,
            failure_outcome=FailureOutcome(
                issue.kind, "publication", issue.detail, issue.affected, "not_published", issue.action, "primary"
            ),
        ) from issue
    except TrafficlabError as error:
        if error.failure_outcome is not None:
            raise
        action = "preserve the candidate and restore matching retained scientific evidence"
        raise TrafficlabError(
            f"Validation Study bundle audit failed: {error}",
            corrective_action=action,
            failure_outcome=FailureOutcome(
                "artifact_foreign",
                "publication",
                str(error),
                "candidate accepted evidence bundle",
                "not_published",
                action,
                "primary",
            ),
        ) from error
    except (OSError, TypeError, UnicodeDecodeError, ValueError, tomllib.TOMLDecodeError) as error:
        action = "preserve the candidate and restore canonical retained evidence"
        raise TrafficlabError(
            f"Validation Study bundle audit failed: {error}",
            corrective_action=action,
            failure_outcome=FailureOutcome(
                "artifact_corrupt",
                "publication",
                str(error),
                "candidate accepted evidence bundle",
                "not_published",
                action,
                "primary",
            ),
        ) from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="audit_validation_study.py", description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--repository", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        parsed = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as error:
        return int(error.code) if error.code is not None else 0
    try:
        result = audit_bundle(parsed.bundle, repository=parsed.repository)
    except TrafficlabError as error:
        print(f"validation-study-audit: {error}; {error.corrective_action}", file=sys.stderr)
        return error.exit_code
    print(f"validation-study-audit: accepted {result.file_count} retained files at {result.bundle}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
