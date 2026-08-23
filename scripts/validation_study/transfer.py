"""Transfer owner for Validation Study tooling."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from scripts.validation_study.common import (
    PRIMARY_ORDER,
    path_entry_exists,
    require,
    strict_int,
    validate_endpoint_url,
    validate_study_id,
)
from scripts.validation_study.workloads import workload_specs
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.json import render_json_document

if TYPE_CHECKING:
    from scripts.validation_study.common import JsonObject, WorkloadName
    from scripts.validation_study.workloads import WorkloadSpec

WORKLOADS: tuple[WorkloadName, ...] = ("short", "streaming", "bursty")

URL = "https://downloads.example.test/object.bin"

OBJECT_SIZE_BYTES = 4 * 1024 * 1024


def fixture_canonical_json(value: object) -> bytes:
    return render_json_document(value, ensure_ascii=False)


def _transfer_header(start: int, end: int) -> bytes:
    return (
        b"HTTP/1.1 206 Partial Content\r\n"
        + f"Content-Length: {end - start + 1}\r\n".encode("ascii")
        + f"Content-Range: bytes {start}-{end}/{OBJECT_SIZE_BYTES}\r\n\r\n".encode("ascii")
    )


def write_transfer_evidence(root: Path) -> bytes:
    bindings: list[tuple[str, str, str, int, int, int, str]] = [
        ("prerequisites", "00-prerequisites", "prerequisites", 0, 0, 0, "capability.headers")
    ]
    transfers = {spec.name: spec.transfers for spec in workload_specs(URL)}
    for _order, run_id, workload, _repeat in PRIMARY_ORDER:
        bindings.extend(
            (
                ("training", run_id, workload, index, start, end, filename)
                for index, (start, end, filename) in enumerate(transfers[workload])
            )
        )
    for workload in WORKLOADS:
        bindings.extend(
            (
                ("held_out", f"held-out-{workload}", workload, index, start, end, filename)
                for index, (start, end, filename) in enumerate(transfers[workload])
            )
        )
    capability_header = b""
    for scope, run_id, workload, transfer_index, start, end, filename in bindings:
        header = _transfer_header(start, end)
        if scope == "prerequisites":
            capability_header = header
        header_relative = f"headers/{scope}/{run_id}/{filename}"
        observation_relative = f"observations/{scope}/{run_id}/{filename}.json"
        header_path = root / header_relative
        header_path.parent.mkdir(parents=True, exist_ok=True)
        header_path.write_bytes(header)
        observation_path = root / observation_relative
        observation_path.parent.mkdir(parents=True, exist_ok=True)
        observation_path.write_bytes(
            fixture_canonical_json(
                {
                    "content_length": end - start + 1,
                    "content_range": f"bytes {start}-{end}/{OBJECT_SIZE_BYTES}",
                    "header_identity": identify_bytes(header).as_dict(),
                    "requested_end": end,
                    "requested_start": start,
                    "run_id": run_id,
                    "scope": scope,
                    "status": 206,
                    "transfer_index": transfer_index,
                    "workload": workload,
                }
            )
        )
    if not capability_header:
        raise ValueError("fixture transfer evidence must retain the prerequisite capability header")
    return capability_header


def _workload_url(workload: WorkloadSpec) -> str:
    urls = tuple((workload.argv[index + 1] for index, token in enumerate(workload.argv[:-1]) if token == "--url"))
    require(bool(urls) and len(set(urls)) == 1, "workload must contain one exact URL for every transfer")
    url = validate_endpoint_url(urls[0])
    require(workload in workload_specs(url), "workload must equal one exact Validation Study profile")
    return url


def prepare_transfer_scratch(
    repository_root: Path, study_id: str, run_id: str, workload: WorkloadSpec
) -> dict[str, tuple[Path, int]]:
    _workload_url(workload)
    require(re.fullmatch("[a-z0-9][a-z0-9-]{0,63}", run_id) is not None, "run ID must be a simple lowercase identifier")
    root = repository_root.resolve()
    mount_directory = root / "examples" / "validation_study" / ".study-work" / "mount" / validate_study_id(study_id)
    if path_entry_exists(mount_directory):
        mode = mount_directory.lstat().st_mode
        require(stat.S_ISDIR(mode) and (not stat.S_ISLNK(mode)), "study mount must be a regular directory")
    else:
        mount_directory.mkdir(parents=True, mode=493)
    mount_directory.chmod(493)
    prepared: dict[str, tuple[Path, int]] = {}
    for _start, _end, filename in workload.transfers:
        path = mount_directory / filename
        if path_entry_exists(path):
            mode = path.lstat().st_mode
            require(stat.S_ISREG(mode) and (not stat.S_ISLNK(mode)), f"scratch {filename} must be a regular file")
            path.unlink()
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 438)
        except FileExistsError as error:
            raise ValueError(f"scratch {filename} already exists") from error
        os.close(descriptor)
        path.chmod(438)
        metadata = path.lstat()
        require(
            stat.S_ISREG(metadata.st_mode) and stat.S_IMODE(metadata.st_mode) == 438,
            f"scratch {filename} must be an exclusive regular 0666 file",
        )
        prepared[filename] = (path, metadata.st_ino)
    evidence_parent = root / "examples" / "validation_study" / ".study-work" / "evidence" / study_id
    evidence_parent.mkdir(parents=True, exist_ok=True)
    evidence_directory = evidence_parent / run_id
    try:
        evidence_directory.mkdir()
    except FileExistsError as error:
        raise ValueError(f"transfer evidence directory already exists: {evidence_directory}") from error
    return prepared


def header_blocks(content: bytes) -> tuple[tuple[int, dict[str, list[str]]], ...]:
    require(bool(content), "transfer header must be nonempty")
    try:
        text = content.decode("iso-8859-1")
    except UnicodeDecodeError as error:
        raise ValueError("transfer header must use HTTP header bytes") from error
    raw_blocks = tuple(block for block in text.split("\r\n\r\n") if block)
    require(bool(raw_blocks), "transfer header must contain at least one response block")
    blocks: list[tuple[int, dict[str, list[str]]]] = []
    for raw_block in raw_blocks:
        lines = raw_block.split("\r\n")
        match = re.fullmatch("HTTP/1\\.1[ \\t]+(\\d{3})(?:[ \\t].*)?", lines[0])
        if match is None:
            raise ValueError("each response block must contain exactly one HTTP/1.1 status line")
        headers: dict[str, list[str]] = {}
        for line in lines[1:]:
            require(not line.upper().startswith("HTTP/"), "response block must not contain a duplicate status line")
            require(":" in line, "response header lines must contain a field name and value")
            name, value = line.split(":", 1)
            key = name.strip().lower()
            require(bool(key), "response header field name must be nonempty")
            headers.setdefault(key, []).append(value.strip())
        blocks.append((int(match.group(1)), headers))
    return tuple(blocks)


def singleton_header(headers: Mapping[str, list[str]], name: str) -> str:
    values = headers.get(name.lower(), [])
    require(len(values) == 1, f"final response must contain exactly one {name} header")
    return values[0]


def parse_transfer_header(
    content: bytes, *, initial_url: str, start: int, end: int, object_size_bytes: int
) -> tuple[int, int, str]:
    blocks = header_blocks(content)
    require(len(blocks) <= 4, "transfer header must contain at most three redirects")
    current_url = initial_url
    for status_code, headers in blocks[:-1]:
        require(300 <= status_code <= 399, "every response before the final block must be a redirect")
        location = singleton_header(headers, "Location")
        current_url = validate_endpoint_url(urljoin(current_url, location))
    status_code, final_headers = blocks[-1]
    require(status_code == 206, "final transfer response status must be exactly 206")
    content_range = singleton_header(final_headers, "Content-Range")
    expected_range = f"bytes {start}-{end}/{object_size_bytes}"
    require(content_range == expected_range, "final Content-Range must equal the exact requested range and total")
    length_text = singleton_header(final_headers, "Content-Length")
    require(re.fullmatch("[0-9]+", length_text) is not None, "final Content-Length must be an exact integer")
    content_length = int(length_text)
    require(content_length == end - start + 1, "final Content-Length must equal the requested range length")
    return (status_code, content_length, content_range)


def best_effort_archive(evidence_directory: Path, prepared: Mapping[str, tuple[Path, int]]) -> str | None:
    diagnostics: list[str] = []
    for filename, (scratch, _inode) in prepared.items():
        archive = evidence_directory / filename
        if path_entry_exists(archive):
            continue
        try:
            metadata = scratch.lstat()
            if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                diagnostics.append(f"{filename}: scratch is not a regular file")
                continue
            content = scratch.read_bytes()
            with archive.open("xb") as stream:
                stream.write(content)
            archive.chmod(384)
        except OSError as error:
            diagnostics.append(f"{filename}: {error}")
    return "; ".join(diagnostics) if diagnostics else None


def archive_transfer_evidence(
    repository_root: Path,
    study_id: str,
    run_id: str,
    workload: WorkloadSpec,
    prepared: Mapping[str, tuple[Path, int]],
    *,
    object_size_bytes: int,
) -> tuple[JsonObject, ...]:
    initial_url = _workload_url(workload)
    object_size_bytes = strict_int(object_size_bytes, name="object size", minimum=1)
    require(4 * 1024 * 1024 <= object_size_bytes <= 16 * 1024 * 1024, "object size must be from 4 MiB through 16 MiB")
    expected_names = tuple((filename for _start, _end, filename in workload.transfers))
    require(tuple(prepared) == expected_names, "prepared scratch must contain the exact workload header names")
    root = repository_root.resolve()
    evidence_directory = (
        root / "examples" / "validation_study" / ".study-work" / "evidence" / validate_study_id(study_id) / run_id
    )
    evidence_mode = evidence_directory.lstat().st_mode
    require(
        stat.S_ISDIR(evidence_mode) and (not stat.S_ISLNK(evidence_mode)),
        "transfer evidence directory must be prepared exclusively",
    )
    validated: list[tuple[int, int, str, Path, int, bytes, int, int, str]] = []
    try:
        for index, (start, end, filename) in enumerate(workload.transfers):
            scratch, inode = prepared[filename]
            expected_scratch = root / "examples" / "validation_study" / ".study-work" / "mount" / study_id / filename
            require(scratch == expected_scratch, f"scratch {filename} must use the exact study mount path")
            metadata = scratch.lstat()
            require(
                stat.S_ISREG(metadata.st_mode)
                and (not stat.S_ISLNK(metadata.st_mode))
                and (metadata.st_ino == inode)
                and (stat.S_IMODE(metadata.st_mode) == 438),
                f"scratch {filename} must preserve its exclusive regular 0666 inode",
            )
            content = scratch.read_bytes()
            after_read = scratch.lstat()
            require(after_read.st_ino == inode, f"scratch {filename} inode changed while reading")
            status_code, content_length, content_range = parse_transfer_header(
                content, initial_url=initial_url, start=start, end=end, object_size_bytes=object_size_bytes
            )
            archive = evidence_directory / filename
            require(not path_entry_exists(archive), f"header archive already exists: {archive}")
            validated.append(
                (index, start, filename, archive, inode, content, status_code, content_length, content_range)
            )
        responses: list[JsonObject] = []
        for index, start, filename, archive, inode, content, status_code, content_length, content_range in validated:
            with archive.open("xb") as stream:
                stream.write(content)
            archive.chmod(384)
            archived = archive.read_bytes()
            require(archived == content, f"header archive {filename} must preserve exact bytes")
            require(stat.S_IMODE(archive.lstat().st_mode) == 384, f"header archive {filename} must use mode 0600")
            end = start + content_length - 1
            responses.append(
                {
                    "transfer_index": index,
                    "requested_start": start,
                    "requested_end": end,
                    "status": status_code,
                    "content_length": content_length,
                    "content_range": content_range,
                    "header_archive_path": archive.relative_to(root).as_posix(),
                    "header_sha256": hashlib.sha256(archived).hexdigest(),
                    "scratch_precreate_mode": 438,
                    "archive_mode": 384,
                    "inode_preserved": True,
                }
            )
            scratch = prepared[filename][0]
            require(scratch.lstat().st_ino == inode, f"scratch {filename} inode changed before removal")
        for _index, _start, filename, _archive, _inode, _content, _status, _length, _range in validated:
            prepared[filename][0].unlink()
        return tuple(responses)
    except (OSError, ValueError) as error:
        best_effort_archive(evidence_directory, prepared)
        if isinstance(error, ValueError):
            raise
        raise ValueError(f"could not archive transfer evidence: {error}") from error
