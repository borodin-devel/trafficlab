from __future__ import annotations

import platform
from pathlib import Path

import pytest

from scripts import generate_fit_fixtures as fixture_generator
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.config_io import load_experiment
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.trace import load_capture_metadata, normalize_reference
from trafficlab.fitting.genetic.checkpoint import load_checkpoint
from trafficlab.fitting.genetic.strategy import make_strategy_context

_ROOT = Path(__file__).resolve().parents[2]
_FIT_DIRECTORY = _ROOT / "examples" / "data" / "fit"


def _checked_tree() -> dict[str, bytes]:
    return {name: (_FIT_DIRECTORY / name).read_bytes() for name in fixture_generator.ARTIFACT_NAMES}


def test_fit_fixture_generator_check_mode_accepts_checked_in_bytes() -> None:
    """A stale production-derived fit artifact must make the deterministic fixture gate fail."""
    assert fixture_generator.main(["--check"]) == 0


def test_development_runtime_pin_exactly_matches_checked_checkpoint_python_version() -> None:
    """A floating patch runtime would make strict fixture checkpoint loading fail after an otherwise valid checkout."""
    runtime_pin = (_ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert runtime_pin == platform.python_version()

    experiment_path = _FIT_DIRECTORY / "experiment.toml"
    capture_path = _FIT_DIRECTORY / "capture.json"
    reference_path = _FIT_DIRECTORY / "reference.pcapng"
    config = load_experiment(experiment_path)
    capture_bytes = capture_path.read_bytes()
    metadata = load_capture_metadata(capture_path)
    reference_bytes = reference_path.read_bytes()
    parsed = read_pcapng_bytes(reference_bytes, metadata, source=reference_path)
    reference, window = normalize_reference(parsed)
    context = make_strategy_context(
        config,
        reference,
        window,
        _FIT_DIRECTORY,
        experiment_identity=identify_bytes(experiment_path.read_bytes()),
        reference_identity=identify_bytes(reference_bytes),
        capture_identity=identify_bytes(capture_bytes),
    )
    checkpoint = load_checkpoint(_FIT_DIRECTORY / "checkpoint.json", context.compatibility)
    assert checkpoint.compatibility.python_version == runtime_pin


def test_normal_mode_writes_only_the_complete_fit_fixture_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropping an artifact or escaping the fit directory would publish an incomplete reproducibility fixture."""
    expected = _checked_tree()
    destination = tmp_path / "examples" / "data" / "fit"
    monkeypatch.setattr(fixture_generator, "FIT_DIRECTORY", destination)
    monkeypatch.setattr(fixture_generator, "generate_fixture_tree", lambda: expected)

    assert fixture_generator.main([]) == 0

    written = {
        path.relative_to(destination).as_posix(): path.read_bytes() for path in destination.rglob("*") if path.is_file()
    }
    assert written == expected
    assert tuple(path for path in tmp_path.rglob("*") if path.is_file() and destination not in path.parents) == ()


def test_check_mode_reports_every_missing_modified_and_unexpected_relative_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stopping at the first mismatch would hide other stale paths in the checked fixture tree."""
    expected = _checked_tree()
    destination = tmp_path / "examples" / "data" / "fit"
    destination.mkdir(parents=True)
    for name, content in expected.items():
        (destination / name).write_bytes(content)
    (destination / "checkpoint.json").write_bytes(b"modified\n")
    (destination / "best_model.json").unlink()
    (destination / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
    monkeypatch.setattr(fixture_generator, "FIT_DIRECTORY", destination)
    monkeypatch.setattr(fixture_generator, "generate_fixture_tree", lambda: expected)

    assert fixture_generator.main(["--check"]) == 1

    errors = capsys.readouterr().err.splitlines()
    assert errors == [
        "mismatched fixture path: examples/data/fit/best_model.json",
        "mismatched fixture path: examples/data/fit/checkpoint.json",
        "unexpected fixture path: examples/data/fit/unexpected.txt",
    ]


def test_fixture_validation_rejects_wrong_path_set_invalid_toml_stale_history_and_non_utf8_readme(
    tmp_path: Path,
) -> None:
    """A generator that validates only the happy path could check in an unusable artifact tree."""
    tree = _checked_tree()
    without_readme = {name: content for name, content in tree.items() if name != "README.md"}
    with pytest.raises(TrafficlabError, match="wrong paths"):
        fixture_generator._validate_fixture_tree(  # pyright: ignore[reportPrivateUsage]
            without_readme,
            tmp_path,
        )

    invalid_toml = {**tree, "experiment.toml": b"[invalid\n"}
    with pytest.raises(TrafficlabError, match="invalid generated fit experiment"):
        fixture_generator._validate_fixture_tree(  # pyright: ignore[reportPrivateUsage]
            invalid_toml,
            tmp_path,
        )

    stale_history = {**tree, "ga_history.csv": b"stale\n"}
    with pytest.raises(TrafficlabError, match="exact checkpoint projection"):
        fixture_generator._validate_fixture_tree(  # pyright: ignore[reportPrivateUsage]
            stale_history,
            tmp_path,
        )

    invalid_readme = {**tree, "README.md": b"\xff"}
    with pytest.raises(TrafficlabError, match="README is not UTF-8"):
        fixture_generator._validate_fixture_tree(  # pyright: ignore[reportPrivateUsage]
            invalid_readme,
            tmp_path,
        )


def test_generator_rejects_invalid_embedded_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """An invalid embedded experiment must fail before the generator runs a fit."""
    monkeypatch.setattr(fixture_generator, "_CONFIG_TEMPLATE", "[invalid\n")
    with pytest.raises(
        TrafficlabError,
        match="invalid genetic-fitting and checkpoint-resume fixture configuration",
    ):
        fixture_generator.generate_fixture_tree()


def test_writer_rejects_invalid_paths_and_translates_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid or failed destinations must not be reported as a complete checked fixture tree."""
    expected = _checked_tree()
    with pytest.raises(
        TrafficlabError,
        match="invalid genetic-fitting and checkpoint-resume fixture path set",
    ):
        fixture_generator.write_fixture_tree({"../escape": b"no"})

    destination = tmp_path / "fit"
    monkeypatch.setattr(fixture_generator, "FIT_DIRECTORY", destination)
    real_write = Path.write_bytes

    def fail_checkpoint(path: Path, content: bytes) -> int:
        if path.name == "checkpoint.json":
            raise OSError("write sentinel")
        return real_write(path, content)

    monkeypatch.setattr(Path, "write_bytes", fail_checkpoint)
    with pytest.raises(TrafficlabError, match="write sentinel"):
        fixture_generator.write_fixture_tree(expected)


def test_check_translates_fixture_directory_inspection_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable directory must be distinct from an ordinary byte mismatch."""
    expected = _checked_tree()
    destination = tmp_path / "fit"
    destination.mkdir()
    for name, content in expected.items():
        (destination / name).write_bytes(content)
    monkeypatch.setattr(fixture_generator, "FIT_DIRECTORY", destination)

    def fail_rglob(_path: Path, _pattern: str) -> tuple[Path, ...]:
        raise OSError("inspect sentinel")

    monkeypatch.setattr(Path, "rglob", fail_rglob)
    with pytest.raises(TrafficlabError, match="inspect sentinel"):
        fixture_generator.compare_fixture_tree(expected)
