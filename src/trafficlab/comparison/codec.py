"""Traffic comparison codec ownership."""

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from pydantic import (
    ValidationError,
)

from trafficlab.common.compatibility import ContentIdentity, identify_bytes
from trafficlab.common.config import SimilarityConfig
from trafficlab.common.errors import (
    TrafficlabError,
    attach_failure_outcome,
)
from trafficlab.comparison.diagnostics import diagnostic_discriminator
from trafficlab.comparison.schema import (
    ComparisonResult,
    PublishedComparisonResult,
    operational_comparison_result,
    published_comparison_result,
)


def _reject_cross_key_diagnostics(value: object) -> None:
    if type(value) is not dict:
        return
    methods = cast(dict[str, object], value).get("methods")
    if type(methods) is not dict:
        return
    for name, method in cast(dict[str, object], methods).items():
        if type(method) is not dict:
            continue
        diagnostics = cast(dict[str, object], method).get("diagnostics")
        tag = diagnostic_discriminator(diagnostics)
        if tag is not None and tag != name:
            raise ValueError(f"{name} diagnostics use the wrong method discriminator")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"duplicate JSON key: {key}")
        document[key] = value
    return document


def parse_comparison_result(content: bytes) -> ComparisonResult:
    """Parse strict UTF-8 JSON bytes into the immutable result type."""
    try:
        text = content.decode("utf-8")
        document = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid JSON constant: {value}")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid similarity JSON: {error}") from error
    _reject_cross_key_diagnostics(document)
    try:
        published = PublishedComparisonResult.model_validate(document)
    except ValidationError as error:
        first = error.errors()[0]
        field = ".".join(str(part) for part in first["loc"])
        if field.endswith("observation_window_seconds"):
            raise ValueError("every method diagnostic observation window must be a finite positive float") from error
        if field.endswith("reference_count") and first["type"] == "int_type":
            raise ValueError("reference_count must be an integer") from error
        raise ValueError(f"invalid comparison result {field}: {first['msg']}") from error
    return operational_comparison_result(published)


def canonical_comparison_bytes(result: ComparisonResult) -> bytes:
    published = published_comparison_result(result)
    content = (
        json.dumps(published.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")
    reparsed = PublishedComparisonResult.model_validate(json.loads(content.decode("utf-8")))
    if reparsed != published:
        raise ValueError("canonical similarity rendering changed the validated comparison result")
    return content


def render_comparison_result(result: ComparisonResult) -> bytes:
    """Render one complete result as deterministic sorted compact JSON."""
    return canonical_comparison_bytes(result)


def load_comparison_result(path: Path) -> ComparisonResult:
    """Load and strictly validate one similarity artifact."""
    try:
        content = path.read_bytes()
    except OSError as error:
        raise TrafficlabError(
            f"could not read similarity artifact {path}: {error}",
            corrective_action="verify similarity.json exists and is readable",
        ) from error
    try:
        return parse_comparison_result(content)
    except ValueError as error:
        raise TrafficlabError(
            f"invalid similarity artifact {path}: {error}",
            corrective_action="rerun comparison to publish a valid similarity.json",
        ) from error


def sha256_bytes(content: bytes) -> str:
    """Return the lowercase SHA-256 identity of exact bytes."""
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    """Return the SHA-256 identity of one exact file without loading it all at once."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise TrafficlabError(
            f"could not hash comparison input {path}: {error}",
            corrective_action=f"verify {path.name} exists and is readable",
        ) from error
    return digest.hexdigest()


def read_comparison_input(path: Path, *, kind: str, corrective_action: str) -> bytes:
    """Read one comparison input exactly once with its artifact-specific error."""
    try:
        return path.read_bytes()
    except FileNotFoundError as error:
        raise attach_failure_outcome(
            TrafficlabError(
                f"could not read {kind} {path}: {error}",
                corrective_action=corrective_action,
            ),
            kind="artifact_missing",
            stage="compare",
            affected_evidence=path.name,
            evidence_state="not_published",
        ) from error
    except OSError as error:
        raise attach_failure_outcome(
            TrafficlabError(
                f"could not read {kind} {path}: {error}",
                corrective_action=corrective_action,
            ),
            kind="artifact_corrupt",
            stage="compare",
            affected_evidence=path.name,
            evidence_state="preserved",
        ) from error


def similarity_settings_sha256(settings: SimilarityConfig) -> str:
    """Hash only the effective similarity settings as sorted compact JSON."""
    content = json.dumps(
        settings.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256_bytes(content)


def similarity_settings_identity(settings: SimilarityConfig) -> ContentIdentity:
    """Identify the exact canonical effective similarity settings bytes."""
    content = json.dumps(
        settings.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return identify_bytes(content)
