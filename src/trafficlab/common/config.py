"""Immutable, strict configuration models for Trafficlab experiments."""

import math
import re
from collections.abc import Sequence
from ipaddress import ip_address
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

FamilyName = Literal[
    "poisson_empirical",
    "markov_renewal",
    "mmpp",
    "nhpp",
    "acd",
    "markov_packet_train",
    "packet_hmm",
]
type GeneCoordinateKind = Literal["linear", "log", "integer"]

NonEmptyString = Annotated[StrictStr, Field(min_length=1)]
NonNegativeInteger = Annotated[StrictInt, Field(ge=0)]
PositiveInteger = Annotated[StrictInt, Field(gt=0)]
PositiveFloat = Annotated[StrictFloat, Field(gt=0)]
AtLeastTwoInteger = Annotated[StrictInt, Field(ge=2)]
BoundedSimilarityAllocation = Annotated[StrictInt, Field(gt=0, le=65_536)]
Probability = Annotated[StrictFloat, Field(ge=0.0, le=1.0)]
Tolerance = Annotated[StrictFloat, Field(ge=0.0, le=1.0)]
NormalizedMutationScale = Annotated[StrictFloat, Field(gt=0.0, le=1.0)]

_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")


def _weights_sum_to_one(values: Sequence[float], name: str) -> None:
    if any(value < 0.0 for value in values) or not math.isclose(sum(values), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(f"{name} must be nonnegative and sum to one")


class StrictModel(BaseModel):
    """Base model that rejects unknown values and cannot be changed after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class FloatBounds(StrictModel):
    lower: StrictFloat
    upper: StrictFloat

    @model_validator(mode="after")
    def lower_is_less_than_upper(self) -> Self:
        if self.lower >= self.upper:
            raise ValueError("float bounds lower must be less than upper")
        return self


class IntegerBounds(StrictModel):
    lower: StrictInt
    upper: StrictInt

    @model_validator(mode="after")
    def lower_is_less_than_upper(self) -> Self:
        if self.lower >= self.upper:
            raise ValueError("integer bounds lower must be less than upper")
        return self


class MountConfig(StrictModel):
    source: Path
    target: NonEmptyString
    read_only: StrictBool = True

    @field_validator("target")
    @classmethod
    def target_is_absolute_posix_path(cls, value: str) -> str:
        if not PurePosixPath(value).is_absolute():
            raise ValueError("mount target must be an absolute POSIX path")
        return value


class RunConfig(StrictModel):
    directory: Path
    minimum_free_bytes: PositiveInteger
    master_seed: NonNegativeInteger
    final_seed: NonNegativeInteger


class TargetConfig(StrictModel):
    image: NonEmptyString
    argv: tuple[NonEmptyString, ...] = Field(min_length=1)
    environment: dict[NonEmptyString, NonEmptyString] = Field(default_factory=dict)
    working_directory: NonEmptyString
    mounts: tuple[MountConfig, ...] = ()

    @field_validator("working_directory")
    @classmethod
    def working_directory_is_absolute_posix_path(cls, value: str) -> str:
        if not PurePosixPath(value).is_absolute():
            raise ValueError("working directory must be an absolute POSIX path")
        return value


class CaptureConfig(StrictModel):
    image: NonEmptyString
    network_probe_url: NonEmptyString
    readiness_timeout_seconds: PositiveFloat
    workload_timeout_seconds: PositiveFloat
    flush_timeout_seconds: PositiveFloat
    total_timeout_seconds: PositiveFloat

    @field_validator("network_probe_url")
    @classmethod
    def network_probe_url_is_an_explicit_http_endpoint(cls, value: str) -> str:
        if any(character.isspace() for character in value):
            raise ValueError("network probe URL must not contain whitespace")
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError as error:
            raise ValueError("network probe URL has an invalid host or port") from error
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or hostname is None:
            raise ValueError("network probe URL must be an absolute HTTP or HTTPS URL with a hostname")
        try:
            ip_address(hostname)
        except ValueError:
            pass
        else:
            raise ValueError("network probe URL must contain a DNS hostname, not an IP address literal")
        dns_hostname = hostname[:-1] if hostname.endswith(".") else hostname
        labels = dns_hostname.split(".")
        if not dns_hostname or len(dns_hostname) > 253 or any(_DNS_LABEL.fullmatch(label) is None for label in labels):
            raise ValueError("network probe URL must contain a valid DNS hostname")
        return value


class GenerationLimits(StrictModel):
    max_packets: PositiveInteger
    max_output_bytes: PositiveInteger
    max_wall_seconds: PositiveFloat


class GenerationConfig(StrictModel):
    trial: GenerationLimits
    final: GenerationLimits


class GeneticConfig(StrictModel):
    population_size: PositiveInteger
    generation_count: NonNegativeInteger
    tournament_size: PositiveInteger
    elite_count: PositiveInteger
    trial_seeds: tuple[NonNegativeInteger, ...] = Field(min_length=1)
    duplicate_mutation_attempts: NonNegativeInteger
    early_stopping_generations: NonNegativeInteger
    early_stopping_tolerance: Tolerance = 0.0
    resume: StrictBool = False

    @field_validator("early_stopping_tolerance", mode="before")
    @classmethod
    def early_stopping_tolerance_is_an_exact_float(cls, value: object) -> object:
        if type(value) is not float:
            raise ValueError("early stopping tolerance must be an exact float")
        return value


class FamilyOperators(StrictModel):
    crossover_probability: Probability
    mutation_probability: Probability
    mutation_scale: NormalizedMutationScale

    @property
    def operator_values(self) -> tuple[float, float, float]:
        """Return the configured crossover probability, mutation probability, and scale."""
        return (self.crossover_probability, self.mutation_probability, self.mutation_scale)


class PoissonConfig(FamilyOperators):
    crossover_probability: Probability = 0.9
    mutation_probability: Probability = 1.0
    mutation_scale: NormalizedMutationScale = 0.1
    c_lambda: FloatBounds

    @field_validator("c_lambda")
    @classmethod
    def c_lambda_has_positive_lower_bound(cls, value: FloatBounds) -> FloatBounds:
        if value.lower <= 0.0:
            raise ValueError("c_lambda lower bound must be positive")
        return value


class MarkovRenewalConfig(FamilyOperators):
    crossover_probability: Probability = 0.9
    mutation_probability: Probability = 0.2
    mutation_scale: NormalizedMutationScale = 0.1
    q1: FloatBounds
    q2: FloatBounds
    alpha: FloatBounds
    r: IntegerBounds
    c_t: FloatBounds

    @field_validator("q1", "q2")
    @classmethod
    def q_bounds_are_strictly_between_zero_and_one(cls, value: FloatBounds) -> FloatBounds:
        if value.lower <= 0.0 or value.upper >= 1.0:
            raise ValueError("q bounds must be strictly between zero and one")
        return value

    @field_validator("alpha")
    @classmethod
    def alpha_lower_bound_is_nonnegative(cls, value: FloatBounds) -> FloatBounds:
        if value.lower < 0.0:
            raise ValueError("alpha lower bound must be nonnegative")
        return value

    @field_validator("r")
    @classmethod
    def r_lower_bound_is_at_least_one(cls, value: IntegerBounds) -> IntegerBounds:
        if value.lower < 1:
            raise ValueError("r lower bound must be at least one")
        return value

    @field_validator("c_t")
    @classmethod
    def c_t_has_positive_lower_bound(cls, value: FloatBounds) -> FloatBounds:
        if value.lower <= 0.0:
            raise ValueError("c_t lower bound must be positive")
        return value


class MmppConfig(FamilyOperators):
    crossover_probability: Probability = 0.9
    mutation_probability: Probability = 0.25
    mutation_scale: NormalizedMutationScale = 0.1
    q01: FloatBounds
    q10: FloatBounds
    lambda0: FloatBounds
    lambda1: FloatBounds

    @field_validator("q01", "q10", "lambda0", "lambda1")
    @classmethod
    def logarithmic_bounds_have_positive_lower_bounds(cls, value: FloatBounds) -> FloatBounds:
        if value.lower <= 0.0:
            raise ValueError("MMPP logarithmic lower bounds must be positive")
        return value


class PacketHmmConfig(FamilyOperators):
    """Strict latent-state bounds for the categorical packet-HMM family."""

    crossover_probability: Probability = 0.9
    mutation_probability: Probability = 1.0
    mutation_scale: NormalizedMutationScale = 0.1
    state_count: IntegerBounds

    @field_validator("state_count")
    @classmethod
    def state_count_is_within_supported_range(cls, value: IntegerBounds) -> IntegerBounds:
        if value.lower < 2 or value.upper > 4:
            raise ValueError("packet HMM state_count bounds must be within 2..4")
        return value


class MarkovPacketTrainConfig(FamilyOperators):
    """Strict capped-length settings for the Markov packet-train family."""

    crossover_probability: Probability = 0.9
    mutation_probability: Probability = 1.0
    mutation_scale: NormalizedMutationScale = 0.1
    length_cap: IntegerBounds

    @field_validator("length_cap")
    @classmethod
    def length_cap_is_within_supported_range(cls, value: IntegerBounds) -> IntegerBounds:
        if value.lower < 3 or value.upper > 8:
            raise ValueError("Markov packet-train length_cap bounds must be within 3..8")
        return value


class AcdConfig(FamilyOperators):
    """Strict exponential ACD structural settings."""

    crossover_probability: Probability = 0.9
    mutation_probability: Probability = 1.0
    mutation_scale: NormalizedMutationScale = 0.1
    order: IntegerBounds

    @field_validator("order")
    @classmethod
    def order_is_within_supported_range(cls, value: IntegerBounds) -> IntegerBounds:
        if value.lower < 1 or value.upper > 3:
            raise ValueError("ACD order bounds must be within 1..3")
        return value


class NhppConfig(FamilyOperators):
    """Strict piecewise-constant NHPP structural settings."""

    crossover_probability: Probability = 0.9
    mutation_probability: Probability = 1.0
    mutation_scale: NormalizedMutationScale = 0.1
    bin_count: IntegerBounds

    @field_validator("bin_count")
    @classmethod
    def bin_count_is_within_supported_range(cls, value: IntegerBounds) -> IntegerBounds:
        if value.lower < 2 or value.upper > 16:
            raise ValueError("NHPP bin_count bounds must be within 2..16")
        return value


class ModelsConfig(StrictModel):
    enabled: tuple[FamilyName, ...] = Field(min_length=1)
    poisson_empirical: PoissonConfig | None = None
    markov_renewal: MarkovRenewalConfig | None = None
    mmpp: MmppConfig | None = None
    nhpp: NhppConfig | None = None
    acd: AcdConfig | None = None
    markov_packet_train: MarkovPacketTrainConfig | None = None
    packet_hmm: PacketHmmConfig | None = None

    @model_validator(mode="after")
    def enabled_families_match_configured_tables(self) -> Self:
        configured = {
            name
            for name, table in (
                ("poisson_empirical", self.poisson_empirical),
                ("markov_renewal", self.markov_renewal),
                ("mmpp", self.mmpp),
                ("nhpp", self.nhpp),
                ("acd", self.acd),
                ("markov_packet_train", self.markov_packet_train),
                ("packet_hmm", self.packet_hmm),
            )
            if table is not None
        }
        if len(self.enabled) != len(set(self.enabled)):
            raise ValueError("enabled model families must be unique")
        if set(self.enabled) != configured:
            raise ValueError("enabled model families must exactly match configured family tables")
        return self


class MethodWeights(StrictModel):
    frame_size_ks: Probability
    iat_ks: Probability
    autocorrelation: Probability
    multiscale_rate: Probability
    cramer_von_mises: Probability
    anderson_darling: Probability
    jensen_shannon: Probability
    approximate_mmd: Probability

    @model_validator(mode="after")
    def values_are_normalized(self) -> Self:
        _weights_sum_to_one(
            (
                self.frame_size_ks,
                self.iat_ks,
                self.autocorrelation,
                self.multiscale_rate,
                self.cramer_von_mises,
                self.anderson_darling,
                self.jensen_shannon,
                self.approximate_mmd,
            ),
            "method weights",
        )
        return self


class C2stSettings(StrictModel):
    feature_version: Literal["window-v1"]
    window_width_seconds: PositiveFloat
    fold_count: AtLeastTwoInteger
    guard_window_count: NonNegativeInteger
    maximum_window_count: BoundedSimilarityAllocation
    l2_regularization: PositiveFloat
    maximum_iterations: PositiveInteger
    tolerance: PositiveFloat


class DispersionSettings(StrictModel):
    widths_seconds: tuple[PositiveFloat, ...] = Field(min_length=1)
    scale_weights: tuple[Probability, ...] = Field(min_length=1)
    fano_weight: Probability
    allan_weight: Probability

    @model_validator(mode="after")
    def vectors_are_compatible(self) -> Self:
        if any(left >= right for left, right in zip(self.widths_seconds, self.widths_seconds[1:], strict=False)):
            raise ValueError("post-fit dispersion widths must be unique and strictly increasing")
        if len(self.widths_seconds) != len(self.scale_weights):
            raise ValueError("post-fit dispersion widths and scale weights must have equal length")
        _weights_sum_to_one(self.scale_weights, "post-fit dispersion scale weights")
        _weights_sum_to_one((self.fano_weight, self.allan_weight), "post-fit dispersion component weights")
        return self


class TransitionSettings(StrictModel):
    size_bin_count: BoundedSimilarityAllocation
    iat_bin_count: BoundedSimilarityAllocation
    pseudocount: PositiveFloat
    occupancy_weight: Probability
    transition_rows_weight: Probability
    runs_weight: Probability

    @model_validator(mode="after")
    def component_weights_are_normalized(self) -> Self:
        _weights_sum_to_one(
            (self.occupancy_weight, self.transition_rows_weight, self.runs_weight),
            "post-fit transition component weights",
        )
        return self


class PostfitSettings(StrictModel):
    dispersion: DispersionSettings
    transition: TransitionSettings
    c2st: C2stSettings


class SimilarityConfig(StrictModel):
    iat_diagnostic_quantile: StrictFloat
    acf_lags: tuple[StrictInt, ...] = Field(min_length=1)
    acf_lag_weights: tuple[StrictFloat, ...] = Field(min_length=1)
    acf_iat_weight: StrictFloat
    acf_size_weight: StrictFloat
    multiscale_widths_seconds: tuple[StrictFloat, ...] = Field(min_length=1)
    multiscale_scale_weights: tuple[StrictFloat, ...] = Field(min_length=1)
    multiscale_packet_weight: StrictFloat
    multiscale_byte_weight: StrictFloat
    max_direction_bin_cells: AtLeastTwoInteger
    cvm_iat_weight: StrictFloat
    cvm_size_weight: StrictFloat
    ad_iat_weight: StrictFloat
    ad_size_weight: StrictFloat
    js_iat_bin_count: BoundedSimilarityAllocation
    js_iat_weight: StrictFloat
    js_mark_weight: StrictFloat
    mmd_feature_count: BoundedSimilarityAllocation
    mmd_seed: NonNegativeInteger
    mmd_scale_floor: PositiveFloat
    method_weights: MethodWeights
    postfit: PostfitSettings

    @field_validator("iat_diagnostic_quantile")
    @classmethod
    def iat_quantile_is_strictly_between_zero_and_one(cls, value: float) -> float:
        if not 0.0 < value < 1.0:
            raise ValueError("iat diagnostic quantile must be strictly between zero and one")
        return value

    @field_validator("acf_lags")
    @classmethod
    def acf_lags_are_unique_positive_integers(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(lag <= 0 for lag in value) or len(value) != len(set(value)):
            raise ValueError("acf lags must be unique positive integers")
        return value

    @field_validator("multiscale_widths_seconds")
    @classmethod
    def multiscale_widths_are_strictly_increasing(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if any(not math.isfinite(width) or width <= 0.0 for width in value):
            raise ValueError("multiscale widths must be finite and positive")
        if any(left >= right for left, right in zip(value, value[1:], strict=False)):
            raise ValueError("multiscale widths must be unique and strictly increasing")
        return value

    @model_validator(mode="after")
    def diagnostic_vectors_are_compatible(self) -> Self:
        if len(self.acf_lags) != len(self.acf_lag_weights):
            raise ValueError("acf lags and lag weights must have equal length")
        if len(self.multiscale_widths_seconds) != len(self.multiscale_scale_weights):
            raise ValueError("multiscale widths and scale weights must have equal length")
        _weights_sum_to_one(self.acf_lag_weights, "acf lag weights")
        _weights_sum_to_one((self.acf_iat_weight, self.acf_size_weight), "acf component weights")
        _weights_sum_to_one(self.multiscale_scale_weights, "multiscale scale weights")
        _weights_sum_to_one(
            (self.multiscale_packet_weight, self.multiscale_byte_weight),
            "multiscale component weights",
        )
        _weights_sum_to_one((self.cvm_iat_weight, self.cvm_size_weight), "Cramér--von Mises feature weights")
        _weights_sum_to_one((self.ad_iat_weight, self.ad_size_weight), "Anderson--Darling feature weights")
        _weights_sum_to_one((self.js_iat_weight, self.js_mark_weight), "Jensen--Shannon feature weights")
        return self


class ExperimentConfig(StrictModel):
    run: RunConfig
    target: TargetConfig
    capture: CaptureConfig
    generation: GenerationConfig
    genetic: GeneticConfig
    models: ModelsConfig
    similarity: SimilarityConfig

    @model_validator(mode="after")
    def cross_section_values_are_compatible(self) -> Self:
        genetic = self.genetic
        if genetic.population_size < 2:
            raise ValueError("population size must be at least two")
        if genetic.elite_count >= genetic.population_size:
            raise ValueError("elite count must be less than population size")
        if genetic.population_size < genetic.elite_count + len(self.models.enabled):
            raise ValueError("population size must include elites and each enabled family")
        if not 2 <= genetic.tournament_size <= genetic.population_size:
            raise ValueError("tournament size must be between two and population size")
        if len(genetic.trial_seeds) != len(set(genetic.trial_seeds)):
            raise ValueError("trial seeds must be unique")
        if self.run.final_seed in genetic.trial_seeds:
            raise ValueError("final seed must not be one of the genetic trial seeds")
        if genetic.early_stopping_generations > genetic.generation_count:
            raise ValueError("early stopping generations must not exceed generation count")

        capture = self.capture
        for stage_name, timeout in (
            ("readiness", capture.readiness_timeout_seconds),
            ("workload", capture.workload_timeout_seconds),
            ("flush", capture.flush_timeout_seconds),
        ):
            if timeout > capture.total_timeout_seconds:
                raise ValueError(f"{stage_name} timeout must not exceed total timeout")

        trial = self.generation.trial
        final = self.generation.final
        if final.max_packets < trial.max_packets:
            raise ValueError("final max packets must be at least trial max packets")
        if final.max_output_bytes < trial.max_output_bytes:
            raise ValueError("final max output bytes must be at least trial max output bytes")
        if final.max_wall_seconds < trial.max_wall_seconds:
            raise ValueError("final max wall seconds must be at least trial max wall seconds")
        return self
