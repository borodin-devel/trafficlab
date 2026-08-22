"""Reporting owner for Validation Study tooling."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from statistics import fmean, variance
from typing import cast

from scripts.validation_study.common import (
    BOOTSTRAP_SEED,
    DESCRIPTOR_KEYS,
    FAMILY_ORDER,
    PRIMARY_ORDER,
    PUBLISHED_METHOD_ORDER,
    WORKLOAD_SUMMARY_KEYS,
    JsonObject,
    JsonValue,
    WorkloadName,
    exact_object,
    require,
    require_type,
    strict_float,
    strict_int,
    strict_list,
    strict_string,
    thaw_json,
)
from scripts.validation_study.records import ReproductionRecord, StudyResults, StudyRunRecord
from trafficlab.common.config import FamilyName, SimilarityConfig
from trafficlab.common.statistics import bootstrap_interval
from trafficlab.common.trace import (
    TrafficTrace,
    align_generated,
    normalize_reference,
)
from trafficlab.comparison.metrics import compare_traces
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.fitting.genetic.checkpoint import CheckpointState
from trafficlab.fitting.genetic.types import Candidate, CandidateId, TrialResult
from trafficlab.generation.models.fitted_model import (
    BestModel,
)


def _numeric_sample(values: Sequence[int | float], *, name: str) -> tuple[float, ...]:
    result: list[float] = []
    for value in values:
        if type(value) not in {int, float} or not math.isfinite(value):
            raise ValueError(f"{name} must contain only finite non-boolean numbers")
        result.append(float(value))
    require(bool(result), f"{name} must be nonempty")
    return tuple(result)


def _median(values: Sequence[int | float]) -> float:
    ordered = sorted(_numeric_sample(values, name="median sample"))
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _nearest_rank(values: Sequence[int | float], probability: float) -> float:
    ordered = sorted(_numeric_sample(values, name="quantile sample"))
    probability = strict_float(probability, name="quantile probability", lower=0.0, upper=1.0)
    require(probability > 0.0, "quantile probability must be greater than zero")
    return ordered[math.ceil(probability * len(ordered)) - 1]


def sample_record(values: Sequence[int | float], *, quantile_probability: float, zero_count: int) -> JsonObject:
    sample = _numeric_sample(values, name="sample")
    require(all(value >= 0.0 for value in sample), "sample must contain nonnegative values")
    zero_count = strict_int(zero_count, name="zero count", minimum=0)
    require(zero_count <= len(sample), "zero count must not exceed the sample count")
    return {
        "count": len(sample),
        "minimum": min(sample),
        "median": _median(sample),
        "quantile_probability": strict_float(quantile_probability, name="quantile probability", lower=0.0, upper=1.0),
        "quantile": _nearest_rank(sample, quantile_probability),
        "maximum": max(sample),
        "zero_count": zero_count,
    }


def descriptive_statistics(values: Sequence[int | float]) -> JsonObject:
    sample = _numeric_sample(values, name="descriptive statistics sample")
    require(len(sample) == 3, "descriptive statistics require exactly three observations")
    sample_variance = variance(sample)
    minimum = min(sample)
    maximum = max(sample)
    return {
        "bootstrap": cast(JsonValue, bootstrap_interval(sample, seed=BOOTSTRAP_SEED).as_dict()),
        "count": 3,
        "mean": fmean(sample),
        "minimum": minimum,
        "maximum": maximum,
        "range": maximum - minimum,
        "sample_variance": sample_variance,
        "sample_standard_deviation": math.sqrt(sample_variance),
    }


def score_from_trial(trial: TrialResult) -> JsonObject:
    require_type(type(trial) is TrialResult, "trial score source must be a TrialResult")
    bounded_score(trial.aggregate_score, name="trial aggregate score")
    require(
        tuple(method.name for method in trial.methods) == PUBLISHED_METHOD_ORDER,
        "trial methods must use published method order",
    )
    methods: JsonObject = {}
    for method in trial.methods:
        methods[method.name] = bounded_score(method.score, name=f"trial {method.name} score")
    return {"aggregate": trial.aggregate_score, "methods": methods}


def score_from_comparison(result: ComparisonResult) -> JsonObject:
    require_type(type(result) is ComparisonResult, "published score source must be a ComparisonResult")
    bounded_score(result.aggregate_score, name="comparison aggregate score")
    require(result.methods.keys() == PUBLISHED_METHOD_ORDER, "comparison methods must use published method order")
    return {
        "aggregate": result.aggregate_score,
        "methods": {
            name: bounded_score(result.methods[name].score, name=f"comparison {name} score")
            for name in PUBLISHED_METHOD_ORDER
        },
    }


def _candidate_id(identifier: CandidateId) -> JsonObject:
    require_type(type(identifier) is CandidateId, "candidate identifier must be a CandidateId")
    strict_int(identifier.birth_generation, name="candidate birth generation", minimum=0)
    strict_int(identifier.birth_index, name="candidate birth index", minimum=0)
    return {"birth_generation": identifier.birth_generation, "birth_index": identifier.birth_index}


def _canonical_genes(candidate: Candidate) -> list[int | float]:
    require_type(type(candidate) is Candidate, "candidate must be a Candidate")
    genes_value = candidate.genes
    if genes_value is None:
        raise ValueError("evidence candidate must have canonical genes")
    require(
        candidate.status == "valid" and candidate.invalid is None,
        "evidence candidate must be valid with canonical genes",
    )
    genes = list(genes_value)
    validate_genes(genes, family=candidate.family)
    return genes


def family_champions(state: CheckpointState) -> tuple[JsonObject, JsonObject, JsonObject]:
    require_type(type(state) is CheckpointState, "champion source must be a CheckpointState")
    require(state.terminal_reason != "running", "champions require a terminal checkpoint")
    require(
        tuple(family.name for family in state.compatibility.families) == FAMILY_ORDER,
        "checkpoint must contain all three families in lexical order",
    )
    require(state.compatibility.trial_seeds == (17, 29), "checkpoint selection seeds must be exactly [17, 29]")
    records: list[JsonObject] = []
    for family in FAMILY_ORDER:
        candidates = tuple(
            candidate
            for candidate in state.population
            if candidate.family == family and candidate.status == "valid" and (candidate.genes is not None)
        )
        require(bool(candidates), f"terminal checkpoint has no valid {family} family champion")
        champion = min(candidates, key=lambda item: (-item.fitness, item.identifier))
        require(
            tuple(trial.seed for trial in champion.trials) == (17, 29),
            f"{family} champion trials must use exactly selection seeds [17, 29]",
        )
        aggregate = fmean(trial.aggregate_score for trial in champion.trials)
        require(
            aggregate == champion.fitness, f"{family} champion fitness must equal its mean selection aggregate score"
        )
        method_means = {
            name: fmean(
                next(method.score for method in trial.methods if method.name == name) for trial in champion.trials
            )
            for name in PUBLISHED_METHOD_ORDER
        }
        records.append(
            cast(
                JsonObject,
                {
                    "family": family,
                    "candidate_id": _candidate_id(champion.identifier),
                    "genes": _canonical_genes(champion),
                    "selection_fitness": champion.fitness,
                    "selection_seeds": [17, 29],
                    "selection_score": {"aggregate": aggregate, "methods": method_means},
                },
            )
        )
    return cast(tuple[JsonObject, JsonObject, JsonObject], tuple(records))


def select_winner(state: CheckpointState, best: BestModel) -> JsonObject:
    require_type(type(state) is CheckpointState, "winner source must be a CheckpointState")
    require_type(type(best) is BestModel, "published winner must be a BestModel")
    matches = tuple(candidate for candidate in state.population if candidate.identifier == state.best_identifier)
    require(len(matches) == 1, "checkpoint best identifier must identify exactly one terminal candidate")
    candidate = matches[0]
    genes = _canonical_genes(candidate)
    require(candidate.fitness == state.best_fitness, "checkpoint best fitness must match its identified candidate")
    require(
        candidate.family == best.family and tuple(genes) == best.genes,
        "checkpoint winner family and genes must match the published best model",
    )
    return cast(
        JsonObject,
        {
            "family": candidate.family,
            "candidate_id": _candidate_id(candidate.identifier),
            "genes": genes,
            "selection_fitness": candidate.fitness,
        },
    )


WORKLOAD_ORDER: tuple[WorkloadName, WorkloadName, WorkloadName] = ("short", "streaming", "bursty")


def group_run_documents(records: Sequence[StudyRunRecord]) -> dict[WorkloadName, tuple[JsonObject, ...]]:
    require(
        len(records) == len(PRIMARY_ORDER) and all(type(record) is StudyRunRecord for record in records),
        "study summaries require exactly nine primary run records",
    )
    grouped: dict[WorkloadName, dict[int, JsonObject]] = {workload: {} for workload in WORKLOAD_ORDER}
    for record in records:
        document = study_run_document(record)
        key = cast(JsonObject, document["key"])
        workload_value = strict_string(key.get("workload"), name="primary summary workload")
        repeat_value = strict_int(key.get("repeat"), name="primary summary repeat", minimum=1)
        require(
            workload_value in WORKLOAD_ORDER and repeat_value <= 3,
            "primary summary keys must contain one exact workload and repeat 1..3",
        )
        workload = cast(WorkloadName, workload_value)
        require(repeat_value not in grouped[workload], "primary summary keys must be unique")
        grouped[workload][repeat_value] = document
    for workload in WORKLOAD_ORDER:
        require(set(grouped[workload]) == {1, 2, 3}, f"{workload} summaries require repeats 1, 2, and 3")
    return {workload: tuple(grouped[workload][repeat] for repeat in (1, 2, 3)) for workload in WORKLOAD_ORDER}


def _reference_descriptions(runs: Sequence[JsonObject]) -> JsonObject:
    observations = _descriptor_observations(runs)
    return {key: descriptive_statistics(observations[key]) for key in DESCRIPTOR_KEYS}


def natural_variation(
    records: Sequence[StudyRunRecord],
    traces: Mapping[tuple[WorkloadName, int], TrafficTrace],
    settings: Mapping[WorkloadName, SimilarityConfig],
) -> tuple[JsonObject, JsonObject, JsonObject]:
    grouped = group_run_documents(records)
    expected_trace_keys = {(workload, repeat) for workload in WORKLOAD_ORDER for repeat in (1, 2, 3)}
    require(set(traces) == expected_trace_keys, "natural variation requires exactly nine primary reference traces")
    require(set(settings) == set(WORKLOAD_ORDER), "natural variation requires exact per-workload settings")
    results: list[JsonObject] = []
    for workload in WORKLOAD_ORDER:
        pairs: list[JsonValue] = []
        for left_repeat, right_repeat in ((1, 2), (1, 3), (2, 3)):
            directional: list[JsonObject] = []
            for reference_repeat, generated_repeat in ((left_repeat, right_repeat), (right_repeat, left_repeat)):
                reference, window = normalize_reference(traces[workload, reference_repeat])
                generated = align_generated(traces[workload, generated_repeat], window)
                directional.append(
                    score_from_comparison(compare_traces(reference, generated, window, settings[workload]))
                )
            forward, reverse = directional
            pairs.append(
                {
                    "left_repeat": left_repeat,
                    "right_repeat": right_repeat,
                    "forward": forward,
                    "reverse": reverse,
                    "symmetric": _average_score(forward, reverse),
                }
            )
        results.append(
            {"workload": workload, "pairs": pairs, "reference_descriptors": _reference_descriptions(grouped[workload])}
        )
    return cast(tuple[JsonObject, JsonObject, JsonObject], tuple(results))


def _summarize_scores(scores: Sequence[JsonObject]) -> JsonObject:
    require(len(scores) == 3, "score summaries require exactly three observations")
    methods = [cast(JsonObject, score["methods"]) for score in scores]
    return {
        "aggregate": descriptive_statistics([cast(float, score["aggregate"]) for score in scores]),
        "methods": {
            method: descriptive_statistics([cast(float, values[method]) for values in methods])
            for method in PUBLISHED_METHOD_ORDER
        },
    }


def workload_summaries(records: Sequence[StudyRunRecord]) -> tuple[JsonObject, JsonObject, JsonObject]:
    grouped = group_run_documents(records)
    results: list[JsonObject] = []
    for workload in WORKLOAD_ORDER:
        runs = grouped[workload]
        champions_by_family: dict[FamilyName, list[JsonObject]] = {family: [] for family in FAMILY_ORDER}
        for run in runs:
            champions = cast(list[JsonValue], run["family_champions"])
            for family, champion in zip(FAMILY_ORDER, champions, strict=True):
                champion_document = cast(JsonObject, champion)
                require(champion_document.get("family") == family, "family champions must retain lexical order")
                champions_by_family[family].append(champion_document)
        family_summaries: JsonObject = {}
        for family in FAMILY_ORDER:
            champions = champions_by_family[family]
            selection_scores = [cast(JsonObject, champion["selection_score"]) for champion in champions]
            method_scores = [cast(JsonObject, score["methods"]) for score in selection_scores]
            family_summaries[family] = {
                "selection_fitness": descriptive_statistics(
                    [cast(float, champion["selection_fitness"]) for champion in champions]
                ),
                "selection_components": {
                    method: descriptive_statistics([cast(float, scores[method]) for scores in method_scores])
                    for method in PUBLISHED_METHOD_ORDER
                },
            }
        winners = [cast(JsonObject, run["winner"]) for run in runs]
        fresh_simulation = [cast(JsonObject, cast(JsonObject, run["fresh_simulation"])["score"]) for run in runs]
        published = [cast(JsonObject, cast(JsonObject, run["published"])["score"]) for run in runs]
        results.append(
            {
                "workload": workload,
                "runtime": descriptive_statistics([cast(float, run["elapsed_seconds"]) for run in runs]),
                "family_champions": family_summaries,
                "winner_selection_fitness": descriptive_statistics(
                    [cast(float, winner["selection_fitness"]) for winner in winners]
                ),
                "fresh_simulation": _summarize_scores(fresh_simulation),
                "published": _summarize_scores(published),
                "reference_descriptors": _reference_descriptions(runs),
                "winner_counts": {
                    family: sum(winner["family"] == family for winner in winners) for family in FAMILY_ORDER
                },
            }
        )
    return cast(tuple[JsonObject, JsonObject, JsonObject], tuple(results))


def validate_candidate_id(value: object) -> JsonObject:
    document = exact_object(value, ("birth_generation", "birth_index"), name="candidate ID")
    return {
        "birth_generation": strict_int(document["birth_generation"], name="birth generation", minimum=0),
        "birth_index": strict_int(document["birth_index"], name="birth index", minimum=0),
    }


def validate_genes(value: object, *, family: str) -> list[JsonValue]:
    genes = strict_list(value, name=f"{family} genes")
    if family == "poisson_empirical":
        require(len(genes) == 1, "poisson_empirical genes must have one value")
        strict_float(genes[0], name="poisson c_lambda", lower=0.25, upper=4.0)
    elif family == "markov_renewal":
        require(len(genes) == 5, "markov_renewal genes must have five values")
        strict_float(genes[0], name="markov q1", lower=0.1, upper=0.4)
        strict_float(genes[1], name="markov q2", lower=0.6, upper=0.9)
        strict_float(genes[2], name="markov alpha", lower=0.0, upper=2.0)
        r = strict_int(genes[3], name="markov r")
        require(1 <= r <= 8, "markov r must be in [1, 8]")
        strict_float(genes[4], name="markov c_t", lower=0.25, upper=4.0)
    elif family == "mmpp":
        require(len(genes) == 4, "mmpp genes must have four values")
        strict_float(genes[0], name="MMPP q01", lower=0.01, upper=10.0)
        strict_float(genes[1], name="MMPP q10", lower=0.01, upper=10.0)
        lambda0 = strict_float(genes[2], name="MMPP lambda0", lower=10.0, upper=100.0)
        lambda1 = strict_float(genes[3], name="MMPP lambda1", lower=0.1, upper=1000.0)
        require(lambda0 < lambda1, "MMPP lambda0 must be strictly less than lambda1")
    else:
        raise ValueError("family must be one of the three published model families")
    return cast(list[JsonValue], genes)


def bounded_score(value: object, *, name: str) -> float:
    return strict_float(value, name=name, lower=0.0, upper=1.0)


def _validate_method_scores(value: object, *, name: str = "method scores") -> JsonObject:
    document = exact_object(value, PUBLISHED_METHOD_ORDER, name=name)
    for method in PUBLISHED_METHOD_ORDER:
        bounded_score(document[method], name=f"{name}.{method}")
    return cast(JsonObject, document)


def validate_score(value: object, *, name: str = "score") -> JsonObject:
    document = exact_object(value, ("aggregate", "methods"), name=name)
    bounded_score(document["aggregate"], name=f"{name}.aggregate")
    _validate_method_scores(document["methods"], name=f"{name}.methods")
    return cast(JsonObject, document)


def _validate_descriptive(
    value: object,
    *,
    name: str,
    observations: Sequence[int | float] | None = None,
    historic_schema_one_result: bool = False,
) -> JsonObject:
    legacy_keys = ("count", "mean", "minimum", "maximum", "range", "sample_variance", "sample_standard_deviation")
    keys = legacy_keys if historic_schema_one_result else ("bootstrap", *legacy_keys)
    document = exact_object(value, keys, name=name)
    if observations is not None:
        expected = descriptive_statistics(observations)
        if historic_schema_one_result:
            expected.pop("bootstrap")
        require(document == expected, f"{name} is stale and does not recompute from its three source observations")
        return cast(JsonObject, document)
    if not historic_schema_one_result:
        bootstrap = exact_object(
            document["bootstrap"],
            (
                "confidence_level",
                "generator",
                "generator_state",
                "lower_bound",
                "method",
                "n_resamples",
                "sample_size",
                "seed",
                "statistic",
                "upper_bound",
            ),
            name=f"{name}.bootstrap",
        )
        require(bootstrap["confidence_level"] == 0.95, f"{name}.bootstrap confidence level must be 0.95")
        require(bootstrap["generator"] == "PCG64", f"{name}.bootstrap generator must be PCG64")
        require(bootstrap["method"] == "percentile", f"{name}.bootstrap method must be percentile")
        require(bootstrap["n_resamples"] == 10000, f"{name}.bootstrap resamples must be 10000")
        require(bootstrap["sample_size"] == 3, f"{name}.bootstrap sample size must be three")
        require(bootstrap["seed"] == BOOTSTRAP_SEED, f"{name}.bootstrap seed is not the fixed report seed")
        require(bootstrap["statistic"] == "mean", f"{name}.bootstrap statistic must be mean")
        lower = strict_float(bootstrap["lower_bound"], name=f"{name}.bootstrap lower bound")
        upper = strict_float(bootstrap["upper_bound"], name=f"{name}.bootstrap upper bound")
        require(lower <= upper, f"{name}.bootstrap bounds must not be inverted")
        require(type(bootstrap["generator_state"]) is dict, f"{name}.bootstrap generator state must be an object")
    count = strict_int(document["count"], name=f"{name}.count")
    strict_float(document["mean"], name=f"{name}.mean")
    minimum = strict_float(document["minimum"], name=f"{name}.minimum")
    maximum = strict_float(document["maximum"], name=f"{name}.maximum")
    range_ = strict_float(document["range"], name=f"{name}.range", lower=0.0)
    strict_float(document["sample_variance"], name=f"{name}.sample_variance", lower=0.0)
    strict_float(document["sample_standard_deviation"], name=f"{name}.sample_standard_deviation", lower=0.0)
    require(count == 3, f"{name}.count must be exactly 3")
    require(minimum <= maximum, f"{name} minimum must not exceed maximum")
    require(range_ == maximum - minimum, f"{name}.range must equal maximum minus minimum")
    return cast(JsonObject, document)


def _validate_score_summary(
    value: object, *, name: str, observations: Sequence[JsonObject], historic_schema_one_result: bool = False
) -> JsonObject:
    document = exact_object(value, ("aggregate", "methods"), name=name)
    aggregate_values = [cast(float, score["aggregate"]) for score in observations]
    methods = exact_object(document["methods"], PUBLISHED_METHOD_ORDER, name=f"{name}.methods")
    source_methods = [cast(dict[str, JsonValue], score["methods"]) for score in observations]
    _validate_descriptive(
        document["aggregate"],
        name=f"{name}.aggregate",
        observations=aggregate_values,
        historic_schema_one_result=historic_schema_one_result,
    )
    for method in PUBLISHED_METHOD_ORDER:
        _validate_descriptive(
            methods[method],
            name=f"{name}.methods.{method}",
            observations=[cast(float, values[method]) for values in source_methods],
            historic_schema_one_result=historic_schema_one_result,
        )
    return cast(JsonObject, document)


def _descriptor_observations(runs: Sequence[JsonObject]) -> dict[str, list[int | float]]:
    references = [cast(dict[str, JsonValue], run["reference"]) for run in runs]
    packet_totals = [cast(dict[str, JsonValue], reference["packet_totals"]) for reference in references]
    byte_totals = [cast(dict[str, JsonValue], reference["byte_totals"]) for reference in references]
    result: dict[str, list[int | float]] = {
        "packet_count": [cast(int, reference["packet_count"]) for reference in references],
        "observation_window_seconds": [
            cast(float, reference["observation_window_seconds"]) for reference in references
        ],
        "outbound_packets": [cast(int, totals["outbound"]) for totals in packet_totals],
        "inbound_packets": [cast(int, totals["inbound"]) for totals in packet_totals],
        "outbound_bytes": [cast(int, totals["outbound"]) for totals in byte_totals],
        "inbound_bytes": [cast(int, totals["inbound"]) for totals in byte_totals],
    }
    return result


def _validate_descriptors(
    value: object, *, name: str, runs: Sequence[JsonObject], historic_schema_one_result: bool = False
) -> JsonObject:
    document = exact_object(value, DESCRIPTOR_KEYS, name=name)
    observations = _descriptor_observations(runs)
    for key in DESCRIPTOR_KEYS:
        _validate_descriptive(
            document[key],
            name=f"{name}.{key}",
            observations=observations[key],
            historic_schema_one_result=historic_schema_one_result,
        )
    return cast(JsonObject, document)


def _average_score(forward: JsonObject, reverse: JsonObject) -> JsonObject:
    forward_methods = cast(dict[str, JsonValue], forward["methods"])
    reverse_methods = cast(dict[str, JsonValue], reverse["methods"])
    return {
        "aggregate": (cast(float, forward["aggregate"]) + cast(float, reverse["aggregate"])) / 2.0,
        "methods": {
            method: (cast(float, forward_methods[method]) + cast(float, reverse_methods[method])) / 2.0
            for method in PUBLISHED_METHOD_ORDER
        },
    }


def validate_natural_variation(
    value: object, *, workload: str, runs: Sequence[JsonObject], historic_schema_one_result: bool = False
) -> JsonObject:
    document = exact_object(value, ("workload", "pairs", "reference_descriptors"), name="natural variation")
    name = strict_string(document["workload"], name="natural variation workload")
    require(name == workload, "natural variation records must be ordered short, streaming, bursty")
    pair_items = strict_list(document["pairs"], name="natural variation pairs")
    expected_pairs = ((1, 2), (1, 3), (2, 3))
    require(len(pair_items) == 3, "natural variation must contain exactly three unordered repeat pairs")
    for item, expected in zip(pair_items, expected_pairs, strict=True):
        pair = exact_object(
            item, ("left_repeat", "right_repeat", "forward", "reverse", "symmetric"), name="pair comparison"
        )
        left = strict_int(pair["left_repeat"], name="left repeat")
        right = strict_int(pair["right_repeat"], name="right repeat")
        require((left, right) == expected, "natural variation pair order must be (1,2), (1,3), (2,3)")
        forward = validate_score(pair["forward"], name="forward pair score")
        reverse = validate_score(pair["reverse"], name="reverse pair score")
        symmetric = validate_score(pair["symmetric"], name="symmetric pair score")
        require(
            symmetric == _average_score(forward, reverse),
            "symmetric pair score must be the arithmetic mean of forward and reverse",
        )
    _validate_descriptors(
        document["reference_descriptors"],
        name="natural reference descriptors",
        runs=runs,
        historic_schema_one_result=historic_schema_one_result,
    )
    return cast(JsonObject, document)


def _validate_family_summary(
    value: object, *, family: str, champions: Sequence[JsonObject], historic_schema_one_result: bool = False
) -> JsonObject:
    document = exact_object(value, ("selection_fitness", "selection_components"), name="family summary")
    fitness_values = [cast(float, champion["selection_fitness"]) for champion in champions]
    component_maps = [
        cast(dict[str, JsonValue], cast(dict[str, JsonValue], champion["selection_score"])["methods"])
        for champion in champions
    ]
    components = exact_object(
        document["selection_components"], PUBLISHED_METHOD_ORDER, name=f"{family} selection components"
    )
    _validate_descriptive(
        document["selection_fitness"],
        name=f"{family} selection fitness",
        observations=fitness_values,
        historic_schema_one_result=historic_schema_one_result,
    )
    for method in PUBLISHED_METHOD_ORDER:
        _validate_descriptive(
            components[method],
            name=f"{family} selection component {method}",
            observations=[cast(float, values[method]) for values in component_maps],
            historic_schema_one_result=historic_schema_one_result,
        )
    return cast(JsonObject, document)


def validate_workload_summary(
    value: object, *, workload: str, runs: Sequence[JsonObject], historic_schema_one_result: bool = False
) -> JsonObject:
    document = exact_object(value, WORKLOAD_SUMMARY_KEYS, name="workload summary")
    name = strict_string(document["workload"], name="workload summary name")
    require(name == workload, "workload summaries must be ordered short, streaming, bursty")
    families = exact_object(document["family_champions"], FAMILY_ORDER, name="family summary map")
    champions_by_family: dict[str, list[JsonObject]] = {family: [] for family in FAMILY_ORDER}
    for run in runs:
        champions = cast(list[JsonValue], run["family_champions"])
        for family, champion in zip(FAMILY_ORDER, champions, strict=True):
            champions_by_family[family].append(cast(JsonObject, champion))
    winners = [cast(dict[str, JsonValue], run["winner"]) for run in runs]
    held_scores = [cast(JsonObject, cast(dict[str, JsonValue], run["fresh_simulation"])["score"]) for run in runs]
    published_scores = [cast(JsonObject, cast(dict[str, JsonValue], run["published"])["score"]) for run in runs]
    counts_document = exact_object(document["winner_counts"], FAMILY_ORDER, name="winner counts")
    counts = {
        family: strict_int(counts_document[family], name=f"winner count {family}", minimum=0) for family in FAMILY_ORDER
    }
    expected_counts = {family: sum(winner["family"] == family for winner in winners) for family in FAMILY_ORDER}
    require(counts == expected_counts, "winner counts must recompute from the three selected winners and sum to three")
    _validate_descriptive(
        document["runtime"],
        name=f"{name} runtime",
        observations=[cast(float, run["elapsed_seconds"]) for run in runs],
        historic_schema_one_result=historic_schema_one_result,
    )
    for family in FAMILY_ORDER:
        _validate_family_summary(
            families[family],
            family=family,
            champions=champions_by_family[family],
            historic_schema_one_result=historic_schema_one_result,
        )
    _validate_descriptive(
        document["winner_selection_fitness"],
        name=f"{name} winner selection fitness",
        observations=[cast(float, winner["selection_fitness"]) for winner in winners],
        historic_schema_one_result=historic_schema_one_result,
    )
    _validate_score_summary(
        document["fresh_simulation"],
        name=f"{name} fresh simulation",
        observations=held_scores,
        historic_schema_one_result=historic_schema_one_result,
    )
    _validate_score_summary(
        document["published"],
        name=f"{name} published",
        observations=published_scores,
        historic_schema_one_result=historic_schema_one_result,
    )
    _validate_descriptors(
        document["reference_descriptors"],
        name=f"{name} reference descriptors",
        runs=runs,
        historic_schema_one_result=historic_schema_one_result,
    )
    return cast(JsonObject, document)


def study_run_document(value: StudyRunRecord) -> JsonObject:
    require_type(type(value) is StudyRunRecord, "study run value must be StudyRunRecord")
    return {
        "execution_order": value.execution_order,
        "run_id": value.run_id,
        "key": thaw_json(value.key),
        "config_path": value.config_path,
        "run_directory": value.run_directory,
        "transfer_evidence_directory": value.transfer_evidence_directory,
        "elapsed_seconds": value.elapsed_seconds,
        "reuse": thaw_json(value.reuse),
        "cleanup_verified": value.cleanup_verified,
        "transfer_responses": [thaw_json(item) for item in value.transfer_responses],
        "artifact_sha256": thaw_json(value.artifact_sha256),
        "reference": thaw_json(value.reference),
        "generated": thaw_json(value.generated),
        "family_champions": [thaw_json(item) for item in value.family_champions],
        "winner": thaw_json(value.winner),
        "fresh_simulation": thaw_json(value.fresh_simulation),
        "published": thaw_json(value.published),
        "raw_sequence": thaw_json(value.raw_sequence),
    }


def _reproduction_document(value: ReproductionRecord) -> JsonObject:
    require_type(type(value) is ReproductionRecord, "reproduction value must be ReproductionRecord")
    return cast(JsonObject, thaw_json(value.document))


def study_document(value: StudyResults) -> JsonObject:
    require_type(type(value) is StudyResults, "study result value must be StudyResults")
    return {
        "schema_version": value.schema_version,
        "environment": thaw_json(value.environment),
        "protocol": thaw_json(value.protocol),
        "runs": [study_run_document(run) for run in value.runs],
        "natural_variation": [thaw_json(item) for item in value.natural_variation],
        "workload_summaries": [thaw_json(item) for item in value.workload_summaries],
        "reproduction": _reproduction_document(value.reproduction),
    }


def score_delta(reproduction: JsonObject, source: JsonObject) -> JsonObject:
    reproduction_methods = cast(JsonObject, reproduction["methods"])
    source_methods = cast(JsonObject, source["methods"])
    return {
        "aggregate": cast(float, reproduction["aggregate"]) - cast(float, source["aggregate"]),
        "methods": {
            method: cast(float, reproduction_methods[method]) - cast(float, source_methods[method])
            for method in PUBLISHED_METHOD_ORDER
        },
    }


def symmetric_reference_score(
    source: TrafficTrace, reproduction: TrafficTrace, settings: SimilarityConfig
) -> JsonObject:
    scores: list[JsonObject] = []
    for reference_events, generated_events in ((source, reproduction), (reproduction, source)):
        reference, window = normalize_reference(reference_events)
        generated = align_generated(generated_events, window)
        scores.append(score_from_comparison(compare_traces(reference, generated, window, settings)))
    return _average_score(scores[0], scores[1])
