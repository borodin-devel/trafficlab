import copy
from pathlib import Path

import pytest

from tests.support.failure_matrix.oracle import (
    assert_failure_log_suffix,
    canonical_log_bytes,
    strict_canonical_log_rows,
)


@pytest.mark.parametrize(
    "content",
    (
        pytest.param(b'{"event":"first","event":"second"}\n', id="duplicate-key"),
        pytest.param(b'{"stage":"fit","event":"stage_failed"}\n', id="unsorted-keys"),
        pytest.param(b'{"event": "stage_failed"}\n', id="noncanonical-whitespace"),
        pytest.param(b'{"event":"stage_failed"}', id="missing-newline"),
    ),
)
def test_public_matrix_log_oracle_rejects_noncanonical_jsonl(content: bytes) -> None:
    with pytest.raises(AssertionError):
        strict_canonical_log_rows(content)


@pytest.mark.parametrize("mutation", ("missing", "extra", "wrong"))
def test_public_matrix_log_oracle_rejects_wrong_outer_record(
    mutation: str,
    tmp_path: Path,
) -> None:
    expected: dict[str, object] = {
        "detail": "expected detail",
        "event": "stage_failed",
        "stage": "fit",
    }
    actual = copy.deepcopy(expected)
    if mutation == "missing":
        actual.pop("detail")
    elif mutation == "extra":
        actual["unexpected"] = True
    else:
        actual["detail"] = "wrong detail"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    (run_directory / "run.log").write_bytes(canonical_log_bytes((actual,)))

    with pytest.raises(AssertionError):
        assert_failure_log_suffix(
            run_directory,
            (False, b""),
            expected_records=(expected,),
        )
