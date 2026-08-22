"""Reporting behavior."""

from __future__ import annotations

import hashlib
import math
import platform as platform
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
import tomli_w

import scripts.validation_study.candidate.reporting as vs_candidate_reporting
import scripts.validation_study.common as vs_common
import scripts.validation_study.evidence as vs_evidence
import scripts.validation_study.prerequisites.codec as vs_prereq_codec
import scripts.validation_study.results.reporting as vs_results_reporting
import scripts.validation_study.rotation.run as vs_rotation_run
import scripts.validation_study.workloads as vs_workloads
import trafficlab.common.config_io as trafficlab_common_config_io
from tests.support.validation_study.builders import changed_config_paths, frozen, write_checked_configs
from tests.support.validation_study.constants import ROOT
from tests.unit.validation.study.protocol._support import expected_base_config, natural_variation_inputs
from trafficlab import USER_AGENT
from trafficlab.common.config import SimilarityConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace, align_generated, normalize_reference
from trafficlab.comparison.metrics import compare_traces
from trafficlab.comparison.schema import ComparisonResult


def test_trace_summary_uses_canonical_events_and_multiscale_direction_totals(tmp_path: Path) -> None:
    config = vs_workloads.build_base_config(
        vs_workloads.workload_specs("https://downloads.example.test/object.bin")[0],
        repository_root=tmp_path,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        capture_image_id=f"sha256:{'d' * 64}",
    )
    reference = TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 60),
            TraceEvent(0.0, Direction.INBOUND, 100),
            TraceEvent(1.0, Direction.OUTBOUND, 200),
            TraceEvent(3.0, Direction.INBOUND, 300),
        )
    )
    generated = TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.INBOUND, 80),
            TraceEvent(0.5, Direction.OUTBOUND, 120),
            TraceEvent(1.5, Direction.INBOUND, 160),
            TraceEvent(3.0, Direction.OUTBOUND, 240),
        )
    )
    comparison = compare_traces(reference, generated, 3.0, config.similarity)

    reference_summary = vs_evidence.trace_summary(reference, comparison, role="reference")
    generated_summary = vs_evidence.trace_summary(generated, comparison, role="generated")

    assert reference_summary == {
        "packet_count": 4,
        "observation_window_seconds": 3.0,
        "packet_totals": {"outbound": 2, "inbound": 2},
        "byte_totals": {"outbound": 260, "inbound": 400},
        "frame_lengths": {
            "count": 4,
            "minimum": 60.0,
            "median": 150.0,
            "quantile_probability": 0.95,
            "quantile": 300.0,
            "maximum": 300.0,
            "zero_count": 0,
        },
        "iats": {
            "count": 3,
            "minimum": 0.0,
            "median": 1.0,
            "quantile_probability": 0.95,
            "quantile": 2.0,
            "maximum": 2.0,
            "zero_count": 1,
        },
        "scales": [
            {
                "width_seconds": width,
                "bins_per_direction": bins,
                "packet_totals": {"outbound": 2, "inbound": 2},
                "byte_totals": {"outbound": 260, "inbound": 400},
            }
            for width, bins in ((0.001, 3000), (0.01, 300))
        ],
    }
    assert generated_summary["packet_totals"] == {"outbound": 2, "inbound": 2}
    assert generated_summary["byte_totals"] == {"outbound": 360, "inbound": 240}
    assert generated_summary["frame_lengths"] == {
        "count": 4,
        "minimum": 80.0,
        "median": 140.0,
        "quantile_probability": 0.95,
        "quantile": 240.0,
        "maximum": 240.0,
        "zero_count": 0,
    }


def test_natural_variation_compares_each_pair_in_both_directions_and_averages_scores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records, traces, settings, document = natural_variation_inputs(tmp_path)
    calls: list[
        tuple[
            TrafficTrace,
            TrafficTrace,
            float,
            SimilarityConfig,
            ComparisonResult,
        ]
    ] = []

    def comparison_spy(
        reference: TrafficTrace,
        generated: TrafficTrace,
        window: float,
        config: SimilarityConfig,
    ) -> ComparisonResult:
        comparison = compare_traces(reference, generated, window, config)
        calls.append((reference, generated, window, config, comparison))
        return comparison

    monkeypatch.setattr(vs_results_reporting, "compare_traces", comparison_spy)

    variation = vs_results_reporting.natural_variation(records, traces, settings)

    workloads: tuple[vs_common.WorkloadName, ...] = ("short", "streaming", "bursty")
    pairs = ((1, 2), (1, 3), (2, 3))
    assert len(calls) == 18
    call_index = 0
    expected_natural = cast(list[dict[str, object]], document["natural_variation"])
    for workload_index, workload in enumerate(workloads):
        record = variation[workload_index]
        assert record["workload"] == workload
        assert record["reference_descriptors"] == expected_natural[workload_index]["reference_descriptors"]
        result_pairs = cast(list[vs_common.JsonValue], record["pairs"])
        assert (
            tuple(
                (
                    cast(dict[str, vs_common.JsonValue], item)["left_repeat"],
                    cast(dict[str, vs_common.JsonValue], item)["right_repeat"],
                )
                for item in result_pairs
            )
            == pairs
        )
        for pair_index, (left, right) in enumerate(pairs):
            pair = cast(vs_common.JsonObject, result_pairs[pair_index])
            for source_repeat, generated_repeat, field in (
                (left, right, "forward"),
                (right, left, "reverse"),
            ):
                expected_reference, expected_window = normalize_reference(traces[(workload, source_repeat)])
                expected_generated = align_generated(traces[(workload, generated_repeat)], expected_window)
                actual_reference, actual_generated, actual_window, actual_settings, comparison = calls[call_index]
                assert (actual_reference, actual_generated, actual_window) == (
                    expected_reference,
                    expected_generated,
                    expected_window,
                )
                assert actual_settings is settings[workload]
                assert pair[field] == vs_results_reporting.score_from_comparison(comparison)
                call_index += 1
            assert pair["symmetric"] == vs_results_reporting._average_score(  # pyright: ignore[reportPrivateUsage]
                cast(vs_common.JsonObject, pair["forward"]),
                cast(vs_common.JsonObject, pair["reverse"]),
            )


def test_workload_summaries_recompute_runtime_family_score_variance_and_winner_counts(tmp_path: Path) -> None:
    records, _traces, _settings, document = natural_variation_inputs(tmp_path)

    summaries = vs_results_reporting.workload_summaries(tuple(reversed(records)))

    assert summaries == tuple(cast(list[vs_common.JsonObject], document["workload_summaries"]))
    short = summaries[0]
    assert short["runtime"] == vs_results_reporting.descriptive_statistics((1.0, 2.0, 3.0))
    runtime_interval = cast(dict[str, object], cast(dict[str, object], short["runtime"])["bootstrap"])
    assert runtime_interval["confidence_level"] == 0.95
    assert runtime_interval["generator"] == "PCG64"
    assert runtime_interval["method"] == "percentile"
    assert runtime_interval["n_resamples"] == 10_000
    assert runtime_interval["sample_size"] == 3
    assert runtime_interval["statistic"] == "mean"
    families = cast(vs_common.JsonObject, short["family_champions"])
    poisson = cast(vs_common.JsonObject, families["poisson_empirical"])
    assert poisson["selection_fitness"] == vs_results_reporting.descriptive_statistics((0.61, 0.62, 0.63))
    assert short["winner_counts"] == {"markov_renewal": 0, "mmpp": 0, "poisson_empirical": 3}


def test_natural_variation_propagates_metric_precondition_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records, traces, settings, _document = natural_variation_inputs(tmp_path)
    failure = TrafficlabError(
        "natural comparison precondition failed",
        corrective_action="retain the reference evidence",
    )

    def failing_comparison(
        reference: TrafficTrace,
        generated: TrafficTrace,
        window: float,
        config: SimilarityConfig,
    ) -> ComparisonResult:
        del reference, generated, window, config
        raise failure

    monkeypatch.setattr(vs_results_reporting, "compare_traces", failing_comparison)

    with pytest.raises(TrafficlabError, match="natural comparison precondition failed") as captured:
        vs_results_reporting.natural_variation(records, traces, settings)

    assert captured.value is failure


def test_median_quantile_and_descriptive_statistics_use_published_formulas() -> None:
    assert vs_results_reporting.sample_record([1.0, 3.0, 5.0], quantile_probability=0.95, zero_count=0)["median"] == 3.0
    assert (
        vs_results_reporting.sample_record([1.0, 3.0, 5.0, 9.0], quantile_probability=0.95, zero_count=0)["median"]
        == 4.0
    )
    assert (
        vs_results_reporting.sample_record([1.0, 3.0, 5.0, 9.0], quantile_probability=0.5, zero_count=0)["quantile"]
        == 3.0
    )
    descriptive = vs_results_reporting.descriptive_statistics([1, 2, 3])
    assert descriptive == {
        "bootstrap": descriptive["bootstrap"],
        "count": 3,
        "mean": 2.0,
        "minimum": 1.0,
        "maximum": 3.0,
        "range": 2.0,
        "sample_variance": 1.0,
        "sample_standard_deviation": 1.0,
    }
    bootstrap = cast(dict[str, object], descriptive["bootstrap"])
    assert bootstrap["seed"] == vs_common.BOOTSTRAP_SEED
    assert bootstrap["lower_bound"] == 1.0
    assert bootstrap["upper_bound"] == 3.0

    for values in ([], [1, 2], [1, 2, 3, 4], [1, True, 3], [1, math.nan, 3]):
        with pytest.raises(ValueError):
            vs_results_reporting.descriptive_statistics(values)
    with pytest.raises(ValueError, match="nonempty"):
        vs_results_reporting.sample_record([], quantile_probability=0.95, zero_count=0)
    with pytest.raises(ValueError, match="zero count"):
        vs_results_reporting.sample_record([1.0, 2.0], quantile_probability=0.95, zero_count=3)


def test_candidate_sample_summary_retains_bootstrap_and_rejects_invalid_inputs() -> None:
    summary = vs_candidate_reporting._candidate_sample_summary(  # pyright: ignore[reportPrivateUsage]
        [1.0, 2.0, 3.0], name="training runtime"
    )
    bootstrap = cast(dict[str, object], summary["bootstrap"])
    assert summary["mean"] == 2.0
    assert summary["sample_variance"] == 1.0
    assert bootstrap["seed"] == 20_260_819
    assert bootstrap["sample_size"] == 3
    assert bootstrap["n_resamples"] == 10_000

    with pytest.raises(ValueError, match="exactly three"):
        vs_candidate_reporting._candidate_sample_summary([1.0, 2.0], name="training runtime")  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="finite"):
        vs_candidate_reporting._candidate_sample_summary(  # pyright: ignore[reportPrivateUsage]
            [1.0, math.nan, 3.0], name="training runtime"
        )


def test_workload_specs_expand_exact_short_streaming_and_eight_bursty_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://downloads.example.test/object.bin"
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert USER_AGENT == f"{metadata['name']}/{metadata['version']} (+{metadata['urls']['Repository']})"
    common = (
        "--fail",
        "--silent",
        "--show-error",
        "--location",
        "--max-redirs",
        "3",
        "--proto",
        "=https",
        "--proto-redir",
        "=https",
        "--http1.1",
        "--user-agent",
        USER_AGENT,
        "--connect-timeout",
        "15",
    )
    short_argv = (
        *common,
        "--max-time",
        "30",
        "--limit-rate",
        "4M",
        "--range",
        "0-1048575",
        "--max-filesize",
        "1048576",
        "--dump-header",
        "/trafficlab-study/short.headers",
        "--output",
        "/dev/null",
        "--url",
        url,
    )
    streaming_argv = (
        *common,
        "--max-time",
        "40",
        "--limit-rate",
        "256K",
        "--range",
        "0-4194303",
        "--max-filesize",
        "4194304",
        "--dump-header",
        "/trafficlab-study/streaming.headers",
        "--output",
        "/dev/null",
        "--url",
        url,
    )
    starts = (0, 524288, 1048576, 1572864, 2097152, 2621440, 3145728, 3670016)
    bursty_groups: list[str] = []
    for index, start in enumerate(starts):
        if index:
            bursty_groups.append("--next")
        bursty_groups.extend(
            (
                *common,
                "--max-time",
                "30",
                "--range",
                f"{start}-{start + 32767}",
                "--max-filesize",
                "32768",
                "--dump-header",
                f"/trafficlab-study/bursty-{index}.headers",
                "--output",
                "/dev/null",
                "--url",
                url,
            )
        )
    bursty_argv = ("--parallel", "--parallel-max", "4", "--fail-early", *bursty_groups)

    specs = vs_workloads.workload_specs(url)

    assert specs == (
        vs_workloads.WorkloadSpec("short", short_argv, ((0, 1048575, "short.headers"),), 35.0, 90.0, (0.001, 0.01)),
        vs_workloads.WorkloadSpec(
            "streaming",
            streaming_argv,
            ((0, 4194303, "streaming.headers"),),
            50.0,
            120.0,
            (0.25, 1.0),
        ),
        vs_workloads.WorkloadSpec(
            "bursty",
            bursty_argv,
            tuple((start, start + 32767, f"bursty-{index}.headers") for index, start in enumerate(starts)),
            35.0,
            90.0,
            (0.001, 0.01),
        ),
    )
    assert len(specs[2].transfers) == 8
    assert len({filename for _start, _end, filename in specs[2].transfers}) == 8
    assert specs[2].argv[:4] == ("--parallel", "--parallel-max", "4", "--fail-early")
    assert specs[2].argv.count("--next") == 7
    assert specs[2].argv[-1] == url
    assert all("sh" not in spec.argv and "-c" not in spec.argv for spec in specs)
    legacy_short = replace(
        specs[0],
        argv=tuple(
            "0-262143" if item == "0-1048575" else "262144" if item == "1048576" else item for item in specs[0].argv
        ),
        transfers=((0, 262143, "short.headers"),),
    )
    with pytest.raises(ValueError, match="exact HTTPS-only curl profile oracle"):
        vs_workloads._validate_workload_specs(  # pyright: ignore[reportPrivateUsage]
            (legacy_short, specs[1], specs[2]),
            url=url,
        )
    capability_argv = vs_prereq_codec.build_expected_capability_argv("study-1", url)
    capability_user_agent = capability_argv.index("--user-agent")
    assert capability_argv[capability_user_agent : capability_user_agent + 2] == ("--user-agent", USER_AGENT)

    monkeypatch.setattr(vs_workloads, "CURL_COMMON", (*common[:-1], "--proto-redir", "=http"))
    with pytest.raises(ValueError, match="exact HTTPS-only curl profile"):
        vs_workloads.workload_specs(url)


@pytest.mark.parametrize("workload", ["short", "streaming", "bursty"])
def test_base_config_contains_every_locked_value_and_only_profile_differences(
    workload: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    url = "https://downloads.example.test/object.bin"
    capture_image_id = f"sha256:{'d' * 64}"
    specs = {spec.name: spec for spec in vs_workloads.workload_specs(url)}

    config = vs_workloads.build_base_config(
        specs[cast(vs_common.WorkloadName, workload)],
        repository_root=repository_root,
        study_id="study-1",
        url=url,
        capture_image_id=capture_image_id,
    )

    assert config.model_dump(mode="python") == expected_base_config(repository_root, workload)
    all_configs = {
        name: vs_workloads.build_base_config(
            spec,
            repository_root=repository_root,
            study_id="study-1",
            url=url,
            capture_image_id=capture_image_id,
        ).model_dump(mode="python")
        for name, spec in specs.items()
    }
    assert changed_config_paths(all_configs["short"], all_configs["streaming"]) == {
        "run.directory",
        "target.argv",
        "capture.workload_timeout_seconds",
        "capture.total_timeout_seconds",
        "similarity.multiscale_widths_seconds",
    }
    assert changed_config_paths(all_configs["short"], all_configs["bursty"]) == {
        "run.directory",
        "target.argv",
    }


def test_checked_and_realized_configs_reload_to_exact_absolute_oracles(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    prerequisite, contents = write_checked_configs(repository_root)

    validated = vs_rotation_run.validate_base_configs(repository_root, prerequisite)

    assert tuple(validated) == ("short", "streaming", "bursty")
    for name, config in validated.items():
        assert config.model_dump(mode="python") == expected_base_config(repository_root, name)
        assert hashlib.sha256(contents[name]).hexdigest() == prerequisite.config_sha256[name]
    portable = tomllib.loads(contents["short"].decode())
    assert cast(dict[str, object], portable["run"])["directory"] == "../../../runs/validation_study/study-1/01-short-r1"
    target = cast(dict[str, object], portable["target"])
    mount = cast(list[dict[str, object]], target["mounts"])[0]
    assert mount["source"] == "../.study-work/mount/study-1"

    realized_directory = (repository_root / "runs" / "validation_study" / "study-1" / "10-streaming-r2").resolve()
    realized = vs_workloads.config_with_run_directory(validated["streaming"], realized_directory)
    realized_path = repository_root / "runs" / "validation_study" / "study-1" / "realized-configs" / "streaming.toml"
    rendered = vs_workloads.render_realized_config(realized, realized_path)
    assert realized_path.read_bytes() == rendered
    assert trafficlab_common_config_io.load_experiment(realized_path) == realized
    assert str(realized_directory) in rendered.decode()

    with pytest.raises(ValueError, match="already exists"):
        vs_workloads.render_checked_base_config(
            validated["short"],
            repository_root / "examples" / "validation_study" / "configs" / "short.toml",
            repository_root,
        )
    with pytest.raises(ValueError, match="already exists"):
        vs_workloads.render_realized_config(realized, realized_path)


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-capture-image",
        "disabled-family",
        "changed-operator",
        "final-seed-reused",
        "wrong-mount",
        "wrong-profile-argv",
        "unexpected-config-difference",
        "existing-run-directory",
        "missing-checked-config",
    ],
)
def test_config_validation_rejects_every_protocol_change(mutation: str, tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    prerequisite, contents = write_checked_configs(repository_root)
    short_path = repository_root / "examples" / "validation_study" / "configs" / "short.toml"
    short_config = vs_workloads.build_base_config(
        vs_workloads.workload_specs(prerequisite.url)[0],
        repository_root=repository_root,
        study_id=prerequisite.study_id,
        url=prerequisite.url,
        capture_image_id=cast(str, prerequisite.images["capture_image_id"]),
    )

    if mutation == "missing-checked-config":
        short_path.unlink()
    elif mutation == "existing-run-directory":
        short_config.run.directory.mkdir(parents=True)
    else:
        document = tomllib.loads(contents["short"].decode())
        run = cast(dict[str, object], document["run"])
        target = cast(dict[str, object], document["target"])
        capture = cast(dict[str, object], document["capture"])
        models = cast(dict[str, object], document["models"])
        if mutation == "wrong-capture-image":
            capture["image"] = f"sha256:{'e' * 64}"
        elif mutation == "disabled-family":
            models["enabled"] = ["poisson_empirical", "markov_renewal"]
            models.pop("mmpp")
        elif mutation == "changed-operator":
            cast(dict[str, object], models["poisson_empirical"])["mutation_scale"] = 0.2
        elif mutation == "final-seed-reused":
            run["final_seed"] = 17
        elif mutation == "wrong-mount":
            cast(list[dict[str, object]], target["mounts"])[0]["source"] = "../.study-work/mount/other"
        elif mutation == "wrong-profile-argv":
            target["argv"] = ["--url", prerequisite.url]
        elif mutation == "unexpected-config-difference":
            run["master_seed"] = 74
        mutated = tomli_w.dumps(document).encode()
        short_path.write_bytes(mutated)
        hashes = dict(prerequisite.config_sha256)
        hashes["short"] = hashlib.sha256(mutated).hexdigest()
        prerequisite = replace(prerequisite, config_sha256=frozen(hashes))

    with pytest.raises((ValueError, TrafficlabError)):
        vs_rotation_run.validate_base_configs(repository_root, prerequisite)
