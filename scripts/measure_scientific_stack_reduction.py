#!/usr/bin/env python3
"""Recompute exact scientific-stack source-reduction inventories from Git."""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

REPOSITORY = Path(__file__).resolve().parents[1]
OUTPUT = REPOSITORY / "examples" / "scientific_stack" / "code_reduction.json"
_HEX40 = re.compile(r"[0-9a-f]{40}")

_NUMPY_BASELINE = "2d1a2dafd3b31787d4b48e4bce508492b89b6c7c"
_NUMPY_AFTER = "b32bc74d8b6778cddbcb863f54e8ff8d56f936c4"
_NUMPY_BEFORE_FUNCTIONS: Mapping[str, tuple[str, ...]] = {
    "src/trafficlab/trace.py": ("normalize_reference", "align_generated"),
    "src/trafficlab/models/common.py": ("MarkDistribution.from_reference",),
    "src/trafficlab/models/markov_renewal.py": ("type7_quantile", "_fit_events"),
    "src/trafficlab/similarity/ks.py": ("exact_ecdf_distance",),
    "src/trafficlab/similarity/autocorrelation.py": ("sample_autocorrelation",),
    "src/trafficlab/similarity/multiscale.py": ("_binned_features",),
}
_NUMPY_AFTER_FUNCTIONS: Mapping[str, tuple[str, ...]] = {
    "src/trafficlab/trace.py": ("normalize_reference", "align_generated"),
    "src/trafficlab/models/common.py": ("MarkDistribution.from_trace",),
    "src/trafficlab/models/markov_renewal.py": (
        "type7_boundaries",
        "encode_markov_states",
        "transition_count_matrix",
        "_fit_trace",
    ),
    "src/trafficlab/similarity/ks.py": ("_ks_statistic",),
    "src/trafficlab/similarity/autocorrelation.py": ("sample_autocorrelation",),
    "src/trafficlab/similarity/multiscale.py": ("_binned_features",),
}

_TASK5_BEFORE: Mapping[str, tuple[str, ...]] = {
    "src/trafficlab/comparison.py": (
        "ComparisonResult.from_dict",
        "MethodComparison.from_dict",
        "_bounded_float",
        "_bounded_weighted_score",
        "_exact_keys",
        "_float_list",
        "_normalized_weights",
        "_ranged_float",
        "_strict_float",
        "_strict_int",
        "_validate_acf_feature",
        "_validate_autocorrelation_diagnostics",
        "_validate_direction_totals",
        "_validate_frame_size_diagnostics",
        "_validate_iat_diagnostics",
        "_validate_method_diagnostics",
        "_validate_multiscale_diagnostics",
        "_validate_score_discrepancy",
    ),
    "src/trafficlab/errors.py": (
        "FailureOutcome.__post_init__",
        "FailureOutcome.from_dict",
        "FailureOutcome.from_json",
        "_strict_json_object",
        "_validate_failure_outcome_order",
    ),
    "src/trafficlab/models/registry.py": (
        "BestModel.__post_init__",
        "_build_bounds",
        "_exact_mapping",
        "_parse_final_limits",
        "_parse_genes",
        "_parse_identity",
        "_validate_best_model",
        "_validate_final_limits",
        "_validate_final_seed",
        "_validate_genes",
        "_validate_identity",
        "_validate_window",
    ),
}
_TASK5_AFTER: Mapping[str, tuple[str, ...]] = {
    "src/trafficlab/comparison.py": (
        "ComparisonResult.from_dict",
        "MethodComparison.from_dict",
        "_bounded_weighted_score",
        "_exact_float_input",
    ),
    "src/trafficlab/errors.py": (
        "FailureOutcomeRecord.from_dict",
        "FailureOutcomeRecord.from_json",
        "_strict_json_object",
        "_validate_failure_outcome_order",
    ),
    "src/trafficlab/models/registry.py": (
        "_build_bounds",
        "_exact_mapping",
        "_parse_final_limits",
        "_parse_genes",
        "_parse_identity",
        "_validate_best_model",
        "_validate_final_limits",
        "_validate_final_seed",
        "_validate_genes",
        "_validate_identity",
        "_validate_window",
    ),
}
_TASK6_BEFORE: Mapping[str, tuple[str, ...]] = {
    "src/trafficlab/genetic/checkpoint.py": (
        "_exact_object",
        "_float",
        "_float_array",
        "_parse_candidate",
        "_parse_compatibility",
        "_parse_coordinate",
        "_parse_decimal",
        "_parse_duplicate",
        "_parse_failure",
        "_parse_family",
        "_parse_gene",
        "_parse_generation_limits",
        "_parse_genetic",
        "_parse_history_csv",
        "_parse_history_row",
        "_parse_identifier",
        "_parse_method",
        "_parse_repr_float",
        "_parse_rng",
        "_parse_similarity",
        "_parse_trial",
        "_validate_candidate",
        "_validate_compatibility_shape",
        "_validate_coordinate",
        "_validate_family_spec",
        "_validate_genetic",
        "_validate_history",
        "_validate_rng_state",
        "_validate_state",
    ),
    "src/trafficlab/genetic/types.py": (
        "Candidate.__post_init__",
        "CandidateFailure.__post_init__",
        "DuplicateDiagnostic.__post_init__",
        "HistoryRow.__post_init__",
        "TrialResult.__post_init__",
        "_validate_model_diagnostic_shape",
    ),
}
_TASK6_AFTER: Mapping[str, tuple[str, ...]] = {
    "src/trafficlab/genetic/checkpoint.py": (
        "_exact_float_input",
        "_float",
        "_parse_decimal",
        "_parse_gene",
        "_parse_history_csv",
        "_parse_repr_float",
        "_validate_candidate",
        "_validate_compatibility_shape",
        "_validate_coordinate",
        "_validate_family_spec",
        "_validate_genetic",
        "_validate_history",
        "_validate_rng_state",
        "_validate_state",
    ),
    "src/trafficlab/genetic/types.py": ("_exact_float", "_validate_model_diagnostic_shape"),
}


def _qualified_functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    def visit(body: Sequence[ast.stmt], prefix: str = "") -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = f"{prefix}{node.name}"
                functions[qualified] = node
                visit(node.body, f"{qualified}.")
            elif isinstance(node, ast.ClassDef):
                visit(node.body, f"{prefix}{node.name}.")

    visit(tree.body)
    return functions


def _git_source(repository: Path, revision: str, path: str) -> str:
    completed = subprocess.run(
        ("git", "show", f"{revision}:{path}"),
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError(f"cannot read {path} at {revision}: {completed.stderr.strip()}")
    return completed.stdout


def _statement_lines(function: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, ...]:
    return tuple(sorted({node.lineno for node in ast.walk(function) if isinstance(node, ast.stmt)}))


def _loop_body_lines(function: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, ...]:
    lines: set[int] = set()
    for loop in (node for node in ast.walk(function) if isinstance(node, (ast.For, ast.While))):
        lines.update(node.lineno for node in ast.walk(loop) if isinstance(node, ast.stmt))
    return tuple(sorted(lines))


def _inventory(
    repository: Path,
    revision: str,
    paths: Mapping[str, tuple[str, ...]],
    *,
    measurement: Literal["ast_statements", "loop_body_statements"],
) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    total_by_path: dict[str, set[int]] = {}
    for path in sorted(paths):
        source = _git_source(repository, revision, path)
        functions = _qualified_functions(ast.parse(source, filename=path))
        names = paths[path]
        missing = set(names) - set(functions)
        if missing:
            raise ValueError(f"missing functions at {revision}:{path}: {', '.join(sorted(missing))}")
        for name in sorted(names):
            function = functions[name]
            if measurement == "ast_statements":
                lines = _statement_lines(function)
            else:
                lines = _loop_body_lines(function)
            total_by_path.setdefault(path, set()).update(lines)
            entries.append(
                {
                    "executable_lines": list(lines),
                    "function": name,
                    "line_count": len(lines),
                    "path": path,
                }
            )
    return {
        "functions": entries,
        "revision": revision,
        "total_lines": sum(len(lines) for lines in total_by_path.values()),
    }


def _phase(
    repository: Path,
    *,
    name: str,
    before_revision: str,
    after_revision: str,
    before_paths: Mapping[str, tuple[str, ...]],
    after_paths: Mapping[str, tuple[str, ...]],
    measurement: Literal["ast_statements", "loop_body_statements"],
) -> dict[str, object]:
    before = _inventory(repository, before_revision, before_paths, measurement=measurement)
    after = _inventory(repository, after_revision, after_paths, measurement=measurement)
    return {
        "after": after,
        "after_lines": after["total_lines"],
        "before": before,
        "before_lines": before["total_lines"],
        "measurement": measurement,
        "name": name,
    }


def _category(name: str, threshold: float, phases: list[dict[str, object]]) -> dict[str, object]:
    before = sum(cast(int, phase["before_lines"]) for phase in phases)
    after = sum(cast(int, phase["after_lines"]) for phase in phases)
    reduction = 100.0 * (before - after) / before
    return {
        "after_lines": after,
        "before_lines": before,
        "name": name,
        "passed": reduction >= threshold,
        "phases": phases,
        "reduction_percent": reduction,
        "threshold_percent": threshold,
    }


def build_reduction_evidence(repository: Path = REPOSITORY) -> dict[str, Any]:
    """Recompute both acceptance categories from immutable Git revisions."""
    root = repository.resolve()
    numerical = _phase(
        root,
        name="tasks_2_to_4_numpy_migration",
        before_revision=_NUMPY_BASELINE,
        after_revision=_NUMPY_AFTER,
        before_paths={path: names for path, names in _NUMPY_BEFORE_FUNCTIONS.items()},
        after_paths={path: names for path, names in _NUMPY_AFTER_FUNCTIONS.items()},
        measurement="loop_body_statements",
    )
    task_5 = _phase(
        root,
        name="task_5_core_artifacts",
        before_revision="90e3e1d19406b45dbbe2ab0abb56ad1b946b5187",
        after_revision="fc865991a65e82b5c0199682768888f4856366ce",
        before_paths=_TASK5_BEFORE,
        after_paths=_TASK5_AFTER,
        measurement="ast_statements",
    )
    task_6 = _phase(
        root,
        name="task_6_checkpoint_artifacts",
        before_revision="734af74eb9c31e5fd1890e77bb61969b59995bab",
        after_revision="60674f7b2d2edf7ba844c01fa37e419aa9cd83b5",
        before_paths=_TASK6_BEFORE,
        after_paths=_TASK6_AFTER,
        measurement="ast_statements",
    )
    task_7_functions: Mapping[str, tuple[str, ...]] = {
        "scripts/audit_validation_study.py": (
            "_entries",
            "_environment",
            "_protocol",
            "_lifecycle_rows",
            "_lifecycle",
        ),
        "scripts/run_validation_study.py": (
            "_retained_identity",
            "_retained_output",
            "_retained_prerequisite_environment",
            "_retained_prerequisite_capability",
            "_retained_prerequisite_document",
        ),
    }
    task_7 = _phase(
        root,
        name="task_7_study_artifacts",
        before_revision="cc43e4bda65cd7afc40cad12228bc73a1673c868",
        after_revision="7b6094a9f47458b8de8d45deca64bc71170c62fd",
        before_paths=task_7_functions,
        after_paths=task_7_functions,
        measurement="ast_statements",
    )
    categories = [
        _category("numpy_loop_validation", 25.0, [numerical]),
        _category("artifact_schema_validation", 30.0, [task_5, task_6, task_7]),
    ]
    evidence: dict[str, object] = {
        "categories": categories,
        "decision": {"passed": all(cast(bool, category["passed"]) for category in categories)},
        "excluded_prefixes": ["examples/", "tests/"],
        "schema_version": 1,
    }
    validate_reduction_evidence(evidence)
    return evidence


def _inventory_total(side: Mapping[str, object]) -> int:
    functions = cast(list[dict[str, object]], side.get("functions"))
    by_path: dict[str, set[int]] = {}
    previous: tuple[str, str] | None = None
    for item in functions:
        path = cast(str, item.get("path"))
        name = cast(str, item.get("function"))
        key = (path, name)
        if previous is not None and key <= previous:
            raise ValueError("function inventory must be unique and sorted")
        previous = key
        if path.startswith(("examples/", "tests/")):
            raise ValueError("function inventory includes tests or generated evidence")
        lines = cast(list[object], item.get("executable_lines"))
        typed_lines: list[int] = []
        for line in lines:
            if type(line) is not int or line <= 0:
                raise ValueError("function inventory has invalid executable lines")
            typed_lines.append(line)
        if typed_lines != sorted(set(typed_lines)):
            raise ValueError("function inventory has invalid executable lines")
        if item.get("line_count") != len(lines):
            raise ValueError("function inventory line count does not match its lines")
        by_path.setdefault(path, set()).update(typed_lines)
    revision = side.get("revision")
    if not isinstance(revision, str) or _HEX40.fullmatch(revision) is None:
        raise ValueError("function inventory revision must be a full Git identity")
    total = sum(len(lines) for lines in by_path.values())
    if side.get("total_lines") != total:
        raise ValueError("function inventory total does not match unique lines")
    return total


def validate_reduction_evidence(evidence: Mapping[str, object]) -> None:
    """Recompute totals, percentages, disjointness, and final decision."""
    if tuple(sorted(evidence)) != ("categories", "decision", "excluded_prefixes", "schema_version"):
        raise ValueError("reduction evidence has invalid root fields")
    if evidence.get("schema_version") != 1 or evidence.get("excluded_prefixes") != ["examples/", "tests/"]:
        raise ValueError("reduction evidence policy is invalid")
    categories = cast(list[dict[str, object]], evidence.get("categories"))
    if [category.get("name") for category in categories] != [
        "numpy_loop_validation",
        "artifact_schema_validation",
    ]:
        raise ValueError("reduction evidence categories are invalid")
    for category in categories:
        phases = cast(list[dict[str, object]], category.get("phases"))
        if category["name"] == "artifact_schema_validation" and any(
            phase.get("measurement") != "ast_statements" for phase in phases
        ):
            raise ValueError("artifact phases must use the same AST statement-line metric")
        if category["name"] == "numpy_loop_validation" and any(
            phase.get("measurement") != "loop_body_statements" for phase in phases
        ):
            raise ValueError("NumPy phase must use the declared loop-body statement metric")
        before_total = 0
        after_total = 0
        phase_paths: list[set[str]] = []
        for phase in phases:
            before = cast(dict[str, object], phase.get("before"))
            after = cast(dict[str, object], phase.get("after"))
            before_lines = _inventory_total(before)
            after_lines = _inventory_total(after)
            if phase.get("before_lines") != before_lines:
                raise ValueError("phase before total does not match inventory")
            if phase.get("after_lines") != after_lines:
                raise ValueError("phase after total does not match inventory")
            before_total += before_lines
            after_total += after_lines
            phase_paths.append(
                {
                    cast(str, item["path"])
                    for side in (before, after)
                    for item in cast(list[dict[str, object]], side["functions"])
                }
            )
        if category["name"] == "artifact_schema_validation":
            for index, paths in enumerate(phase_paths):
                if any(paths & other for other in phase_paths[index + 1 :]):
                    raise ValueError("artifact phases double-count an owner path")
        if category.get("before_lines") != before_total:
            raise ValueError("category before total does not match phase inventories")
        if category.get("after_lines") != after_total:
            raise ValueError("category after total does not match phase inventories")
        if before_total <= 0 or after_total < 0 or after_total > before_total:
            raise ValueError("reduction totals are invalid")
        reduction = 100.0 * (before_total - after_total) / before_total
        threshold = category.get("threshold_percent")
        if type(threshold) is not float or threshold <= 0.0:
            raise ValueError("reduction threshold is invalid")
        if category.get("reduction_percent") != reduction:
            raise ValueError("reduction percentage does not match inventories")
        if category.get("passed") is not (reduction >= threshold):
            raise ValueError("reduction gate does not match percentage")
    expected_decision = {"passed": all(cast(bool, category["passed"]) for category in categories)}
    if evidence.get("decision") != expected_decision:
        raise ValueError("reduction decision does not match category gates")


def canonical_json_bytes(document: Mapping[str, object]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--repository", type=Path, default=REPOSITORY)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    repository = cast(Path, arguments.repository)
    output = cast(Path, arguments.output)
    try:
        expected = canonical_json_bytes(build_reduction_evidence(repository))
        if cast(bool, arguments.check):
            if output.read_bytes() != expected:
                print(f"scientific-stack-reduction: stale evidence at {output}", file=sys.stderr)
                return 1
            print(f"scientific-stack-reduction: verified {output}")
            return 0
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(expected)
        print(f"scientific-stack-reduction: wrote {output}")
        return 0
    except (OSError, ValueError) as error:
        print(f"scientific-stack-reduction: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
