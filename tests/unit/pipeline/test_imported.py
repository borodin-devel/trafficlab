# pyright: reportPrivateUsage=false
from __future__ import annotations

import builtins
import json
import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
import tomli_w

import trafficlab.pipeline.imported as imported_module
from trafficlab.artifacts.capture import CapturePublication
from trafficlab.capture.stage import CaptureResult
from trafficlab.capture.validation import CaptureInspection
from trafficlab.common.compatibility import identify_bytes, identify_file
from trafficlab.common.config_io import ConfigurationPair, render_effective_config
from trafficlab.common.errors import DeadlineExceededError, TrafficlabError
from trafficlab.common.scapy_io import RawNormalizationResult, normalize_raw_capture
from trafficlab.pipeline.imported import (
    ImportSource,
    discover_import_source,
    import_reference,
    run_imported_experiment,
)
from trafficlab.preflight.stage import run_preflight
from trafficlab.preflight.types import PreparedExperiment

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "data" / "import_run"


def _source_copy(tmp_path: Path, name: str = "classic-pcap-source") -> ImportSource:
    destination = tmp_path / "source"
    shutil.copytree(FIXTURES / name, destination)
    return discover_import_source(destination)


def _prepared(valid_config_data: dict[str, object], tmp_path: Path) -> tuple[Path, PreparedExperiment]:
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_text(tomli_w.dumps(valid_config_data), encoding="utf-8")
    return experiment_path, run_preflight(experiment_path, config_only=True)


def _stat_identity(path: Path) -> list[int]:
    status = path.stat(follow_symlinks=False)
    return [status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns]


def _run_bytes(run_directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in run_directory.iterdir() if path.is_file()}


def _records(run_directory: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (run_directory / "run.log").read_text().splitlines()]


def _write_records(run_directory: Path, records: list[dict[str, object]]) -> None:
    (run_directory / "run.log").write_text(
        "".join(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


@pytest.mark.parametrize("suffix", [".pcap", ".PCAP", ".pcapng", ".PCAPNG"])
def test_discover_import_source_accepts_exact_regular_pair(
    suffix: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    capture = source / f"traffic{suffix}"
    metadata = source / "capture.json"
    capture.write_bytes(b"capture")
    metadata.write_bytes(b"{}")
    monkeypatch.chdir(tmp_path)

    relative = discover_import_source(Path("source"))
    absolute = discover_import_source(source.resolve())

    expected = ImportSource(source.resolve(), capture.resolve(), metadata.resolve())
    assert relative == expected
    assert absolute == expected


@pytest.mark.parametrize("shape", ["missing", "file", "directory-symlink"])
def test_discover_import_source_rejects_non_real_directory(shape: str, tmp_path: Path) -> None:
    source = tmp_path / "source"
    if shape == "file":
        source.write_bytes(b"not a directory")
    elif shape == "directory-symlink":
        target = tmp_path / "target"
        target.mkdir()
        source.symlink_to(target, target_is_directory=True)

    with pytest.raises(TrafficlabError, match="real directory"):
        discover_import_source(source)


@pytest.mark.parametrize(
    ("entries", "counts"),
    [
        (("capture.json",), "captures=0, metadata=1, unexpected=0"),
        (("capture.json", "one.pcap", "two.pcapng"), "captures=2, metadata=1, unexpected=0"),
        (("Capture.json", "one.pcap"), "captures=1, metadata=0, unexpected=1"),
        (("capture.json", "one.pcap", "notes.txt"), "captures=1, metadata=1, unexpected=1"),
    ],
)
def test_discover_import_source_rejects_nonexact_inventory(
    entries: tuple[str, ...], counts: str, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in entries:
        (source / name).write_bytes(name.encode())

    with pytest.raises(TrafficlabError, match=counts):
        discover_import_source(source)


@pytest.mark.parametrize("kind", ["nested-directory", "capture-symlink", "metadata-symlink", "fifo"])
def test_discover_import_source_rejects_nonregular_direct_entry(kind: str, tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    capture = source / "traffic.pcap"
    metadata = source / "capture.json"
    capture.write_bytes(b"capture")
    metadata.write_bytes(b"{}")
    if kind == "nested-directory":
        (source / "nested").mkdir()
    elif kind == "capture-symlink":
        capture.unlink()
        capture.symlink_to(tmp_path / "outside.pcap")
    elif kind == "metadata-symlink":
        metadata.unlink()
        metadata.symlink_to(tmp_path / "outside.json")
    else:
        os.mkfifo(source / "unexpected")

    with pytest.raises(TrafficlabError, match=r"unexpected=[1-9]"):
        discover_import_source(source)


def test_import_reference_snapshots_normalizes_validates_and_publishes_once(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    _experiment_path, prepared = _prepared(valid_config_data, tmp_path)
    source_before = {
        path: (path.read_bytes(), path.stat(follow_symlinks=False))
        for path in (source.capture_path, source.metadata_path)
    }
    calls: list[tuple[str, Path, float | None]] = []
    real_load_metadata = imported_module.load_capture_metadata
    real_normalize = imported_module.normalize_raw_capture
    real_validate = imported_module.validate_capture_pair

    def tracked_load_metadata(path: Path) -> object:
        calls.append(("metadata", path, None))
        return real_load_metadata(path)

    def tracked_normalize(
        source_path: Path, destination: Path, *, deadline: float | None, clock: Callable[[], float]
    ) -> object:
        assert destination.parent.parent == prepared.run_directory
        calls.append(("normalize", source_path, deadline))
        return real_normalize(source_path, destination, deadline=deadline, clock=clock)

    def tracked_validate(
        metadata_path: Path, pcapng_path: Path, *, deadline: float | None, clock: Callable[[], float]
    ) -> object:
        calls.append(("validate", pcapng_path, deadline))
        return real_validate(metadata_path, pcapng_path, deadline=deadline, clock=clock)

    monkeypatch.setattr(imported_module, "load_capture_metadata", tracked_load_metadata)
    monkeypatch.setattr(imported_module, "normalize_raw_capture", tracked_normalize)
    monkeypatch.setattr(imported_module, "validate_capture_pair", tracked_validate)

    result = import_reference(source, prepared, clock=lambda: 10.0)

    assert result == CaptureResult(
        run_directory=prepared.run_directory,
        reference_path=prepared.run_directory / "reference.pcapng",
        packet_count=4,
        target_status=0,
        reused=False,
    )
    assert [name for name, _path, _deadline in calls[:3]] == ["metadata", "normalize", "validate"]
    assert {deadline for _name, _path, deadline in calls[1:]} == {70.0}
    assert (prepared.run_directory / "capture.json").read_bytes() == source.metadata_path.read_bytes()
    for path, (content, status) in source_before.items():
        assert path.read_bytes() == content
        current = path.stat(follow_symlinks=False)
        assert (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) == (
            status.st_dev,
            status.st_ino,
            status.st_size,
            status.st_mtime_ns,
        )
    assert not tuple(prepared.run_directory.glob(".import-reference.*"))

    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    imports = [record for record in records if record.get("event") == "reference_imported"]
    assert imports == [
        {
            "capture_identity": identify_file(prepared.run_directory / "capture.json").as_dict(),
            "event": "reference_imported",
            "experiment_identity": identify_bytes(render_effective_config(prepared.config)).as_dict(),
            "normalization_version": "scapy-raw-v1",
            "packet_count": 4,
            "path": str(prepared.run_directory / "reference.pcapng"),
            "reference_identity": identify_file(prepared.run_directory / "reference.pcapng").as_dict(),
            "reused": False,
            "source_capture_file_identity": _stat_identity(source.capture_path),
            "source_capture_identity": identify_file(source.capture_path).as_dict(),
            "source_capture_path": str(source.capture_path),
            "source_metadata_file_identity": _stat_identity(source.metadata_path),
            "source_metadata_identity": identify_file(source.metadata_path).as_dict(),
            "source_metadata_path": str(source.metadata_path),
            "stage": "capture",
        }
    ]


def test_import_reference_exact_retry_reuses_without_normalization(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    _experiment_path, prepared = _prepared(valid_config_data, tmp_path)
    first = import_reference(source, prepared, clock=lambda: 10.0)
    canonical_before = {
        name: (prepared.run_directory / name).read_bytes() for name in ("capture.json", "reference.pcapng")
    }

    def forbidden_normalization(*args: object, **kwargs: object) -> object:
        raise AssertionError(f"exact reuse normalized again: {args!r} {kwargs!r}")

    monkeypatch.setattr(imported_module, "normalize_raw_capture", forbidden_normalization)
    second = import_reference(source, prepared, clock=lambda: 20.0)

    assert first.reused is False
    assert second == replace(first, reused=True)
    assert {
        name: (prepared.run_directory / name).read_bytes() for name in ("capture.json", "reference.pcapng")
    } == canonical_before
    records = [record for record in _records(prepared.run_directory) if record.get("event") == "reference_imported"]
    assert len(records) == 2
    assert records[0]["reused"] is False
    assert records[1] == {**records[0], "reused": True}


@pytest.mark.parametrize(
    "mismatch",
    [
        "changed-source-capture",
        "changed-source-metadata",
        "replaced-source-path",
        "changed-effective-config",
        "changed-normalization-version",
        "changed-canonical-output",
        "missing-publication",
        "duplicate-publication",
        "contradictory-reuse",
    ],
)
def test_import_reference_reuse_mismatch_preserves_every_preexisting_run_byte(
    mismatch: str,
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_copy(tmp_path)
    _experiment_path, prepared = _prepared(valid_config_data, tmp_path)
    import_reference(source, prepared, clock=lambda: 10.0)

    if mismatch == "changed-source-capture":
        source.capture_path.write_bytes(source.capture_path.read_bytes() + b"changed")
    elif mismatch == "changed-source-metadata":
        source.metadata_path.write_text('{"interface":"eth0","target_mac":"02:42:ac:11:00:03"}\n', encoding="utf-8")
    elif mismatch == "replaced-source-path":
        content = source.capture_path.read_bytes()
        source.capture_path.unlink()
        source.capture_path.write_bytes(content)
    elif mismatch == "changed-effective-config":
        changed_capture = prepared.config.capture.model_copy(update={"total_timeout_seconds": 61.0})
        changed_config = prepared.config.model_copy(update={"capture": changed_capture})
        prepared = replace(prepared, config=changed_config)
        (prepared.run_directory / "experiment.toml").write_bytes(render_effective_config(changed_config))
    elif mismatch == "changed-normalization-version":
        monkeypatch.setattr(imported_module, "_NORMALIZATION_VERSION", "scapy-raw-v2")
    elif mismatch == "changed-canonical-output":
        normalize_raw_capture(
            FIXTURES / "noncanonical-pcapng-source" / "source.pcapng",
            prepared.run_directory / "replacement.pcapng",
            deadline=None,
        )
        os.replace(
            prepared.run_directory / "replacement.pcapng",
            prepared.run_directory / "reference.pcapng",
        )
    else:
        records = _records(prepared.run_directory)
        publication = next(record for record in records if record.get("event") == "reference_imported")
        if mismatch == "missing-publication":
            records.remove(publication)
        elif mismatch == "duplicate-publication":
            records.append(dict(publication))
        else:
            records.append({**publication, "reused": True, "packet_count": 999})
        _write_records(prepared.run_directory, records)

    before = _run_bytes(prepared.run_directory)
    with pytest.raises(TrafficlabError, match="exact imported-reference reuse"):
        import_reference(source, prepared, clock=lambda: 20.0)

    assert _run_bytes(prepared.run_directory) == before


@pytest.mark.parametrize("existing", ["metadata-only", "reference-only", "malformed-pair"])
def test_import_reference_incomplete_or_malformed_pair_is_nonmutating(
    existing: str, valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    source = _source_copy(tmp_path)
    _experiment_path, prepared = _prepared(valid_config_data, tmp_path)
    if existing != "reference-only":
        (prepared.run_directory / "capture.json").write_bytes(source.metadata_path.read_bytes())
    if existing != "metadata-only":
        (prepared.run_directory / "reference.pcapng").write_bytes(source.capture_path.read_bytes())
    before = _run_bytes(prepared.run_directory)

    with pytest.raises(TrafficlabError, match="exact imported-reference reuse"):
        import_reference(source, prepared, clock=lambda: 20.0)

    assert _run_bytes(prepared.run_directory) == before


@pytest.mark.parametrize("relationship", ["same", "run-inside-source", "source-inside-run", "aliased"])
def test_run_imported_experiment_rejects_source_run_overlap_before_creating_artifacts(
    relationship: str, valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    outer = tmp_path / "outer"
    outer.mkdir()
    source_root = outer if relationship in {"same", "run-inside-source"} else outer / "source"
    source_root.mkdir(exist_ok=True)
    shutil.copyfile(FIXTURES / "classic-pcap-source" / "source.pcap", source_root / "source.pcap")
    shutil.copyfile(FIXTURES / "classic-pcap-source" / "capture.json", source_root / "capture.json")
    if relationship == "same":
        run_directory = source_root
    elif relationship == "run-inside-source":
        run_directory = source_root / "run"
    elif relationship == "source-inside-run":
        run_directory = outer
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(source_root, target_is_directory=True)
        run_directory = alias
    cast(dict[str, object], valid_config_data["run"])["directory"] = str(run_directory)
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_text(tomli_w.dumps(valid_config_data), encoding="utf-8")
    before = {path.relative_to(source_root): path.read_bytes() for path in source_root.rglob("*") if path.is_file()}

    with pytest.raises(TrafficlabError, match="overlap"):
        run_imported_experiment(experiment_path, source_root)

    assert {
        path.relative_to(source_root): path.read_bytes() for path in source_root.rglob("*") if path.is_file()
    } == before


def test_run_imported_experiment_discovers_source_before_reading_config(tmp_path: Path) -> None:
    with pytest.raises(TrafficlabError, match="real directory"):
        run_imported_experiment(tmp_path / "missing.toml", tmp_path / "missing-source")


def test_run_imported_experiment_rejects_config_changed_before_authoritative_preflight(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_text(tomli_w.dumps(valid_config_data), encoding="utf-8")
    real_preflight = imported_module._config_only_preflight

    def changed_preflight(path: Path) -> PreparedExperiment:
        changed = dict(valid_config_data)
        changed_capture = dict(cast(dict[str, object], changed["capture"]))
        changed_capture["total_timeout_seconds"] = 61.0
        changed["capture"] = changed_capture
        path.write_text(tomli_w.dumps(changed), encoding="utf-8")
        return real_preflight(path)

    monkeypatch.setattr(imported_module, "_config_only_preflight", changed_preflight)

    with pytest.raises(TrafficlabError, match="changed during imported preflight"):
        run_imported_experiment(experiment_path, source.directory)


def test_run_imported_experiment_routes_post_preflight_import_failure_through_coordinator(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "source.pcap").write_bytes(b"not a capture")
    shutil.copyfile(FIXTURES / "classic-pcap-source" / "capture.json", source / "capture.json")
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_text(tomli_w.dumps(valid_config_data), encoding="utf-8")

    with pytest.raises(TrafficlabError, match="raw capture"):
        run_imported_experiment(experiment_path, source)

    run_directory = Path(cast(str, cast(dict[str, object], valid_config_data["run"])["directory"]))
    failures = [record for record in _records(run_directory) if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == "capture"


def test_real_import_acquisition_reaches_fit_without_subprocess_docker_or_scripts(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_text(tomli_w.dumps(valid_config_data), encoding="utf-8")

    def forbidden_subprocess(*args: object, **kwargs: object) -> object:
        raise AssertionError(f"import-run reached subprocess: {args!r} {kwargs!r}")

    real_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> Any:
        if name == "scripts" or name.startswith("scripts.") or name.startswith("trafficlab.capture.docker"):
            raise AssertionError(f"import-run reached forbidden module {name}")
        return real_import(name, globals, locals, fromlist, level)

    def stop_after_capture(_path: Path) -> object:
        raise TrafficlabError("fit sentinel", corrective_action="test completed acquisition")

    monkeypatch.setattr("subprocess.run", forbidden_subprocess)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(imported_module, "fit_experiment", stop_after_capture)

    with pytest.raises(TrafficlabError, match="fit sentinel"):
        run_imported_experiment(experiment_path, source.directory)

    run_directory = Path(cast(str, cast(dict[str, object], valid_config_data["run"])["directory"]))
    assert (run_directory / "reference.pcapng").is_file()
    assert len([record for record in _records(run_directory) if record.get("event") == "reference_imported"]) == 1


@pytest.mark.parametrize(
    "boundary",
    ["first-snapshot", "second-snapshot", "metadata", "normalize", "validate", "publish", "log"],
)
def test_import_reference_boundary_failures_clean_owned_temporary_and_preserve_authority(
    boundary: str,
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_copy(tmp_path)
    _experiment_path, prepared = _prepared(valid_config_data, tmp_path)
    run_before = _run_bytes(prepared.run_directory)
    error = TrafficlabError(f"{boundary} sentinel", corrective_action="test boundary")

    if boundary in {"first-snapshot", "second-snapshot"}:
        real_copy = imported_module._copy_snapshot
        calls = 0

        def fail_copy(
            source_path: Path,
            destination: Path,
            *,
            deadline: float,
            clock: Callable[[], float],
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == (1 if boundary == "first-snapshot" else 2):
                raise error
            real_copy(source_path, destination, deadline=deadline, clock=clock)

        monkeypatch.setattr(imported_module, "_copy_snapshot", fail_copy)
    else:
        attribute = {
            "metadata": "load_capture_metadata",
            "normalize": "normalize_raw_capture",
            "validate": "validate_capture_pair",
            "publish": "publish_capture_pair",
            "log": "append_run_log",
        }[boundary]

        def fail_boundary(*args: object, **kwargs: object) -> object:
            del args, kwargs
            raise error

        monkeypatch.setattr(imported_module, attribute, fail_boundary)

    with pytest.raises(TrafficlabError, match=f"{boundary} sentinel"):
        import_reference(source, prepared, clock=lambda: 10.0)

    assert not tuple(prepared.run_directory.glob(".import-reference.*"))
    if boundary == "log":
        assert (prepared.run_directory / "capture.json").is_file()
        assert (prepared.run_directory / "reference.pcapng").is_file()
        assert (prepared.run_directory / "run.log").read_bytes() == run_before["run.log"]
    else:
        assert _run_bytes(prepared.run_directory) == run_before


def test_import_reference_keyboard_interrupt_cleans_owned_temporary(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    _experiment_path, prepared = _prepared(valid_config_data, tmp_path)
    run_before = _run_bytes(prepared.run_directory)

    def interrupt(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise KeyboardInterrupt

    monkeypatch.setattr(imported_module, "normalize_raw_capture", interrupt)

    with pytest.raises(KeyboardInterrupt):
        import_reference(source, prepared, clock=lambda: 10.0)

    assert _run_bytes(prepared.run_directory) == run_before
    assert not tuple(prepared.run_directory.glob(".import-reference.*"))


def test_import_reference_uses_one_deadline_and_cleans_on_copy_expiry(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    source = _source_copy(tmp_path)
    _experiment_path, prepared = _prepared(valid_config_data, tmp_path)
    values = iter((10.0, 10.0, 71.0))

    with pytest.raises(DeadlineExceededError, match="total_timeout_seconds expired") as caught:
        import_reference(source, prepared, clock=lambda: next(values))

    assert caught.value.corrective_action == "increase capture.total_timeout_seconds and retry import-run"
    assert not tuple(prepared.run_directory.glob(".import-reference.*"))
    assert not (prepared.run_directory / "reference.pcapng").exists()


def test_import_reference_reuse_preserves_deadline_error_and_existing_pair(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    _experiment_path, prepared = _prepared(valid_config_data, tmp_path)
    import_reference(source, prepared, clock=lambda: 10.0)
    before = _run_bytes(prepared.run_directory)

    def expired(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise DeadlineExceededError("reuse deadline", corrective_action="increase timeout")

    monkeypatch.setattr(imported_module, "validate_capture_pair", expired)
    with pytest.raises(DeadlineExceededError, match="reuse deadline"):
        import_reference(source, prepared, clock=lambda: 20.0)

    assert _run_bytes(prepared.run_directory) == before


@pytest.mark.parametrize("change_boundary", ["after-snapshot", "after-normalize", "after-publish"])
def test_import_reference_detects_source_change_at_each_stable_boundary(
    change_boundary: str,
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_copy(tmp_path)
    _experiment_path, prepared = _prepared(valid_config_data, tmp_path)
    if change_boundary == "after-snapshot":
        real_copy = imported_module._copy_snapshot
        calls = 0

        def changing_copy(
            source_path: Path,
            destination: Path,
            *,
            deadline: float,
            clock: Callable[[], float],
        ) -> None:
            nonlocal calls
            real_copy(source_path, destination, deadline=deadline, clock=clock)
            calls += 1
            if calls == 2:
                source.capture_path.write_bytes(source.capture_path.read_bytes() + b"changed")

        monkeypatch.setattr(imported_module, "_copy_snapshot", changing_copy)
    elif change_boundary == "after-normalize":
        real_normalize = imported_module.normalize_raw_capture

        def changing_normalize(*args: object, **kwargs: object) -> object:
            result = cast(Any, real_normalize)(*args, **kwargs)
            source.capture_path.write_bytes(source.capture_path.read_bytes() + b"changed")
            return result

        monkeypatch.setattr(imported_module, "normalize_raw_capture", changing_normalize)
    else:
        real_publish = imported_module.publish_capture_pair

        def changing_publish(*args: object, **kwargs: object) -> CapturePublication:
            publication = cast(Any, real_publish)(*args, **kwargs)
            source.capture_path.write_bytes(source.capture_path.read_bytes() + b"changed")
            return publication

        monkeypatch.setattr(imported_module, "publish_capture_pair", changing_publish)

    with pytest.raises(TrafficlabError, match="source changed"):
        import_reference(source, prepared, clock=lambda: 10.0)

    assert not tuple(prepared.run_directory.glob(".import-reference.*"))
    assert (prepared.run_directory / "reference.pcapng").exists() is (change_boundary == "after-publish")


def test_discovery_reports_invalid_argument_and_filesystem_inspection_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(TypeError, match="directory must be a Path"):
        discover_import_source(cast(Any, "source"))

    source = tmp_path / "source"
    source.mkdir()
    (source / "source.pcap").write_bytes(b"capture")
    (source / "capture.json").write_bytes(b"{}")
    real_resolve = Path.resolve

    def failed_resolve(path: Path, strict: bool = False) -> Path:
        if path == source:
            raise OSError("resolve sentinel")
        return real_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", failed_resolve)
    with pytest.raises(TrafficlabError, match="resolve sentinel"):
        discover_import_source(source)
    monkeypatch.setattr(Path, "resolve", real_resolve)

    real_stat = Path.stat

    def failed_stat(path: Path, *args: object, **kwargs: object) -> os.stat_result:
        if path.name == "source.pcap":
            raise OSError("entry sentinel")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", failed_stat)
    with pytest.raises(TrafficlabError, match="entry sentinel"):
        discover_import_source(source)


def test_import_identity_deadline_and_copy_defensive_failures(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    _experiment_path, prepared = _prepared(valid_config_data, tmp_path)
    missing = tmp_path / "missing.pcap"
    with pytest.raises(TrafficlabError, match="could not inspect source path"):
        imported_module._path_identity(missing)
    with pytest.raises(TrafficlabError, match="no longer a regular file"):
        imported_module._path_identity(source.directory)
    with pytest.raises(TrafficlabError, match="finite future import deadline"):
        imported_module._deadline(prepared, lambda: float("inf"))
    with pytest.raises(TrafficlabError, match="could not snapshot source file"):
        imported_module._copy_snapshot(
            source.capture_path,
            tmp_path / "missing-parent" / "snapshot",
            deadline=100.0,
            clock=lambda: 0.0,
        )

    class ExplodingFloat(float):
        def __add__(self, other: object) -> float:
            del other
            raise ArithmeticError("deadline sentinel")

    with pytest.raises(TrafficlabError, match="calculate the import deadline"):
        imported_module._deadline(prepared, lambda: cast(float, ExplodingFloat(1.0)))


def test_import_rejects_invalid_prepared_contracts(valid_config_data: dict[str, object], tmp_path: Path) -> None:
    source = _source_copy(tmp_path)
    _experiment_path, prepared = _prepared(valid_config_data, tmp_path)
    with pytest.raises(TypeError, match="PreparedExperiment"):
        import_reference(source, cast(Any, object()))
    with pytest.raises(TypeError, match="ImportSource"):
        import_reference(cast(Any, object()), prepared)

    wrong_config = prepared.config.model_copy(
        update={"run": prepared.config.run.model_copy(update={"directory": tmp_path / "other"})}
    )
    with pytest.raises(TrafficlabError, match="run directory does not match"):
        import_reference(source, replace(prepared, config=wrong_config))

    snapshot = prepared.run_directory / "experiment.toml"
    snapshot.unlink()
    with pytest.raises(TrafficlabError, match="could not read the prepared experiment snapshot"):
        import_reference(source, prepared)
    snapshot.write_bytes(b"changed")
    with pytest.raises(TrafficlabError, match="effective configuration changed"):
        import_reference(source, prepared)


@pytest.mark.parametrize("malformation", ["not-terminated", "invalid-json", "nonobject"])
def test_import_reuse_rejects_malformed_log_without_mutation(
    malformation: str, valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    source = _source_copy(tmp_path)
    _experiment_path, prepared = _prepared(valid_config_data, tmp_path)
    import_reference(source, prepared, clock=lambda: 10.0)
    log = prepared.run_directory / "run.log"
    if malformation == "not-terminated":
        log.write_bytes(log.read_bytes().rstrip(b"\n"))
    elif malformation == "invalid-json":
        log.write_bytes(log.read_bytes() + b"{\n")
    else:
        log.write_bytes(log.read_bytes() + b"[]\n")
    before = _run_bytes(prepared.run_directory)

    with pytest.raises(TrafficlabError, match="exact imported-reference reuse"):
        import_reference(source, prepared, clock=lambda: 20.0)

    assert _run_bytes(prepared.run_directory) == before


@pytest.mark.parametrize("boundary", ["during-validation", "before-log"])
def test_import_reuse_detects_output_identity_races(
    boundary: str,
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_copy(tmp_path)
    _experiment_path, prepared = _prepared(valid_config_data, tmp_path)
    import_reference(source, prepared, clock=lambda: 10.0)
    reference = prepared.run_directory / "reference.pcapng"
    if boundary == "during-validation":
        real_validate = imported_module.validate_capture_pair

        def changing_validate(*args: object, **kwargs: object) -> CaptureInspection:
            result = cast(Any, real_validate)(*args, **kwargs)
            os.utime(reference, ns=(reference.stat().st_atime_ns, reference.stat().st_mtime_ns + 1))
            return result

        monkeypatch.setattr(imported_module, "validate_capture_pair", changing_validate)
    else:
        real_read = imported_module._read_import_lineage

        def changing_read(run_directory: Path) -> list[dict[str, object]]:
            result = real_read(run_directory)
            os.utime(reference, ns=(reference.stat().st_atime_ns, reference.stat().st_mtime_ns + 1))
            return result

        monkeypatch.setattr(imported_module, "_read_import_lineage", changing_read)

    with pytest.raises(TrafficlabError, match="capture pair changed"):
        import_reference(source, prepared, clock=lambda: 20.0)


@pytest.mark.parametrize("inconsistency", ["snapshot", "normalized-count", "appeared-pair", "published-count"])
def test_fresh_import_rejects_inconsistent_boundary_results(
    inconsistency: str,
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_copy(tmp_path)
    _experiment_path, prepared = _prepared(valid_config_data, tmp_path)
    if inconsistency == "snapshot":
        real_copy = imported_module._copy_snapshot

        def corrupting_copy(
            source_path: Path,
            destination: Path,
            *,
            deadline: float,
            clock: Callable[[], float],
        ) -> None:
            real_copy(source_path, destination, deadline=deadline, clock=clock)
            if source_path == source.metadata_path:
                destination.write_bytes(destination.read_bytes() + b" ")

        monkeypatch.setattr(imported_module, "_copy_snapshot", corrupting_copy)
    elif inconsistency == "normalized-count":
        real_normalize = imported_module.normalize_raw_capture

        def wrong_normalization(*args: object, **kwargs: object) -> RawNormalizationResult:
            result = cast(Any, real_normalize)(*args, **kwargs)
            return replace(result, packet_count=result.packet_count + 1)

        monkeypatch.setattr(imported_module, "normalize_raw_capture", wrong_normalization)
    else:
        real_publish = imported_module.publish_capture_pair

        def wrong_publication(*args: object, **kwargs: object) -> CapturePublication:
            publication = cast(Any, real_publish)(*args, **kwargs)
            if inconsistency == "appeared-pair":
                return CapturePublication(publication.inspection, False, None)
            inspection = replace(
                publication.inspection,
                packet_count=publication.inspection.packet_count + 1,
            )
            return replace(publication, inspection=inspection)

        monkeypatch.setattr(imported_module, "publish_capture_pair", wrong_publication)

    with pytest.raises(TrafficlabError):
        import_reference(source, prepared, clock=lambda: 10.0)

    assert not tuple(prepared.run_directory.glob(".import-reference.*"))


def test_import_reports_owned_temporary_cleanup_failure_after_preserving_publication(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    _experiment_path, prepared = _prepared(valid_config_data, tmp_path)

    def failed_cleanup(path: Path) -> None:
        del path
        raise OSError("cleanup sentinel")

    monkeypatch.setattr(imported_module.shutil, "rmtree", failed_cleanup)
    with pytest.raises(TrafficlabError, match="cleanup sentinel"):
        import_reference(source, prepared, clock=lambda: 10.0)

    assert (prepared.run_directory / "reference.pcapng").is_file()
    assert (
        len([record for record in _records(prepared.run_directory) if record.get("event") == "reference_imported"]) == 1
    )


def test_reuse_detects_pair_removed_after_initial_presence_check(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    _experiment_path, prepared = _prepared(valid_config_data, tmp_path)

    def pair_present(_directory: Path) -> tuple[bool, bool]:
        return (True, True)

    monkeypatch.setattr(imported_module, "_canonical_pair_presence", pair_present)

    with pytest.raises(TrafficlabError, match="capture pair is incomplete"):
        import_reference(source, prepared, clock=lambda: 10.0)


def test_run_imported_rejects_configuration_change_on_read_only_preflight_reload(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_text(tomli_w.dumps(valid_config_data), encoding="utf-8")
    real_load = imported_module.load_configuration_pair
    calls = 0

    def changing_load(path: Path) -> ConfigurationPair:
        nonlocal calls
        calls += 1
        if calls == 2:
            changed = dict(valid_config_data)
            changed_capture = dict(cast(dict[str, object], changed["capture"]))
            changed_capture["total_timeout_seconds"] = 61.0
            changed["capture"] = changed_capture
            path.write_text(tomli_w.dumps(changed), encoding="utf-8")
        return real_load(path)

    monkeypatch.setattr(imported_module, "load_configuration_pair", changing_load)
    with pytest.raises(TrafficlabError, match="changed during imported preflight"):
        run_imported_experiment(experiment_path, source.directory)
