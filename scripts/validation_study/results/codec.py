"""Codec owner for Validation Study tooling."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import cast

from scripts.validation_study.common import (
    ARTIFACT_NAMES,
    FAMILY_ORDER,
    HISTORIC_PUBLISHED_METHOD_ORDER,
    HISTORIC_SCHEMA_ONE_RESULT_COMMIT,
    HISTORIC_SCHEMA_ONE_RESULT_STUDY_ID,
    HISTORIC_SCHEMA_ONE_RESULT_URL,
    ORACLE_URL,
    PRIMARY_ORDER,
    PROTOCOL_KEYS,
    PUBLISHED_METHOD_ORDER,
    RAW_SEQUENCE_KEYS,
    REPOSITORY_ROOT,
    REPRODUCTION_COMPARISON_KEYS,
    REPRODUCTION_KEYS,
    RESULT_ROOT_KEYS,
    RUNTIME_BOUNDARY,
    STUDY_RUN_KEYS,
    TARGET_REFERENCE,
    TRANSFER_RESPONSE_KEYS,
    FrozenJsonObject,
    JsonObject,
    JsonValue,
    WorkloadName,
    exact_object,
    freeze_object,
    image_id_value,
    load_json,
    profile_hashes,
    publish_support_json,
    repository_relative_path,
    require,
    sha256,
    strict_bool,
    strict_float,
    strict_int,
    strict_list,
    strict_string,
    string_array,
    validate_endpoint_url,
    validate_study_id,
)
from scripts.validation_study.prerequisites.codec import (
    historic_schema_one_workload_specs,
    historic_schema_one_workload_transfers,
    validate_capability,
)
from scripts.validation_study.prerequisites.commands import guard_prefix
from scripts.validation_study.records import ReproductionRecord, StudyResults, run_record_from_document
from scripts.validation_study.results.environment import validate_environment
from scripts.validation_study.results.reporting import (
    bounded_score,
    render_study_document,
    study_document,
    validate_candidate_id,
    validate_genes,
    validate_natural_variation,
    validate_score,
    validate_workload_summary,
)
from scripts.validation_study.workloads import workload_specs


def _validate_run_key(value: object, *, name: str = "run key") -> JsonObject:
    document = exact_object(value, ("workload", "repeat"), name=name)
    workload = strict_string(document["workload"], name=f"{name}.workload")
    require(workload in {"short", "streaming", "bursty"}, f"{name}.workload must be short, streaming, or bursty")
    repeat = strict_int(document["repeat"], name=f"{name}.repeat")
    require(1 <= repeat <= 3, f"{name}.repeat must be in 1..3")
    return {"workload": workload, "repeat": repeat}


def _validate_direction_values(value: object, *, name: str, positive_total: bool = False) -> JsonObject:
    document = exact_object(value, ("outbound", "inbound"), name=name)
    outbound = strict_int(document["outbound"], name=f"{name}.outbound", minimum=0)
    inbound = strict_int(document["inbound"], name=f"{name}.inbound", minimum=0)
    require(not positive_total or outbound + inbound > 0, f"{name} must have a positive total")
    return cast(JsonObject, document)


def _validate_sample(value: object, *, name: str, expected_count: int, frame_lengths: bool) -> JsonObject:
    keys = ("count", "minimum", "median", "quantile_probability", "quantile", "maximum", "zero_count")
    document = exact_object(value, keys, name=name)
    count = strict_int(document["count"], name=f"{name}.count", minimum=1)
    require(count == expected_count, f"{name}.count must equal {expected_count}")
    minimum = strict_float(document["minimum"], name=f"{name}.minimum", lower=0.0)
    median = strict_float(document["median"], name=f"{name}.median", lower=0.0)
    probability = strict_float(document["quantile_probability"], name=f"{name}.quantile_probability")
    quantile = strict_float(document["quantile"], name=f"{name}.quantile", lower=0.0)
    maximum = strict_float(document["maximum"], name=f"{name}.maximum", lower=0.0)
    zero_count = strict_int(document["zero_count"], name=f"{name}.zero_count", minimum=0)
    require(probability == 0.95, f"{name}.quantile_probability must be exactly 0.95")
    require(
        minimum <= median <= maximum and minimum <= quantile <= maximum,
        f"{name} median and quantile must lie between minimum and maximum",
    )
    require(
        zero_count <= count and (not frame_lengths or zero_count == 0),
        f"{name}.zero_count is inconsistent with its sample",
    )
    require(not frame_lengths or minimum > 0.0, f"{name} lengths must be positive")
    return cast(JsonObject, document)


def _workload_widths(workload: str, *, historic_schema_one_result: bool = False) -> tuple[float, float]:
    specs = historic_schema_one_workload_specs() if historic_schema_one_result else workload_specs(ORACLE_URL)
    return next(spec.multiscale_widths_seconds for spec in specs if spec.name == workload)


def _validate_scale(
    value: object, *, expected_width: float, packet_totals: JsonObject, byte_totals: JsonObject
) -> JsonObject:
    document = exact_object(
        value, ("width_seconds", "bins_per_direction", "packet_totals", "byte_totals"), name="scale total"
    )
    width = strict_float(document["width_seconds"], name="scale width", lower=0.0)
    require(width == expected_width, "scale width must equal the configured positive width")
    strict_int(document["bins_per_direction"], name="scale bins per direction", minimum=1)
    packets = _validate_direction_values(document["packet_totals"], name="scale packet totals")
    bytes_ = _validate_direction_values(document["byte_totals"], name="scale byte totals", positive_total=True)
    require(
        packets == packet_totals and bytes_ == byte_totals, "scale direction totals must equal trace direction totals"
    )
    return cast(JsonObject, document)


def _validate_trace(value: object, *, workload: str, name: str, historic_schema_one_result: bool = False) -> JsonObject:
    keys = (
        "packet_count",
        "observation_window_seconds",
        "packet_totals",
        "byte_totals",
        "frame_lengths",
        "iats",
        "scales",
    )
    document = exact_object(value, keys, name=name)
    packet_count = strict_int(document["packet_count"], name=f"{name}.packet_count", minimum=2)
    window = strict_float(document["observation_window_seconds"], name=f"{name}.window", lower=0.0)
    require(window > 0.0, f"{name}.observation_window_seconds must be positive")
    packets = _validate_direction_values(document["packet_totals"], name=f"{name}.packet totals")
    bytes_ = _validate_direction_values(document["byte_totals"], name=f"{name}.byte totals", positive_total=True)
    require(
        cast(int, packets["outbound"]) + cast(int, packets["inbound"]) == packet_count,
        f"{name} packet totals must sum to packet count",
    )
    _validate_sample(
        document["frame_lengths"], name=f"{name}.frame lengths", expected_count=packet_count, frame_lengths=True
    )
    _validate_sample(document["iats"], name=f"{name}.IATs", expected_count=packet_count - 1, frame_lengths=False)
    scales = strict_list(document["scales"], name=f"{name}.scales")
    widths = _workload_widths(workload, historic_schema_one_result=historic_schema_one_result)
    require(len(scales) == len(widths), f"{name}.scales must contain the exact configured widths")
    for scale, width in zip(scales, widths, strict=True):
        _validate_scale(scale, expected_width=width, packet_totals=packets, byte_totals=bytes_)
    return cast(JsonObject, document)


def _expected_transfers(workload: str, *, historic_schema_one_result: bool = False) -> tuple[tuple[int, int, str], ...]:
    if historic_schema_one_result:
        return historic_schema_one_workload_transfers(workload)
    return next(spec.transfers for spec in workload_specs(ORACLE_URL) if spec.name == workload)


def validate_transfer_responses(
    value: object,
    *,
    repository_root: Path,
    workload: str,
    evidence_directory: str,
    object_size: int,
    historic_schema_one_result: bool = False,
) -> list[JsonValue]:
    responses = strict_list(value, name="transfer responses")
    expected = _expected_transfers(workload, historic_schema_one_result=historic_schema_one_result)
    require(len(responses) == len(expected), "transfer responses must contain the exact workload response count")
    paths: set[str] = set()
    for index, (response, (expected_start, expected_end, filename)) in enumerate(zip(responses, expected, strict=True)):
        document = exact_object(response, TRANSFER_RESPONSE_KEYS, name="transfer response")
        transfer_index = strict_int(document["transfer_index"], name="transfer index")
        start = strict_int(document["requested_start"], name="requested start", minimum=0)
        end = strict_int(document["requested_end"], name="requested end", minimum=0)
        status = strict_int(document["status"], name="transfer status")
        length = strict_int(document["content_length"], name="transfer content length", minimum=1)
        require(
            (transfer_index, start, end) == (index, expected_start, expected_end),
            "transfer response index and range must equal the exact workload transfer",
        )
        require(
            status == 206 and length == end - start + 1,
            "transfer response must have status 206 and exact inclusive content length",
        )
        content_range = strict_string(document["content_range"], name="transfer content range")
        require(
            content_range == f"bytes {start}-{end}/{object_size}",
            "transfer content range must equal its requested range and capability object size",
        )
        path = repository_relative_path(
            document["header_archive_path"], repository_root=repository_root, name="header archive path"
        )
        require(
            path == f"{evidence_directory}/{filename}" and path not in paths,
            "header archive path must be unique beneath the transfer evidence directory",
        )
        paths.add(path)
        scratch_mode = strict_int(document["scratch_precreate_mode"], name="scratch precreate mode")
        archive_mode = strict_int(document["archive_mode"], name="header archive mode")
        inode = strict_bool(document["inode_preserved"], name="header inode preservation")
        require(
            scratch_mode == 438 and archive_mode == 384 and inode,
            "transfer response must preserve inode and exact 0666/0600 modes",
        )
        sha256(document["header_sha256"], name="header SHA-256")
    return cast(list[JsonValue], responses)


def _validate_family_champion(
    value: object, *, expected_family: str, historic_schema_one_result: bool = False
) -> JsonObject:
    keys = ("family", "candidate_id", "genes", "selection_fitness", "selection_seeds", "selection_score")
    document = exact_object(value, keys, name="family champion")
    family = strict_string(document["family"], name="champion family")
    require(family == expected_family, "family champions must use exact lexical family order")
    seeds = strict_list(document["selection_seeds"], name="selection seeds")
    require(
        tuple(strict_int(seed, name="selection seed") for seed in seeds) == (17, 29),
        "family champion selection seeds must be exactly [17, 29]",
    )
    fitness = bounded_score(document["selection_fitness"], name="selection fitness")
    score = validate_score(
        document["selection_score"],
        name="selection score",
        historic_schema_one_result=historic_schema_one_result,
    )
    require(score["aggregate"] == fitness, "family champion selection score aggregate must equal selection fitness")
    validate_candidate_id(document["candidate_id"])
    validate_genes(document["genes"], family=family)
    return cast(JsonObject, document)


def _validate_champions(value: object, *, historic_schema_one_result: bool = False) -> list[JsonValue]:
    champions = strict_list(value, name="family champions")
    require(len(champions) == 3, "family champions must contain all three families")
    for champion, family in zip(champions, FAMILY_ORDER, strict=True):
        _validate_family_champion(
            champion, expected_family=family, historic_schema_one_result=historic_schema_one_result
        )
    return cast(list[JsonValue], champions)


def _validate_winner(value: object, *, champions: Sequence[JsonValue]) -> JsonObject:
    keys = ("family", "candidate_id", "genes", "selection_fitness")
    document = exact_object(value, keys, name="winner")
    family = strict_string(document["family"], name="winner family")
    validate_candidate_id(document["candidate_id"])
    validate_genes(document["genes"], family=family)
    bounded_score(document["selection_fitness"], name="winner selection fitness")
    champion_objects = [cast(JsonObject, champion) for champion in champions]
    expected = min(
        champion_objects,
        key=lambda champion: (
            -cast(float, champion["selection_fitness"]),
            cast(int, cast(dict[str, JsonValue], champion["candidate_id"])["birth_generation"]),
            cast(int, cast(dict[str, JsonValue], champion["candidate_id"])["birth_index"]),
        ),
    )
    require(document == {key: expected[key] for key in keys}, "winner must be the stable overall best family champion")
    return cast(JsonObject, document)


def _validate_fresh_simulation(
    value: object, *, expected_source: str, historic_schema_one_result: bool = False
) -> JsonObject:
    document = exact_object(value, ("seed", "score", "source"), name="fresh simulation record")
    seed = strict_int(document["seed"], name="fresh simulation seed")
    source = strict_string(document["source"], name="fresh simulation source")
    require(
        seed == 97 and source == expected_source,
        f"fresh simulation evidence must use seed 97 and source {expected_source}",
    )
    validate_score(
        document["score"], name="fresh simulation score", historic_schema_one_result=historic_schema_one_result
    )
    return cast(JsonObject, document)


def _validate_published(value: object, *, historic_schema_one_result: bool = False) -> JsonObject:
    document = exact_object(value, ("seed", "score"), name="published record")
    seed = strict_int(document["seed"], name="published seed")
    require(seed == 97, "published seed must be exactly 97")
    validate_score(document["score"], name="published score", historic_schema_one_result=historic_schema_one_result)
    return cast(JsonObject, document)


def _validate_raw_sequence(value: object, *, reference: JsonObject, generated: JsonObject) -> JsonObject:
    document = exact_object(value, RAW_SEQUENCE_KEYS, name="raw sequence")
    seed = strict_int(document["seed"], name="raw sequence seed")
    window = strict_float(document["observation_window_seconds"], name="raw sequence window", lower=0.0)
    trial_count = strict_int(document["trial_event_count"], name="trial event count", minimum=1)
    final_count = strict_int(document["final_event_count"], name="final event count", minimum=1)
    reparsed_count = strict_int(document["reparsed_event_count"], name="reparsed event count", minimum=1)
    raw_equal = strict_bool(document["raw_events_equal"], name="raw event equality")
    score_reproduced = strict_bool(
        document["fresh_simulation_score_reproduced"], name="fresh simulation score reproduction"
    )
    reparsed_equal = strict_bool(document["reparsed_matches_quantized"], name="reparsed generated equality")
    require(
        seed == 97 and window == reference["observation_window_seconds"] == generated["observation_window_seconds"],
        "raw sequence must use seed 97 and all raw/reference/generated observation windows must match",
    )
    require(
        trial_count == final_count == reparsed_count == generated["packet_count"],
        "raw sequence trial/final/reparsed/generated event counts must all match",
    )
    require(
        raw_equal and score_reproduced and reparsed_equal,
        "raw sequence equality and reproduction proofs must all be true",
    )
    return cast(JsonObject, document)


def _validate_reuse(value: object) -> JsonObject:
    keys = ("capture", "best_model", "generated", "similarity")
    document = exact_object(value, keys, name="reuse")
    for key in keys:
        strict_bool(document[key], name=f"reuse.{key}")
    require(not any(cast(bool, document[key]) for key in keys), "fresh study reuse fields must all be false")
    return cast(JsonObject, document)


def _validate_artifact_hashes(value: object) -> JsonObject:
    document = exact_object(value, ARTIFACT_NAMES, name="artifact hashes")
    for name in ARTIFACT_NAMES:
        sha256(document[name], name=f"artifact hash {name}")
    return cast(JsonObject, document)


def validate_run_evidence(
    document: JsonObject,
    *,
    repository_root: Path,
    workload: WorkloadName,
    evidence_directory: str,
    object_size: int,
    fresh_simulation_source: str,
    historic_schema_one_result: bool = False,
) -> None:
    elapsed = strict_float(document["elapsed_seconds"], name="run elapsed seconds", lower=0.0)
    require(elapsed > 0.0, "run elapsed seconds must be positive")
    cleanup = strict_bool(document["cleanup_verified"], name="run cleanup verification")
    require(cleanup, "run cleanup must be verified")
    reference = _validate_trace(
        document["reference"],
        workload=workload,
        name="reference trace",
        historic_schema_one_result=historic_schema_one_result,
    )
    generated = _validate_trace(
        document["generated"],
        workload=workload,
        name="generated trace",
        historic_schema_one_result=historic_schema_one_result,
    )
    champions = _validate_champions(document["family_champions"], historic_schema_one_result=historic_schema_one_result)
    _validate_reuse(document["reuse"])
    validate_transfer_responses(
        document["transfer_responses"],
        repository_root=repository_root,
        workload=workload,
        evidence_directory=evidence_directory,
        object_size=object_size,
        historic_schema_one_result=historic_schema_one_result,
    )
    _validate_artifact_hashes(document["artifact_sha256"])
    _validate_winner(document["winner"], champions=champions)
    _validate_fresh_simulation(
        document["fresh_simulation"],
        expected_source=fresh_simulation_source,
        historic_schema_one_result=historic_schema_one_result,
    )
    _validate_published(document["published"], historic_schema_one_result=historic_schema_one_result)
    _validate_raw_sequence(document["raw_sequence"], reference=reference, generated=generated)


def validate_run_document(
    value: object,
    *,
    expected: tuple[int, str, str, int],
    repository_root: Path,
    study_id: str,
    object_size: int,
    historic_schema_one_result: bool = False,
) -> JsonObject:
    document = exact_object(value, STUDY_RUN_KEYS, name="study run")
    order = strict_int(document["execution_order"], name="execution order")
    run_id = strict_string(document["run_id"], name="run ID")
    key = _validate_run_key(document["key"])
    require(
        (order, run_id, key["workload"], key["repeat"]) == expected,
        "primary runs must use the exact balanced primary order and unique run keys",
    )
    workload = cast(str, key["workload"])
    config_path = repository_relative_path(
        document["config_path"], repository_root=repository_root, name="run config path"
    )
    run_directory = repository_relative_path(
        document["run_directory"], repository_root=repository_root, name="run directory"
    )
    evidence_directory = repository_relative_path(
        document["transfer_evidence_directory"], repository_root=repository_root, name="transfer evidence directory"
    )
    require(
        config_path == f"runs/validation_study/{study_id}/realized-configs/{run_id}.toml",
        "primary config path must equal its exact realized config path",
    )
    require(
        run_directory == f"runs/validation_study/{study_id}/{run_id}",
        "primary run directory must equal its exact run path",
    )
    require(
        evidence_directory == f"examples/validation_study/.study-work/evidence/{study_id}/{run_id}",
        "primary transfer evidence directory must equal its exact sibling evidence path",
    )
    validate_run_evidence(
        cast(JsonObject, document),
        repository_root=repository_root,
        workload=cast(WorkloadName, workload),
        evidence_directory=evidence_directory,
        object_size=object_size,
        fresh_simulation_source="run_experiment_fit_outcome",
        historic_schema_one_result=historic_schema_one_result,
    )
    return cast(JsonObject, document)


def _validate_seeds(value: object) -> JsonObject:
    document = exact_object(value, ("master", "final", "selection"), name="seeds")
    master = strict_int(document["master"], name="master seed")
    final = strict_int(document["final"], name="final seed")
    selection = tuple(
        strict_int(seed, name="selection seed") for seed in strict_list(document["selection"], name="selection")
    )
    require(
        (master, final, selection) == (73, 97, (17, 29)),
        "study seeds must be master 73, final 97, selection [17, 29], with final fresh simulation",
    )
    return cast(JsonObject, document)


def _validate_workloads(value: object, *, url: str, historic_schema_one_result: bool = False) -> list[JsonValue]:
    items = strict_list(value, name="workload definitions")
    expected_specs = historic_schema_one_workload_specs() if historic_schema_one_result else workload_specs(url)
    require(len(items) == 3, "workload definitions must contain short, streaming, and bursty")
    keys = ("name", "argv", "workload_timeout_seconds", "total_timeout_seconds", "multiscale_widths_seconds")
    for _index, (item, expected) in enumerate(zip(items, expected_specs, strict=True)):
        document = exact_object(item, keys, name="workload definition")
        name = strict_string(document["name"], name="workload name")
        require(name == expected.name, "workload definitions must be ordered short, streaming, bursty")
        argv = string_array(document["argv"], name=f"{name} argv", nonempty=True)
        workload_timeout = strict_float(document["workload_timeout_seconds"], name=f"{name} workload timeout")
        total_timeout = strict_float(document["total_timeout_seconds"], name=f"{name} total timeout")
        widths = tuple(
            strict_float(width, name=f"{name} multiscale width", lower=0.0)
            for width in strict_list(document["multiscale_widths_seconds"], name=f"{name} widths")
        )
        actual = (argv, workload_timeout, total_timeout, widths)
        oracle = (
            expected.argv,
            expected.workload_timeout_seconds,
            expected.total_timeout_seconds,
            expected.multiscale_widths_seconds,
        )
        require(actual == oracle, f"{name} workload definition must equal the exact workload oracle")
    return cast(list[JsonValue], items)


def _validate_protocol(value: object, *, repository_root: Path, historic_schema_one_result: bool = False) -> JsonObject:
    document = exact_object(value, PROTOCOL_KEYS, name="protocol")
    study_id = validate_study_id(strict_string(document["study_id"], name="protocol study ID"))
    url = validate_endpoint_url(strict_string(document["url"], name="protocol URL"))
    if historic_schema_one_result:
        require(
            study_id == HISTORIC_SCHEMA_ONE_RESULT_STUDY_ID and url == HISTORIC_SCHEMA_ONE_RESULT_URL,
            "historic schema-1 protocol identity must equal the sole retained study ID and URL",
        )
    validate_capability(
        document["capability"],
        repository_root=repository_root,
        study_id=study_id,
        url=url,
        historic_schema_one_result=historic_schema_one_result,
    )
    target_reference = strict_string(document["target_reference"], name="protocol target reference")
    require(
        target_reference == TARGET_REFERENCE, "protocol target reference must equal the approved digest-pinned image"
    )
    image_id_value(document["capture_image_id"], name="protocol capture image ID")
    mount_source = repository_relative_path(
        document["transfer_evidence_mount_source"],
        repository_root=repository_root,
        name="protocol transfer evidence mount source",
    )
    require(
        mount_source == f"examples/validation_study/.study-work/mount/{study_id}",
        "protocol mount source must equal the exact study mount",
    )
    primary_items = strict_list(document["primary_order"], name="protocol primary order")
    primary_order = [_validate_run_key(item, name="protocol primary key") for item in primary_items]
    expected_keys = [{"workload": workload, "repeat": repeat} for _, _, workload, repeat in PRIMARY_ORDER]
    require(primary_order == expected_keys, "protocol primary order must equal the exact balanced nine-run order")
    families = string_array(document["families"], name="protocol families")
    methods = string_array(document["methods"], name="protocol methods")
    require(families == FAMILY_ORDER, "protocol families must use exact lexical family order")
    expected_methods = HISTORIC_PUBLISHED_METHOD_ORDER if historic_schema_one_result else PUBLISHED_METHOD_ORDER
    require(methods == expected_methods, "protocol methods must use exact published method order")
    runtime = strict_string(document["runtime_boundary"], name="protocol runtime boundary")
    require(runtime == RUNTIME_BOUNDARY, "protocol runtime boundary must equal the locked full-lifecycle token")
    sha256(document["prerequisites_sha256"], name="prerequisite file SHA-256")
    profile_hashes(document["base_config_sha256"])
    _validate_seeds(document["seeds"])
    _validate_workloads(document["workloads"], url=url, historic_schema_one_result=historic_schema_one_result)
    return cast(JsonObject, document)


def _validate_delta_score(
    value: object,
    *,
    name: str,
    reproduction: JsonObject,
    source: JsonObject,
    historic_schema_one_result: bool = False,
) -> JsonObject:
    method_order = HISTORIC_PUBLISHED_METHOD_ORDER if historic_schema_one_result else PUBLISHED_METHOD_ORDER
    document = exact_object(value, ("aggregate", "methods"), name=name)
    aggregate = strict_float(document["aggregate"], name=f"{name}.aggregate", lower=-1.0, upper=1.0)
    methods_document = exact_object(document["methods"], method_order, name=f"{name}.methods")
    methods = {
        method: strict_float(methods_document[method], name=f"{name}.{method}", lower=-1.0, upper=1.0)
        for method in method_order
    }
    expected_aggregate = cast(float, reproduction["aggregate"]) - cast(float, source["aggregate"])
    reproduction_methods = cast(dict[str, JsonValue], reproduction["methods"])
    source_methods = cast(dict[str, JsonValue], source["methods"])
    expected_methods = {
        method: cast(float, reproduction_methods[method]) - cast(float, source_methods[method])
        for method in method_order
    }
    require(
        aggregate == expected_aggregate and methods == expected_methods,
        f"{name} must recompute as reproduction minus source",
    )
    return cast(JsonObject, document)


def validate_reproduction_comparison(
    value: object,
    *,
    reproduction: JsonObject,
    source: JsonObject,
    historic_schema_one_result: bool = False,
) -> JsonObject:
    document = exact_object(value, REPRODUCTION_COMPARISON_KEYS, name="reproduction comparison")
    source_winner = cast(dict[str, JsonValue], source["winner"])
    reproduction_winner = cast(dict[str, JsonValue], reproduction["winner"])
    family_equal = strict_bool(document["winner_family_equal"], name="winner family equality")
    genes_equal = strict_bool(document["winner_genes_equal"], name="winner genes equality")
    expected_family_equal = reproduction_winner["family"] == source_winner["family"]
    expected_genes_equal = reproduction_winner["genes"] == source_winner["genes"]
    require(
        family_equal == expected_family_equal and genes_equal == expected_genes_equal,
        "winner equality observations must recompute from reproduction and source",
    )
    fitness_delta = strict_float(
        document["winner_selection_fitness_delta"], name="winner selection fitness delta", lower=-1.0, upper=1.0
    )
    expected_fitness_delta = cast(float, reproduction_winner["selection_fitness"]) - cast(
        float, source_winner["selection_fitness"]
    )
    require(
        fitness_delta == expected_fitness_delta,
        "winner selection fitness delta must recompute from reproduction minus source",
    )
    reproduction_held = cast(JsonObject, cast(dict[str, JsonValue], reproduction["fresh_simulation"])["score"])
    source_held = cast(JsonObject, cast(dict[str, JsonValue], source["fresh_simulation"])["score"])
    reproduction_published = cast(JsonObject, cast(dict[str, JsonValue], reproduction["published"])["score"])
    source_published = cast(JsonObject, cast(dict[str, JsonValue], source["published"])["score"])
    _validate_delta_score(
        document["fresh_simulation_delta"],
        name="fresh simulation delta",
        reproduction=reproduction_held,
        source=source_held,
        historic_schema_one_result=historic_schema_one_result,
    )
    _validate_delta_score(
        document["published_delta"],
        name="published delta",
        reproduction=reproduction_published,
        source=source_published,
        historic_schema_one_result=historic_schema_one_result,
    )
    validate_score(
        document["reference_similarity"],
        name="reproduction reference similarity",
        historic_schema_one_result=historic_schema_one_result,
    )
    return cast(JsonObject, document)


def _validate_reproduction(
    value: object,
    *,
    repository_root: Path,
    protocol: JsonObject,
    source: JsonObject,
    historic_schema_one_result: bool = False,
) -> JsonObject:
    document = exact_object(value, REPRODUCTION_KEYS, name="reproduction")
    study_id = cast(str, protocol["study_id"])
    object_size = cast(int, cast(dict[str, JsonValue], protocol["capability"])["object_size_bytes"])
    source_key = _validate_run_key(document["source_key"], name="reproduction source key")
    require(
        source_key == {"workload": "streaming", "repeat": 2} and source_key == source["key"],
        "reproduction source must be streaming repeat 2",
    )
    order = strict_int(document["execution_order"], name="reproduction execution order")
    run_id = strict_string(document["run_id"], name="reproduction run ID")
    require(
        order == 10 and run_id == "10-streaming-r2-reproduction",
        "reproduction must have execution order 10 and the exact reproduction run ID",
    )
    config_path = repository_relative_path(
        document["config_path"], repository_root=repository_root, name="reproduction config path"
    )
    run_directory = repository_relative_path(
        document["run_directory"], repository_root=repository_root, name="reproduction run directory"
    )
    evidence_directory = repository_relative_path(
        document["transfer_evidence_directory"],
        repository_root=repository_root,
        name="reproduction transfer evidence directory",
    )
    expected_config = f"runs/validation_study/{study_id}/realized-configs/reproduction.toml"
    expected_run = f"runs/validation_study/{study_id}/{run_id}"
    expected_evidence = f"examples/validation_study/.study-work/evidence/{study_id}/{run_id}"
    require(
        (config_path, run_directory, evidence_directory) == (expected_config, expected_run, expected_evidence),
        "reproduction paths must equal the exact fresh tenth-run paths",
    )
    command = string_array(document["command"], name="reproduction command")
    expected_command = ("uv", "run", "--locked", "trafficlab", "run", config_path)
    expected_guard = (*guard_prefix("20m"), *expected_command)
    guard = string_array(document["guard_command"], name="reproduction guard command")
    require(
        command == expected_command and guard == expected_guard,
        "reproduction command and guard must equal the exact five-flag installed-CLI command",
    )
    guard_status = strict_int(document["guard_exit_status"], name="reproduction guard status")
    changed = string_array(document["changed_config_fields"], name="changed config fields")
    same_config = strict_bool(document["same_locked_config"], name="same locked config")
    seeded_count = strict_int(document["seeded_artifact_count"], name="seeded artifact count", minimum=0)
    require(guard_status == 0, "reproduction guard must succeed")
    require(
        changed == ("run.directory",) and same_config and (seeded_count == 0),
        "reproduction must change only run.directory, seed nothing, and match config",
    )
    sha256(document["guard_stdout_sha256"], name="guard stdout SHA-256")
    sha256(document["guard_stderr_sha256"], name="guard stderr SHA-256")
    validate_run_evidence(
        cast(JsonObject, document),
        repository_root=repository_root,
        workload="streaming",
        evidence_directory=evidence_directory,
        object_size=object_size,
        fresh_simulation_source="post_cli_evaluate_final",
        historic_schema_one_result=historic_schema_one_result,
    )
    validate_reproduction_comparison(
        document["comparison_to_source"],
        reproduction=cast(JsonObject, document),
        source=source,
        historic_schema_one_result=historic_schema_one_result,
    )
    return cast(JsonObject, document)


def validate_study_document(document: JsonObject, *, repository_root: Path) -> StudyResults:
    root = exact_object(document, RESULT_ROOT_KEYS, name="result root")
    schema_version = strict_int(root["schema_version"], name="result schema version")
    require(schema_version == 1, "result schema version must be exactly 1")
    environment = validate_environment(root["environment"])
    historic_schema_one_result = cast(str, environment["git_commit"]) == HISTORIC_SCHEMA_ONE_RESULT_COMMIT
    protocol = _validate_protocol(
        root["protocol"], repository_root=repository_root, historic_schema_one_result=historic_schema_one_result
    )
    require(
        environment["capture_image_id"] == protocol["capture_image_id"],
        "environment and protocol capture image IDs must match",
    )
    run_items = strict_list(root["runs"], name="primary runs")
    require(len(run_items) == 9, "results must contain exactly nine primary runs")
    object_size = cast(int, cast(dict[str, JsonValue], protocol["capability"])["object_size_bytes"])
    study_id = cast(str, protocol["study_id"])
    validated_runs = [
        validate_run_document(
            item,
            expected=expected,
            repository_root=repository_root,
            study_id=study_id,
            object_size=object_size,
            historic_schema_one_result=historic_schema_one_result,
        )
        for item, expected in zip(run_items, PRIMARY_ORDER, strict=True)
    ]
    grouped = {
        workload: sorted(
            [run for run in validated_runs if cast(dict[str, JsonValue], run["key"])["workload"] == workload],
            key=lambda run: cast(int, cast(dict[str, JsonValue], run["key"])["repeat"]),
        )
        for workload in ("short", "streaming", "bursty")
    }
    natural_items = strict_list(root["natural_variation"], name="natural variation records")
    summary_items = strict_list(root["workload_summaries"], name="workload summaries")
    require(
        len(natural_items) == 3 and len(summary_items) == 3,
        "natural variation and workload summaries must each contain three workloads",
    )
    workloads = ("short", "streaming", "bursty")
    natural = [
        validate_natural_variation(
            item, workload=workload, runs=grouped[workload], historic_schema_one_result=historic_schema_one_result
        )
        for item, workload in zip(natural_items, workloads, strict=True)
    ]
    summaries = [
        validate_workload_summary(
            item, workload=workload, runs=grouped[workload], historic_schema_one_result=historic_schema_one_result
        )
        for item, workload in zip(summary_items, workloads, strict=True)
    ]
    source = grouped["streaming"][1]
    reproduction = _validate_reproduction(
        root["reproduction"],
        repository_root=repository_root,
        protocol=protocol,
        source=source,
        historic_schema_one_result=historic_schema_one_result,
    )
    return StudyResults(
        schema_version=schema_version,
        environment=freeze_object(environment),
        protocol=freeze_object(protocol),
        runs=tuple(run_record_from_document(run) for run in validated_runs),
        natural_variation=cast(
            tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject], tuple(freeze_object(item) for item in natural)
        ),
        workload_summaries=cast(
            tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject],
            tuple(freeze_object(item) for item in summaries),
        ),
        reproduction=ReproductionRecord(freeze_object(reproduction)),
    )


def render_study_results(value: StudyResults) -> bytes:
    validated = validate_study_document(study_document(value), repository_root=REPOSITORY_ROOT)
    return render_study_document(validated)


def parse_study_results(content: bytes, *, repository_root: Path) -> StudyResults:
    result = validate_study_document(load_json(content), repository_root=repository_root)
    if render_study_document(result) != content:
        raise ValueError("study results JSON must use canonical sorted readable encoding with one trailing newline")
    return result


def publish_results(path: Path, value: StudyResults, *, repository_root: Path) -> None:
    content = render_study_results(value)

    def validate(persisted: bytes) -> None:
        parsed = parse_study_results(persisted, repository_root=repository_root)
        if render_study_results(parsed) != content:
            raise ValueError("persisted study results JSON is not canonical")

    publish_support_json(path, content, validate=validate)
