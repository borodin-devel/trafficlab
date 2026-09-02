import copy
import math
from typing import cast

import pytest
from pydantic import ValidationError

from tests.support.config import acd_config_data, nhpp_config_data
from trafficlab.common.config import AcdConfig, ExperimentConfig, MarkovPacketTrainConfig, NhppConfig, PacketHmmConfig


def _set_value(data: dict[str, object], path: tuple[str, ...], value: object) -> None:
    section = data
    for key in path[:-1]:
        section = cast(dict[str, object], section[key])
    section[path[-1]] = value


@pytest.mark.parametrize(
    "url",
    [
        "http://probe.example/ready",
        "https://probe.example:8443/ready?source=trafficlab",
        "https://probe.example./ready",
    ],
)
def test_network_probe_url_accepts_only_explicit_http_endpoints_with_valid_hosts(
    valid_config_data: dict[str, object], url: str
) -> None:
    """Rejecting a valid HTTP host would prevent a bounded Docker network readiness check."""
    data = copy.deepcopy(valid_config_data)
    _set_value(data, ("capture", "network_probe_url"), url)

    assert ExperimentConfig.model_validate(data).capture.network_probe_url == url


@pytest.mark.parametrize(
    "url",
    [
        "ftp://probe.example/ready",
        "//probe.example/ready",
        "https:///ready",
        "https://bad_host.example/ready",
        "https://-bad.example/ready",
        "https://bad-.example/ready",
        "https://probe.example:70000/ready",
        "https://probe.example /ready",
        "https://127.0.0.1/",
        "http://[2001:db8::1]/ready",
    ],
)
def test_network_probe_url_rejects_ambiguous_or_invalid_endpoints(
    valid_config_data: dict[str, object], url: str
) -> None:
    """An ambiguous probe URL could test a different protocol or host than the configured endpoint."""
    data = copy.deepcopy(valid_config_data)
    _set_value(data, ("capture", "network_probe_url"), url)

    with pytest.raises(ValidationError, match="network probe URL"):
        ExperimentConfig.model_validate(data)


def test_network_probe_url_requires_dns_instead_of_an_ip_literal(valid_config_data: dict[str, object]) -> None:
    """An IP-literal curl target cannot prove that Docker DNS resolution works."""
    data = copy.deepcopy(valid_config_data)
    _set_value(data, ("capture", "network_probe_url"), "https://192.0.2.10/ready")

    with pytest.raises(ValidationError, match="DNS hostname"):
        ExperimentConfig.model_validate(data)


@pytest.mark.parametrize(
    ("config_type", "field", "bounds"),
    [
        (AcdConfig, "order", (0, 3)),
        (AcdConfig, "order", (1, 4)),
        (PacketHmmConfig, "state_count", (1, 4)),
        (PacketHmmConfig, "state_count", (2, 5)),
        (MarkovPacketTrainConfig, "length_cap", (2, 8)),
        (MarkovPacketTrainConfig, "length_cap", (3, 9)),
        (NhppConfig, "bin_count", (1, 16)),
        (NhppConfig, "bin_count", (2, 17)),
    ],
)
def test_required_family_structural_bounds_are_enforced(
    config_type: type[object], field: str, bounds: tuple[int, int]
) -> None:
    """An out-of-range future family structure must fail before model registration."""
    with pytest.raises(ValidationError):
        config_type(**{field: {"lower": bounds[0], "upper": bounds[1]}})  # type: ignore[operator]


def test_nhpp_can_be_explicitly_enabled_without_changing_default_model_selection(
    valid_config_data: dict[str, object],
) -> None:
    """An NHPP table must participate in the same exact enabled/table agreement as live families."""
    data = copy.deepcopy(valid_config_data)
    models = cast(dict[str, object], data["models"])
    models["enabled"] = ["poisson_empirical", "markov_renewal", "mmpp", "nhpp"]
    models["nhpp"] = nhpp_config_data()

    config = ExperimentConfig.model_validate(data)

    assert config.models.nhpp is not None
    assert config.models.nhpp.bin_count.lower == 2
    assert config.models.nhpp.bin_count.upper == 16


def test_acd_can_be_explicitly_enabled_without_changing_default_model_selection(
    valid_config_data: dict[str, object],
) -> None:
    """An ACD table must participate in the same exact enabled/table agreement as live families."""
    data = copy.deepcopy(valid_config_data)
    models = cast(dict[str, object], data["models"])
    models["enabled"] = ["poisson_empirical", "markov_renewal", "mmpp", "acd"]
    models["acd"] = acd_config_data()

    config = ExperimentConfig.model_validate(data)

    assert config.models.acd is not None
    assert config.models.acd.order.lower == 1
    assert config.models.acd.order.upper == 3


@pytest.mark.parametrize(
    ("path", "value"),
    [
        *(
            (("models", family, field), value)
            for family in ("poisson_empirical", "markov_renewal", "mmpp")
            for field in ("crossover_probability", "mutation_probability")
            for value in (-0.1, 1.1, math.inf)
        ),
        *(
            (("models", family, "mutation_scale"), value)
            for family in ("poisson_empirical", "markov_renewal", "mmpp")
            for value in (0.0, -0.1)
        ),
        *(
            (("capture", field), value)
            for field in (
                "readiness_timeout_seconds",
                "workload_timeout_seconds",
                "flush_timeout_seconds",
                "total_timeout_seconds",
            )
            for value in (0.0, -0.1)
        ),
        *(
            (("generation", limit, field), value)
            for limit in ("trial", "final")
            for field in ("max_packets", "max_output_bytes", "max_wall_seconds")
            for value in (0, -1)
        ),
        *(
            (("genetic", field), value)
            for field in ("population_size", "generation_count", "tournament_size", "elite_count")
            for value in ((-1,) if field == "generation_count" else (0, -1))
        ),
    ],
)
def test_probabilities_and_positive_values_reject_invalid_bounds(
    valid_config_data: dict[str, object], path: tuple[str, ...], value: object
) -> None:
    """A missing primitive bound check would make one parametrized input valid."""
    data = copy.deepcopy(valid_config_data)
    _set_value(data, path, value)

    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(data)


@pytest.mark.parametrize("family", ["poisson_empirical", "markov_renewal", "mmpp"])
def test_mutation_scales_accept_one_at_each_family_location(valid_config_data: dict[str, object], family: str) -> None:
    """Removing the inclusive upper bound would reject the documented scale endpoint."""
    data = copy.deepcopy(valid_config_data)
    _set_value(data, ("models", family, "mutation_scale"), 1.0)

    config = ExperimentConfig.model_validate(data)

    family_config = getattr(config.models, family)
    assert family_config is not None
    assert family_config.mutation_scale == 1.0


@pytest.mark.parametrize("family", ["poisson_empirical", "markov_renewal", "mmpp"])
def test_mutation_scales_reject_values_above_one_at_precise_locations(
    valid_config_data: dict[str, object], family: str
) -> None:
    """A scale above one would violate the normalized mutation coordinate contract."""
    data = copy.deepcopy(valid_config_data)
    _set_value(data, ("models", family, "mutation_scale"), 1.1)

    with pytest.raises(ValidationError) as error:
        ExperimentConfig.model_validate(data)

    assert error.value.errors(include_url=False) == [
        {
            "type": "less_than_equal",
            "loc": ("models", family, "mutation_scale"),
            "msg": "Input should be less than or equal to 1",
            "input": 1.1,
            "ctx": {"le": 1.0},
        }
    ]


@pytest.mark.parametrize(
    ("path", "bounds"),
    [
        (("models", "poisson_empirical", "c_lambda"), {"lower": 1.0, "upper": 1.0}),
        (("models", "poisson_empirical", "c_lambda"), {"lower": 2.0, "upper": 1.0}),
        (("models", "markov_renewal", "r"), {"lower": 3, "upper": 3}),
        (("models", "markov_renewal", "r"), {"lower": 4, "upper": 3}),
    ],
)
def test_gene_bounds_require_lower_less_than_upper(
    valid_config_data: dict[str, object], path: tuple[str, ...], bounds: dict[str, float | int]
) -> None:
    """Accepting equal endpoints would leave a family gene without a range."""
    data = copy.deepcopy(valid_config_data)
    _set_value(data, path, bounds)

    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(data)


@pytest.mark.parametrize(
    ("path", "lower"),
    [
        (("models", "poisson_empirical", "c_lambda", "lower"), 0.0),
        (("models", "markov_renewal", "c_t", "lower"), 0.0),
        *((("models", "mmpp", gene, "lower"), 0.0) for gene in ("q01", "q10", "lambda0", "lambda1")),
    ],
)
def test_logarithmic_gene_bounds_require_a_positive_lower_bound(
    valid_config_data: dict[str, object], path: tuple[str, ...], lower: float
) -> None:
    """A nonpositive logarithmic lower bound would make transformed mutation undefined."""
    data = copy.deepcopy(valid_config_data)
    _set_value(data, path, lower)

    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(data)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        *(
            (
                (("models", "markov_renewal", gene, endpoint), value)
                for gene in ("q1", "q2")
                for endpoint, value in (("lower", 0.0), ("upper", 1.0))
            )
        ),
        (("models", "markov_renewal", "alpha", "lower"), -0.1),
        (("models", "markov_renewal", "r", "lower"), 0),
    ],
)
def test_markov_gene_domain_constraints_are_enforced(
    valid_config_data: dict[str, object], path: tuple[str, ...], value: float | int
) -> None:
    """An out-of-domain Markov gene would break its model interpretation."""
    data = copy.deepcopy(valid_config_data)
    _set_value(data, path, value)

    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(data)


def test_operator_defaults(valid_config_data: dict[str, object]) -> None:
    """Removing an enabled family's operator settings must still leave usable defaults."""
    data = copy.deepcopy(valid_config_data)
    models = cast(dict[str, object], data["models"])
    for family in ("poisson_empirical", "markov_renewal", "mmpp"):
        settings = cast(dict[str, object], models[family])
        for key in ("crossover_probability", "mutation_probability", "mutation_scale"):
            del settings[key]

    config = ExperimentConfig.model_validate(data)

    assert config.models.poisson_empirical is not None
    assert config.models.markov_renewal is not None
    assert config.models.mmpp is not None
    assert config.models.poisson_empirical.operator_values == (0.9, 1.0, 0.1)
    assert config.models.markov_renewal.operator_values == (0.9, 0.2, 0.1)
    assert config.models.mmpp.operator_values == (0.9, 0.25, 0.1)


def test_operator_override_is_scoped_to_its_own_family(valid_config_data: dict[str, object]) -> None:
    """Changing one family operator must not alter the other family defaults."""
    data = copy.deepcopy(valid_config_data)
    poisson = cast(dict[str, object], cast(dict[str, object], data["models"])["poisson_empirical"])
    poisson["mutation_probability"] = 0.4
    for family in ("markov_renewal", "mmpp"):
        settings = cast(dict[str, object], cast(dict[str, object], data["models"])[family])
        for key in ("crossover_probability", "mutation_probability", "mutation_scale"):
            del settings[key]

    config = ExperimentConfig.model_validate(data)

    assert config.models.poisson_empirical is not None
    assert config.models.markov_renewal is not None
    assert config.models.mmpp is not None
    assert config.models.poisson_empirical.operator_values == (0.9, 0.4, 0.1)
    assert config.models.markov_renewal.operator_values == (0.9, 0.2, 0.1)
    assert config.models.mmpp.operator_values == (0.9, 0.25, 0.1)


def test_unknown_operator_setting_is_rejected_at_its_precise_location(valid_config_data: dict[str, object]) -> None:
    """An unrecognized operator key must not silently alter a family's behavior."""
    data = copy.deepcopy(valid_config_data)
    poisson = cast(dict[str, object], cast(dict[str, object], data["models"])["poisson_empirical"])
    poisson["mutation_probability_typo"] = 0.4

    with pytest.raises(ValidationError) as error:
        ExperimentConfig.model_validate(data)

    assert error.value.errors(include_url=False) == [
        {
            "type": "extra_forbidden",
            "loc": ("models", "poisson_empirical", "mutation_probability_typo"),
            "msg": "Extra inputs are not permitted",
            "input": 0.4,
        }
    ]


def test_unknown_family_name_is_rejected_at_its_precise_location(valid_config_data: dict[str, object]) -> None:
    """An unrecognized enabled family must not reach later model-table validation."""
    data = copy.deepcopy(valid_config_data)
    models = cast(dict[str, object], data["models"])
    models["enabled"] = ["not_a_model", "markov_renewal", "mmpp"]

    with pytest.raises(ValidationError) as error:
        ExperimentConfig.model_validate(data)

    assert error.value.errors(include_url=False) == [
        {
            "type": "literal_error",
            "loc": ("models", "enabled", 0),
            "msg": "Input should be 'poisson_empirical', 'markov_renewal', 'mmpp', 'nhpp' or 'acd'",
            "input": "not_a_model",
            "ctx": {"expected": "'poisson_empirical', 'markov_renewal', 'mmpp', 'nhpp' or 'acd'"},
        }
    ]


@pytest.mark.parametrize("family", ["packet_hmm", "markov_packet_train"])
def test_unregistered_required_family_name_is_rejected(valid_config_data: dict[str, object], family: str) -> None:
    """A config value type alone must not activate an incomplete model implementation."""
    data = copy.deepcopy(valid_config_data)
    models = cast(dict[str, object], data["models"])
    models["enabled"] = [family]

    with pytest.raises(ValidationError, match="Input should be"):
        ExperimentConfig.model_validate(data)


@pytest.mark.parametrize("invalid_case", ["duplicate", "empty", "missing_table", "disabled_table"])
def test_enabled_families_match_configured_family_tables(
    valid_config_data: dict[str, object], invalid_case: str
) -> None:
    """Missing, duplicate, or disabled family tables would make reproduction ambiguous."""
    data = copy.deepcopy(valid_config_data)
    models = cast(dict[str, object], data["models"])
    if invalid_case == "duplicate":
        models["enabled"] = ["poisson_empirical", "poisson_empirical"]
    elif invalid_case == "empty":
        models["enabled"] = []
    elif invalid_case == "missing_table":
        del models["poisson_empirical"]
    else:
        models["enabled"] = ["poisson_empirical"]

    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(data)


@pytest.mark.parametrize("value", [0, -1])
def test_duplicate_mutation_attempts_accepts_zero_only_when_not_negative(
    valid_config_data: dict[str, object], value: int
) -> None:
    """A negative retry count is invalid while zero means no retry attempts."""
    data = copy.deepcopy(valid_config_data)
    _set_value(data, ("genetic", "duplicate_mutation_attempts"), value)

    if value == 0:
        assert ExperimentConfig.model_validate(data).genetic.duplicate_mutation_attempts == 0
    else:
        with pytest.raises(ValidationError):
            ExperimentConfig.model_validate(data)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("genetic", "population_size"), 1),
        (("genetic", "elite_count"), 9),
        (("genetic", "tournament_size"), 1),
        (("genetic", "tournament_size"), 10),
        (("genetic", "early_stopping_generations"), 4),
        (("genetic", "trial_seeds"), []),
        (("genetic", "trial_seeds"), [101, 101]),
        (("capture", "readiness_timeout_seconds"), 61.0),
        (("capture", "workload_timeout_seconds"), 61.0),
        (("capture", "flush_timeout_seconds"), 61.0),
        (("generation", "final", "max_packets"), 1_999),
        (("generation", "final", "max_output_bytes"), 3_999_999),
        (("generation", "final", "max_wall_seconds"), 4.9),
    ],
)
def test_experiment_cross_section_bounds_are_enforced(
    valid_config_data: dict[str, object], path: tuple[str, ...], value: object
) -> None:
    """Invalid population, timeout, seed, or final-limit relationships must be rejected."""
    data = copy.deepcopy(valid_config_data)
    _set_value(data, path, value)

    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(data)


def test_population_must_leave_one_slot_for_each_enabled_family(valid_config_data: dict[str, object]) -> None:
    """A population smaller than elites plus families cannot preserve every family."""
    data = copy.deepcopy(valid_config_data)
    _set_value(data, ("genetic", "population_size"), 3)
    _set_value(data, ("genetic", "elite_count"), 1)

    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(data)


def test_final_seed_cannot_be_a_selection_trial_seed(valid_config_data: dict[str, object]) -> None:
    run = dict(cast(dict[str, object], valid_config_data["run"]))
    run["final_seed"] = 11
    genetic = dict(cast(dict[str, object], valid_config_data["genetic"]))
    genetic["trial_seeds"] = [7, 11]

    with pytest.raises(ValidationError, match="final seed"):
        ExperimentConfig.model_validate({**valid_config_data, "run": run, "genetic": genetic})


def test_generation_count_zero_means_only_generation_zero(valid_config_data: dict[str, object]) -> None:
    genetic = dict(cast(dict[str, object], valid_config_data["genetic"]))
    genetic["generation_count"] = 0

    config = ExperimentConfig.model_validate({**valid_config_data, "genetic": genetic})

    assert config.genetic.generation_count == 0


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("similarity", "acf_lag_weights"), [0.8]),
        (("similarity", "acf_lag_weights"), [0.5, 0.5]),
        (("similarity", "acf_lag_weights"), [-0.1]),
        (("similarity", "acf_iat_weight"), 0.6),
        (("similarity", "acf_lags"), [1, 1]),
        (("similarity", "acf_lags"), [0]),
        (("similarity", "multiscale_widths_seconds"), [1.0, 0.1]),
        (("similarity", "multiscale_widths_seconds"), [0.1, 0.1]),
        (("similarity", "multiscale_widths_seconds"), [0.0]),
        (("similarity", "multiscale_scale_weights"), [0.8, 0.1]),
        (("similarity", "multiscale_scale_weights"), [1.0]),
        (("similarity", "multiscale_scale_weights"), [-0.1, 1.1]),
        (("similarity", "multiscale_packet_weight"), 0.6),
        (("similarity", "max_direction_bin_cells"), 1),
        (("similarity", "cvm_iat_weight"), 0.6),
        (("similarity", "ad_iat_weight"), 0.6),
        (("similarity", "js_iat_weight"), 0.6),
        (("similarity", "js_iat_bin_count"), 0),
        (("similarity", "js_iat_bin_count"), 65_537),
        (("similarity", "mmd_feature_count"), 0),
        (("similarity", "mmd_feature_count"), 65_537),
        (("similarity", "mmd_seed"), -1),
        (("similarity", "mmd_scale_floor"), 0.0),
        (("similarity", "iat_diagnostic_quantile"), 0.0),
        (("similarity", "iat_diagnostic_quantile"), 1.0),
    ],
)
def test_similarity_vectors_and_scalar_bounds_are_enforced(
    valid_config_data: dict[str, object], path: tuple[str, ...], value: object
) -> None:
    """Malformed diagnostics weights and shapes must not reach metric evaluation."""
    data = copy.deepcopy(valid_config_data)
    _set_value(data, path, value)

    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(data)


@pytest.mark.parametrize(
    ("method_weights", "expected_valid"),
    [
        (
            {
                "frame_size_ks": 0.3,
                "iat_ks": 0.1,
                "autocorrelation": 0.1,
                "multiscale_rate": 0.1,
                "cramer_von_mises": 0.1,
                "anderson_darling": 0.1,
                "jensen_shannon": 0.1,
                "approximate_mmd": 0.1,
            },
            True,
        ),
        (
            {
                "frame_size_ks": 0.2,
                "iat_ks": 0.1,
                "autocorrelation": 0.1,
                "multiscale_rate": 0.1,
                "cramer_von_mises": 0.1,
                "anderson_darling": 0.1,
                "jensen_shannon": 0.1,
                "approximate_mmd": 0.1,
            },
            False,
        ),
        (
            {
                "frame_size_ks": -0.1,
                "iat_ks": 0.1,
                "autocorrelation": 0.1,
                "multiscale_rate": 0.1,
                "cramer_von_mises": 0.1,
                "anderson_darling": 0.1,
                "jensen_shannon": 0.1,
                "approximate_mmd": 0.5,
            },
            False,
        ),
    ],
)
def test_method_weights_are_normalized(
    valid_config_data: dict[str, object], method_weights: dict[str, float], expected_valid: bool
) -> None:
    """Method aggregation requires a nonnegative normalized weight vector."""
    data = copy.deepcopy(valid_config_data)
    _set_value(data, ("similarity", "method_weights"), method_weights)

    if expected_valid:
        assert ExperimentConfig.model_validate(data).similarity.method_weights.frame_size_ks == 0.3
    else:
        with pytest.raises(ValidationError):
            ExperimentConfig.model_validate(data)


def test_each_method_weight_is_bounded_before_normalized_sum_validation(
    valid_config_data: dict[str, object],
) -> None:
    """A sum-tolerance allowance must not let an individual method weight exceed probability bounds."""
    data = copy.deepcopy(valid_config_data)
    _set_value(
        data,
        ("similarity", "method_weights"),
        {
            "frame_size_ks": 1.0000000000005,
            "iat_ks": 0.0,
            "autocorrelation": 0.0,
            "multiscale_rate": 0.0,
            "cramer_von_mises": 0.0,
            "anderson_darling": 0.0,
            "jensen_shannon": 0.0,
            "approximate_mmd": 0.0,
        },
    )

    with pytest.raises(ValidationError) as error:
        ExperimentConfig.model_validate(data)

    assert error.value.errors(include_url=False)[0]["loc"] == (
        "similarity",
        "method_weights",
        "frame_size_ks",
    )


def test_postfit_settings_are_required_without_compatibility_defaults(valid_config_data: dict[str, object]) -> None:
    """A schema-5 config missing final-diagnostic semantics must not silently acquire them."""
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["similarity"]).pop("postfit")

    with pytest.raises(ValidationError) as error:
        ExperimentConfig.model_validate(data)

    assert error.value.errors(include_url=False)[0]["loc"] == ("similarity", "postfit")


def test_c2st_maximum_window_count_accepts_65536_and_rejects_65537(
    valid_config_data: dict[str, object],
) -> None:
    """The serialized allocation boundary must match the documented fixed cap exactly."""
    accepted = copy.deepcopy(valid_config_data)
    _set_value(accepted, ("similarity", "postfit", "c2st", "maximum_window_count"), 65_536)
    assert ExperimentConfig.model_validate(accepted).similarity.postfit.c2st.maximum_window_count == 65_536

    rejected = copy.deepcopy(valid_config_data)
    _set_value(rejected, ("similarity", "postfit", "c2st", "maximum_window_count"), 65_537)
    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(rejected)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("dispersion", "widths_seconds"), [1.0, 1.0]),
        (("dispersion", "scale_weights"), [1.0]),
        (("dispersion", "fano_weight"), 0.75),
        (("transition", "size_bin_count"), 0),
        (("transition", "pseudocount"), 0.0),
        (("transition", "occupancy_weight"), 0.5),
        (("c2st", "feature_version"), "future-v2"),
        (("c2st", "window_width_seconds"), 0.0),
        (("c2st", "fold_count"), 1),
        (("c2st", "guard_window_count"), -1),
        (("c2st", "maximum_window_count"), 0),
        (("c2st", "l2_regularization"), 0.0),
        (("c2st", "maximum_iterations"), 0),
        (("c2st", "tolerance"), 0.0),
    ],
)
def test_postfit_settings_reject_ambiguous_or_unbounded_values(
    valid_config_data: dict[str, object], path: tuple[str, str], value: object
) -> None:
    """Every representation, fold, solver, smoothing, weight, and allocation choice is explicit and bounded."""
    data = copy.deepcopy(valid_config_data)
    similarity = cast(dict[str, object], data["similarity"])
    postfit = cast(dict[str, object], similarity["postfit"])
    cast(dict[str, object], postfit[path[0]])[path[1]] = value

    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(data)


@pytest.mark.parametrize(
    "path",
    [
        ("iat_diagnostic_quantile",),
        ("acf_lags",),
        ("acf_lag_weights",),
        ("acf_iat_weight",),
        ("acf_size_weight",),
        ("multiscale_widths_seconds",),
        ("multiscale_scale_weights",),
        ("multiscale_packet_weight",),
        ("multiscale_byte_weight",),
        ("max_direction_bin_cells",),
        ("cvm_iat_weight",),
        ("cvm_size_weight",),
        ("ad_iat_weight",),
        ("ad_size_weight",),
        ("js_iat_bin_count",),
        ("js_iat_weight",),
        ("js_mark_weight",),
        ("mmd_feature_count",),
        ("mmd_seed",),
        ("mmd_scale_floor",),
        ("method_weights",),
        ("method_weights", "frame_size_ks"),
        ("method_weights", "iat_ks"),
        ("method_weights", "autocorrelation"),
        ("method_weights", "multiscale_rate"),
        ("method_weights", "cramer_von_mises"),
        ("method_weights", "anderson_darling"),
        ("method_weights", "jensen_shannon"),
        ("method_weights", "approximate_mmd"),
    ],
)
def test_every_similarity_setting_and_method_weight_is_mandatory(
    valid_config_data: dict[str, object], path: tuple[str, ...]
) -> None:
    """An omitted setting or zero-weight method field must not silently select a reduced metric set."""
    data = copy.deepcopy(valid_config_data)
    similarity = cast(dict[str, object], data["similarity"])
    if len(path) == 1:
        similarity.pop(path[0])
    else:
        cast(dict[str, object], similarity[path[0]]).pop(path[1])

    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(data)


@pytest.mark.parametrize(
    ("location", "validation_location"),
    [
        ("acf_lag_weights", ("similarity",)),
        ("acf_component_weights", ("similarity",)),
        ("multiscale_scale_weights", ("similarity",)),
        ("multiscale_component_weights", ("similarity",)),
        ("cvm_component_weights", ("similarity",)),
        ("ad_component_weights", ("similarity",)),
        ("js_component_weights", ("similarity",)),
        ("method_weights", ("similarity", "method_weights")),
    ],
)
def test_normalized_weight_vectors_use_an_absolute_tolerance(
    valid_config_data: dict[str, object], location: str, validation_location: tuple[str, ...]
) -> None:
    """Relative tolerance would accept a vector whose sum exceeds the stated absolute bound."""
    inside = math.nextafter(1.0 + 1e-12, 0.0)
    outside = math.nextafter(1.0 + 1e-12, math.inf)
    assert abs(inside - 1.0) < 1e-12 < abs(outside - 1.0)

    accepted = copy.deepcopy(valid_config_data)
    _set_normalized_weight_sum(accepted, location, inside)
    assert ExperimentConfig.model_validate(accepted)

    rejected = copy.deepcopy(valid_config_data)
    _set_normalized_weight_sum(rejected, location, outside)
    with pytest.raises(ValidationError) as error:
        ExperimentConfig.model_validate(rejected)

    assert error.value.errors(include_url=False)[0]["loc"] == validation_location


def _set_normalized_weight_sum(data: dict[str, object], location: str, total: float) -> None:
    similarity = cast(dict[str, object], data["similarity"])
    if location == "acf_lag_weights":
        similarity["acf_lag_weights"] = [total]
    elif location == "acf_component_weights":
        similarity["acf_iat_weight"] = total
        similarity["acf_size_weight"] = 0.0
    elif location == "multiscale_scale_weights":
        similarity["multiscale_scale_weights"] = [total, 0.0]
    elif location == "multiscale_component_weights":
        similarity["multiscale_packet_weight"] = total
        similarity["multiscale_byte_weight"] = 0.0
    elif location == "cvm_component_weights":
        similarity["cvm_iat_weight"] = total
        similarity["cvm_size_weight"] = 0.0
    elif location == "ad_component_weights":
        similarity["ad_iat_weight"] = total
        similarity["ad_size_weight"] = 0.0
    elif location == "js_component_weights":
        similarity["js_iat_weight"] = total
        similarity["js_mark_weight"] = 0.0
    else:
        similarity["method_weights"] = {
            "frame_size_ks": 0.125 + (total - 1.0),
            "iat_ks": 0.125,
            "autocorrelation": 0.125,
            "multiscale_rate": 0.125,
            "cramer_von_mises": 0.125,
            "anderson_darling": 0.125,
            "jensen_shannon": 0.125,
            "approximate_mmd": 0.125,
        }
