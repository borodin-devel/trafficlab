#!/usr/bin/env python3
"""Recompute exact scientific-stack source-reduction inventories from Git."""

from __future__ import annotations

import argparse
import ast
import copy
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

from trafficlab.common.json import render_json_document

REPOSITORY = Path(__file__).resolve().parents[1]
OUTPUT = REPOSITORY / "examples" / "scientific_stack" / "code_reduction.json"
_HEX40 = re.compile(r"[0-9a-f]{40}")

_NUMPY_BASELINE = "2d1a2dafd3b31787d4b48e4bce508492b89b6c7c"
_NUMPY_AFTER = "cd05f02e50a005df02d9e0c81a0d1ca97b9cbe18"
_NUMPY_BEFORE_LOOP_FUNCTIONS: Mapping[str, tuple[str, ...]] = {
    "src/trafficlab/models/common.py": ("MarkDistribution.from_reference",),
    "src/trafficlab/models/markov_renewal.py": ("_fit_events",),
    "src/trafficlab/similarity/ks.py": ("exact_ecdf_distance",),
    "src/trafficlab/similarity/autocorrelation.py": ("sample_autocorrelation",),
    "src/trafficlab/similarity/multiscale.py": ("_binned_features",),
}
_NUMPY_BEFORE_VALIDATION_FUNCTIONS: Mapping[str, tuple[str, ...]] = {
    "src/trafficlab/trace.py": (
        "TraceEvent.__post_init__",
        "_validated_events",
        "normalize_reference",
        "align_generated",
    ),
    "src/trafficlab/models/common.py": (
        "_validate_frame_length",
        "_validate_canonical_events",
        "GenerationResult.__post_init__",
        "validate_fit_inputs",
        "MarkDistribution.__post_init__",
    ),
    "src/trafficlab/models/markov_renewal.py": ("type7_quantile", "_validate_repair_reference"),
    "src/trafficlab/similarity/ks.py": (
        "_validated_numeric_sample",
        "_validated_trace",
        "_validate_diagnostic_quantile",
    ),
    "src/trafficlab/similarity/autocorrelation.py": (
        "_validated_numeric_values",
        "_validated_lag",
        "_validated_lags",
        "_validated_weights",
        "_validated_trace",
        "_validate_lags_fit_samples",
    ),
    "src/trafficlab/similarity/multiscale.py": (
        "_validated_cells",
        "_validated_weights",
        "_validated_widths_and_bin_counts",
        "_validated_trace",
    ),
}
_NUMPY_AFTER_LOOP_FUNCTIONS: Mapping[str, tuple[str, ...]] = {
    "src/trafficlab/generation/models/common.py": ("MarkDistribution.from_trace",),
    "src/trafficlab/generation/models/markov_renewal.py": (
        "_fit_trace",
        "encode_markov_states",
        "transition_count_matrix",
    ),
    "src/trafficlab/comparison/similarity/ks.py": ("_ks_statistic",),
    "src/trafficlab/comparison/similarity/autocorrelation.py": ("_sample_autocorrelations",),
    "src/trafficlab/comparison/similarity/multiscale.py": ("_binned_trace_features",),
}
_NUMPY_AFTER_VALIDATION_FUNCTIONS: Mapping[str, tuple[str, ...]] = {
    "src/trafficlab/common/trace.py": (
        "TrafficTrace.__post_init__",
        "validate_traffic_trace",
        "normalize_reference",
        "align_generated",
    ),
    "src/trafficlab/generation/models/common.py": (
        "_validate_frame_length",
        "GenerationResult.__post_init__",
        "validate_fit_inputs",
        "MarkDistribution.__post_init__",
        "make_generation_trace",
    ),
    "src/trafficlab/generation/models/markov_renewal.py": (
        "type7_boundaries",
        "encode_markov_states",
        "transition_count_matrix",
    ),
    "src/trafficlab/comparison/similarity/ks.py": ("_validate_diagnostic_quantile",),
    "src/trafficlab/comparison/similarity/autocorrelation.py": (
        "_validated_lag",
        "_validated_lags",
        "_validate_lags_fit_samples",
    ),
    "src/trafficlab/comparison/similarity/common.py": (
        "validated_numeric_sample",
        "validated_numeric_array",
        "validated_weights",
    ),
    "src/trafficlab/comparison/similarity/multiscale.py": ("_validated_cells", "_validated_widths_and_bin_counts"),
}

# The historical post-migration revision remains the source of the accepted
# measurement.  Task 7 only relocated these measured functions; the check
# below compares their ASTs at the new owners rather than rewriting history.
_NUMPY_CURRENT_RELOCATIONS: Mapping[tuple[str, str], tuple[str, str]] = {
    ("src/trafficlab/comparison/similarity/autocorrelation.py", "_sample_autocorrelations"): (
        "src/trafficlab/comparison/similarity/autocorrelation.py",
        "sample_autocorrelations",
    ),
    ("src/trafficlab/comparison/similarity/multiscale.py", "_binned_trace_features"): (
        "src/trafficlab/comparison/similarity/multiscale.py",
        "binned_direction_features",
    ),
    ("src/trafficlab/generation/models/markov_renewal.py", "_fit_trace"): (
        "src/trafficlab/generation/models/markov_renewal/model.py",
        "fit_trace",
    ),
    ("src/trafficlab/generation/models/markov_renewal.py", "encode_markov_states"): (
        "src/trafficlab/generation/models/markov_renewal/model.py",
        "encode_markov_states",
    ),
    ("src/trafficlab/generation/models/markov_renewal.py", "transition_count_matrix"): (
        "src/trafficlab/generation/models/markov_renewal/model.py",
        "transition_count_matrix",
    ),
    ("src/trafficlab/generation/models/markov_renewal.py", "type7_boundaries"): (
        "src/trafficlab/generation/models/markov_renewal/parameters.py",
        "type7_boundaries",
    ),
}

_NUMPY_ACCEPTED_SYMBOL_RENAMES = {"_snap_near_integer": "snap_near_integer"}

_LOOP_AND_VALIDATION_DEFINITION = (
    "unique ast.stmt lines in complete explicitly named migrated functions, including nested loop bodies and "
    "straight-line custom validation"
)

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


def _function_ast_identity(function: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Return a function AST identity while allowing an intentional visibility rename."""
    normalized = copy.deepcopy(function)
    normalized.name = "function"
    for node in ast.walk(normalized):
        if isinstance(node, ast.Name):
            node.id = _NUMPY_ACCEPTED_SYMBOL_RENAMES.get(node.id, node.id)
    return ast.dump(normalized, include_attributes=False)


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


def _full_revision(repository: Path, revision: str) -> str:
    completed = subprocess.run(
        ("git", "rev-parse", "--verify", f"{revision}^{{commit}}"),
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    resolved = completed.stdout.strip()
    if completed.returncode != 0 or _HEX40.fullmatch(resolved) is None:
        raise ValueError(f"cannot resolve full Git revision {revision!r}")
    return resolved


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


def _loop_and_validation_inventory(
    repository: Path,
    revision: str,
    *,
    loop_paths: Mapping[str, tuple[str, ...]],
    validation_paths: Mapping[str, tuple[str, ...]],
) -> dict[str, object]:
    """Count loop bodies plus every statement in explicit custom-validation functions."""
    entries: list[dict[str, object]] = []
    total_by_path: dict[str, set[int]] = {}
    for path in sorted(set(loop_paths) | set(validation_paths)):
        source = _git_source(repository, revision, path)
        functions = _qualified_functions(ast.parse(source, filename=path))
        loop_names = set(loop_paths.get(path, ()))
        validation_names = set(validation_paths.get(path, ()))
        missing = (loop_names | validation_names) - set(functions)
        if missing:
            raise ValueError(f"missing functions at {revision}:{path}: {', '.join(sorted(missing))}")
        for name in sorted(loop_names | validation_names):
            roles: list[str] = []
            lines: set[int] = set()
            if name in loop_names:
                roles.append("loop_body")
                lines.update(_loop_body_lines(functions[name]))
            if name in validation_names:
                roles.append("straight_line_validation")
                lines.update(_statement_lines(functions[name]))
            ordered_lines = tuple(sorted(lines))
            total_by_path.setdefault(path, set()).update(ordered_lines)
            entries.append(
                {
                    "executable_lines": list(ordered_lines),
                    "function": name,
                    "line_count": len(ordered_lines),
                    "path": path,
                    "roles": roles,
                }
            )
    return {
        "functions": entries,
        "revision": revision,
        "total_lines": sum(len(lines) for lines in total_by_path.values()),
    }


def _loop_and_validation_phase(
    repository: Path,
    *,
    after_revision: str,
) -> dict[str, object]:
    before = _loop_and_validation_inventory(
        repository,
        _NUMPY_BASELINE,
        loop_paths=_NUMPY_BEFORE_LOOP_FUNCTIONS,
        validation_paths=_NUMPY_BEFORE_VALIDATION_FUNCTIONS,
    )
    after = _loop_and_validation_inventory(
        repository,
        after_revision,
        loop_paths=_NUMPY_AFTER_LOOP_FUNCTIONS,
        validation_paths=_NUMPY_AFTER_VALIDATION_FUNCTIONS,
    )
    return {
        "after": after,
        "after_lines": after["total_lines"],
        "before": before,
        "before_lines": before["total_lines"],
        "measurement": "loop_and_validation_statements",
        "measurement_definition": _LOOP_AND_VALIDATION_DEFINITION,
        "name": "tasks_2_to_4_numpy_migration",
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


def build_reduction_evidence(
    repository: Path = REPOSITORY, *, numpy_after_revision: str = _NUMPY_AFTER
) -> dict[str, Any]:
    """Recompute both acceptance categories from immutable Git revisions."""
    root = repository.resolve()
    numerical = _loop_and_validation_phase(
        root,
        after_revision=_full_revision(root, numpy_after_revision),
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


def _inventory_total(side: Mapping[str, object], *, require_roles: bool = False) -> int:
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
        roles = item.get("roles")
        if require_roles and roles not in (
            ["loop_body"],
            ["straight_line_validation"],
            ["loop_body", "straight_line_validation"],
        ):
            raise ValueError("loop-and-validation inventory has invalid function roles")
        if not require_roles and roles is not None:
            raise ValueError("artifact inventory must not carry loop-and-validation roles")
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
            phase.get("measurement") != "loop_and_validation_statements"
            or phase.get("measurement_definition") != _LOOP_AND_VALIDATION_DEFINITION
            for phase in phases
        ):
            raise ValueError("NumPy phase must use the complete loop-and-validation statement metric")
        before_total = 0
        after_total = 0
        phase_paths: list[set[str]] = []
        for phase in phases:
            before = cast(dict[str, object], phase.get("before"))
            after = cast(dict[str, object], phase.get("after"))
            numerical = category["name"] == "numpy_loop_validation"
            before_lines = _inventory_total(before, require_roles=numerical)
            after_lines = _inventory_total(after, require_roles=numerical)
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
    return render_json_document(document)


def _stored_numpy_after_revision(content: bytes) -> str:
    try:
        document = cast(dict[str, object], json.loads(content))
        categories = cast(list[dict[str, object]], document["categories"])
        phases = cast(list[dict[str, object]], categories[0]["phases"])
        after = cast(dict[str, object], phases[0]["after"])
        revision = cast(str, after["revision"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("stored reduction evidence has no readable NumPy after revision") from error
    if _HEX40.fullmatch(revision) is None:
        raise ValueError("stored reduction evidence NumPy after revision is not a full Git identity")
    return revision


def _verify_numpy_sources_match_revision(repository: Path, revision: str) -> None:
    measured = {
        (path, name)
        for inventory in (_NUMPY_AFTER_LOOP_FUNCTIONS, _NUMPY_AFTER_VALIDATION_FUNCTIONS)
        for path, names in inventory.items()
        for name in names
    }
    historical_sources: dict[str, dict[str, ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    current_sources: dict[str, dict[str, ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for historical_path, historical_name in sorted(measured):
        current_path, current_name = _NUMPY_CURRENT_RELOCATIONS.get(
            (historical_path, historical_name), (historical_path, historical_name)
        )
        if historical_path not in historical_sources:
            historical_sources[historical_path] = _qualified_functions(
                ast.parse(_git_source(repository, revision, historical_path), filename=historical_path)
            )
        historical = historical_sources[historical_path].get(historical_name)
        if historical is None:
            raise ValueError(f"missing historical measured function {historical_path}:{historical_name}")
        if current_path not in current_sources:
            try:
                current_text = (repository / current_path).read_text(encoding="utf-8")
            except OSError as error:
                raise ValueError(f"cannot read current relocated source {current_path}: {error}") from error
            current_sources[current_path] = _qualified_functions(ast.parse(current_text, filename=current_path))
        current = current_sources[current_path].get(current_name)
        if current is None:
            raise ValueError(f"missing current relocated function {current_path}:{current_name}")
        if _function_ast_identity(historical) != _function_ast_identity(current):
            raise ValueError(
                "current measured function differs from NumPy after revision: "
                f"{historical_path}:{historical_name} -> {current_path}:{current_name}"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--repository", type=Path, default=REPOSITORY)
    parser.add_argument("--numpy-after", help="full source commit for the post-migration inventory")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    repository = cast(Path, arguments.repository)
    output = cast(Path, arguments.output)
    try:
        requested_revision = cast(str | None, arguments.numpy_after)
        if cast(bool, arguments.check) and requested_revision is None:
            requested_revision = _stored_numpy_after_revision(output.read_bytes())
        after_revision = _full_revision(repository, requested_revision or _NUMPY_AFTER)
        _verify_numpy_sources_match_revision(repository.resolve(), after_revision)
        expected = canonical_json_bytes(build_reduction_evidence(repository, numpy_after_revision=after_revision))
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
