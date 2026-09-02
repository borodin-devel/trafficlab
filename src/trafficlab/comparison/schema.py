"""Traffic comparison schema ownership."""

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Self, cast

from pydantic import (
    BaseModel,
    Field,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from trafficlab.common.compatibility import ContentIdentity
from trafficlab.comparison.diagnostics import (
    FITNESS_METHOD_NAMES,
    WEIGHT_TOLERANCE,
    AndersonDarlingDiagnostic,
    ApproximateMmdDiagnostic,
    AutocorrelationDiagnostic,
    CramerVonMisesDiagnostic,
    FrameSizeDiagnostic,
    IatDiagnostic,
    JensenShannonDiagnostic,
    MethodDiagnostic,
    MultiscaleDiagnostic,
    NonnegativeInt,
    PositiveFloat,
    StrictArtifactModel,
    UnitFloat,
    diagnostic_discriminator,
    require_close,
)
from trafficlab.comparison.similarity.common import JsonValue, SimilarityResult

INPUT_NAMES = ("capture_json", "generated_pcapng", "reference_pcapng", "similarity_settings")


class MethodComparison(StrictArtifactModel):
    """One configured method's immutable score, weight, and retained diagnostics."""

    score: UnitFloat
    weight: UnitFloat
    diagnostics: MethodDiagnostic

    @field_validator("diagnostics", mode="before")
    @classmethod
    def thaw_diagnostics(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return SimilarityResult(0.0, cast(Mapping[str, object], value)).as_dict()["diagnostics"]

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        diagnostic_tag = diagnostic_discriminator(self.diagnostics)
        if diagnostic_tag is None:
            raise ValueError("diagnostics must identify one supported method")
        discrepancy = (
            self.diagnostics.distance
            if isinstance(self.diagnostics, (FrameSizeDiagnostic, IatDiagnostic))
            else self.diagnostics.discrepancy
        )
        require_close(self.score, 1.0 - discrepancy, name=f"{diagnostic_tag} score")
        return self

    def as_dict(self) -> dict[str, JsonValue]:
        """Return a fresh ordinary JSON representation."""
        return {
            "diagnostics": cast(dict[str, JsonValue], self.diagnostics.model_dump(mode="json")),
            "score": self.score,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, method_name: str, value: object) -> Self:
        """Strictly validate one method object from parsed JSON."""
        if method_name not in FITNESS_METHOD_NAMES:
            raise ValueError(f"unsupported comparison method {method_name!r}")
        prepared: object = value
        if type(value) is dict:
            fields = dict(cast(dict[str, object], value))
            if "method" in fields:
                fields["_persisted_method"] = fields.pop("method")
            prepared = fields
        try:
            result = cls.model_validate(prepared)
            if diagnostic_discriminator(result.diagnostics) != method_name:
                raise ValueError(f"{method_name} diagnostics use the wrong method discriminator")
            return result
        except ValidationError as error:
            first = error.errors()[0]
            field = ".".join(str(part) for part in first["loc"])
            if field.endswith("observation_window_seconds"):
                raise ValueError(
                    "every method diagnostic observation window must be a finite positive float"
                ) from error
            if field.endswith("reference_count") and first["type"] == "int_type":
                raise ValueError("reference_count must be an integer") from error
            raise ValueError(f"invalid method result {field}: {first['msg']}") from error


class ComparisonMethods(StrictArtifactModel):
    autocorrelation: MethodComparison
    frame_size_ks: MethodComparison
    iat_ks: MethodComparison
    multiscale_rate: MethodComparison
    cramer_von_mises: MethodComparison
    anderson_darling: MethodComparison
    jensen_shannon: MethodComparison
    approximate_mmd: MethodComparison

    @field_validator(
        "autocorrelation",
        "frame_size_ks",
        "iat_ks",
        "multiscale_rate",
        "cramer_von_mises",
        "anderson_darling",
        "jensen_shannon",
        "approximate_mmd",
        mode="before",
    )
    @classmethod
    def methods_are_reconstructed_from_primitives(cls, value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="python")
        return value

    @model_validator(mode="after")
    def mapping_keys_match_diagnostics(self) -> Self:
        for name, method in self.items():
            if diagnostic_discriminator(method.diagnostics) != name:
                raise ValueError(f"{name} diagnostics use the wrong method discriminator")
        return self

    def __getitem__(self, name: str) -> MethodComparison:
        if name not in FITNESS_METHOD_NAMES:
            raise KeyError(name)
        return cast(MethodComparison, getattr(self, name))

    def keys(self) -> tuple[str, ...]:
        return FITNESS_METHOD_NAMES

    def items(self) -> tuple[tuple[str, MethodComparison], ...]:
        return tuple((name, self[name]) for name in FITNESS_METHOD_NAMES)

    def values(self) -> tuple[MethodComparison, ...]:
        return tuple(self[name] for name in FITNESS_METHOD_NAMES)


class ContentIdentityPayload(StrictArtifactModel):
    size: NonnegativeInt
    sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


class ComparisonInputIdentities(StrictArtifactModel):
    capture_json: ContentIdentityPayload
    generated_pcapng: ContentIdentityPayload
    reference_pcapng: ContentIdentityPayload
    similarity_settings: ContentIdentityPayload

    @field_validator("capture_json", "generated_pcapng", "reference_pcapng", "similarity_settings", mode="before")
    @classmethod
    def identities_are_reconstructed_from_primitives(cls, value: object) -> object:
        if type(value) is ContentIdentity:
            return value.as_dict()
        if isinstance(value, BaseModel):
            return value.model_dump(mode="python")
        return value

    def __getitem__(self, name: str) -> ContentIdentityPayload:
        if name not in INPUT_NAMES:
            raise KeyError(name)
        return cast(ContentIdentityPayload, getattr(self, name))

    def keys(self) -> tuple[str, ...]:
        return INPUT_NAMES

    def items(self) -> tuple[tuple[str, ContentIdentityPayload], ...]:
        return tuple((name, self[name]) for name in INPUT_NAMES)

    def as_content_identities(self) -> dict[str, ContentIdentity]:
        return {name: ContentIdentity(size=identity.size, sha256=identity.sha256) for name, identity in self.items()}


def _require_method_score(score: float, discrepancy: float, *, name: str) -> None:
    require_close(score, 1.0 - discrepancy, name=f"{name} score")


class PublishedAutocorrelationMethod(StrictArtifactModel):
    diagnostics: AutocorrelationDiagnostic
    score: UnitFloat
    weight: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        _require_method_score(self.score, self.diagnostics.discrepancy, name="autocorrelation")
        return self


class PublishedFrameSizeMethod(StrictArtifactModel):
    diagnostics: FrameSizeDiagnostic
    score: UnitFloat
    weight: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        _require_method_score(self.score, self.diagnostics.distance, name="frame_size_ks")
        return self


class PublishedIatMethod(StrictArtifactModel):
    diagnostics: IatDiagnostic
    score: UnitFloat
    weight: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        _require_method_score(self.score, self.diagnostics.distance, name="iat_ks")
        return self


class PublishedMultiscaleMethod(StrictArtifactModel):
    diagnostics: MultiscaleDiagnostic
    score: UnitFloat
    weight: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        _require_method_score(self.score, self.diagnostics.discrepancy, name="multiscale_rate")
        return self


class PublishedCramerVonMisesMethod(StrictArtifactModel):
    diagnostics: CramerVonMisesDiagnostic
    score: UnitFloat
    weight: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        _require_method_score(self.score, self.diagnostics.discrepancy, name="cramer_von_mises")
        return self


class PublishedAndersonDarlingMethod(StrictArtifactModel):
    diagnostics: AndersonDarlingDiagnostic
    score: UnitFloat
    weight: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        _require_method_score(self.score, self.diagnostics.discrepancy, name="anderson_darling")
        return self


class PublishedJensenShannonMethod(StrictArtifactModel):
    diagnostics: JensenShannonDiagnostic
    score: UnitFloat
    weight: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        _require_method_score(self.score, self.diagnostics.discrepancy, name="jensen_shannon")
        return self


class PublishedApproximateMmdMethod(StrictArtifactModel):
    diagnostics: ApproximateMmdDiagnostic
    score: UnitFloat
    weight: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        _require_method_score(self.score, self.diagnostics.discrepancy, name="approximate_mmd")
        return self


type PublishedMethod = (
    PublishedAutocorrelationMethod
    | PublishedFrameSizeMethod
    | PublishedIatMethod
    | PublishedMultiscaleMethod
    | PublishedCramerVonMisesMethod
    | PublishedAndersonDarlingMethod
    | PublishedJensenShannonMethod
    | PublishedApproximateMmdMethod
)


class PublishedComparisonMethods(StrictArtifactModel):
    autocorrelation: PublishedAutocorrelationMethod
    frame_size_ks: PublishedFrameSizeMethod
    iat_ks: PublishedIatMethod
    multiscale_rate: PublishedMultiscaleMethod
    cramer_von_mises: PublishedCramerVonMisesMethod
    anderson_darling: PublishedAndersonDarlingMethod
    jensen_shannon: PublishedJensenShannonMethod
    approximate_mmd: PublishedApproximateMmdMethod

    def items(self) -> tuple[tuple[str, PublishedMethod], ...]:
        return tuple(
            (name, cast(PublishedMethod, getattr(self, name))) for name in FITNESS_METHOD_NAMES
        )


class PublishedComparisonResult(StrictArtifactModel):
    aggregate_score: UnitFloat
    input_identities: ComparisonInputIdentities
    methods: PublishedComparisonMethods
    observation_window_seconds: PositiveFloat

    @model_validator(mode="after")
    def validate_publication_arithmetic(self) -> Self:
        for _name, method in self.methods.items():
            if method.diagnostics.observation_window_seconds != self.observation_window_seconds:
                raise ValueError("every method diagnostic must contain the shared observation window")
        weights = tuple(method.weight for _name, method in self.methods.items())
        if not math.isclose(math.fsum(weights), 1.0, rel_tol=0.0, abs_tol=WEIGHT_TOLERANCE):
            raise ValueError("method weights must sum to one")
        expected = math.fsum(method.weight * method.score for _name, method in self.methods.items())
        if not math.isclose(expected, self.aggregate_score, rel_tol=0.0, abs_tol=WEIGHT_TOLERANCE):
            raise ValueError("aggregate_score must equal the exact configured weighted sum")
        return self


class ComparisonResult(StrictArtifactModel):
    """One deeply immutable comparison result, optionally carrying artifact identities."""

    # Construction copies nested method and identity mappings before exposing
    # them.  Callers can safely retain this object as publication evidence even
    # if the dictionaries used to build it are later mutated.

    aggregate_score: UnitFloat
    observation_window_seconds: PositiveFloat
    methods: ComparisonMethods
    input_identities: ComparisonInputIdentities | None

    @field_validator("methods", mode="before")
    @classmethod
    def methods_are_reconstructed_from_primitives(cls, value: object) -> object:
        if isinstance(value, ComparisonMethods):
            return value.model_dump(mode="python")
        if isinstance(value, Mapping):
            methods = cast(Mapping[str, object], value)
            return {
                name: method.model_dump(mode="python") if isinstance(method, BaseModel) else method
                for name, method in methods.items()
            }
        return value

    @field_validator("input_identities", mode="before")
    @classmethod
    def input_identities_are_reconstructed_from_primitives(cls, value: object) -> object:
        if isinstance(value, ComparisonInputIdentities):
            return value.model_dump(mode="python")
        if isinstance(value, Mapping):
            identities = cast(Mapping[str, object], value)
            return {
                name: identity.as_dict()
                if type(identity) is ContentIdentity
                else identity.model_dump(mode="python")
                if isinstance(identity, BaseModel)
                else identity
                for name, identity in identities.items()
            }
        return value

    @model_validator(mode="after")
    def validate_local_arithmetic(self) -> Self:
        for _name, method in self.methods.items():
            diagnostic_window = method.diagnostics.get("observation_window_seconds")
            if diagnostic_window != self.observation_window_seconds:
                raise ValueError("every method diagnostic must contain the shared observation window")
        weight_sum = math.fsum(method.weight for method in self.methods.values())
        if not math.isclose(weight_sum, 1.0, rel_tol=0.0, abs_tol=WEIGHT_TOLERANCE):
            raise ValueError("method weights must sum to one")
        weighted_score = math.fsum(method.weight * method.score for method in self.methods.values())
        if not math.isclose(weighted_score, self.aggregate_score, rel_tol=0.0, abs_tol=WEIGHT_TOLERANCE):
            raise ValueError("aggregate_score must equal the exact configured weighted sum")
        return self

    @property
    def input_sha256(self) -> Mapping[str, str] | None:
        """Expose digests for diagnostics that do not need byte counts."""
        if self.input_identities is None:
            return None
        return MappingProxyType({name: identity.sha256 for name, identity in self.input_identities.items()})

    def with_input_identities(
        self,
        identities: Mapping[str, ContentIdentity | ContentIdentityPayload] | ComparisonInputIdentities,
    ) -> Self:
        """Return the same scientific result with exact file and settings identities."""
        return type(self).model_validate(
            {
                "aggregate_score": self.aggregate_score,
                "observation_window_seconds": self.observation_window_seconds,
                "methods": self.methods,
                "input_identities": identities,
            }
        )

    def as_dict(self) -> dict[str, JsonValue]:
        """Return the exact publishable JSON shape as fresh mutable values."""
        return cast(dict[str, JsonValue], published_comparison_result(self).model_dump(mode="json"))

    @classmethod
    def from_dict(cls, value: object) -> Self:
        """Strictly validate the documented similarity artifact object."""
        try:
            return cls.model_validate(value)
        except ValidationError as error:
            first = error.errors()[0]
            field = ".".join(str(part) for part in first["loc"])
            if field.endswith("observation_window_seconds"):
                raise ValueError(
                    "every method diagnostic observation window must be a finite positive float"
                ) from error
            if field.endswith("reference_count") and first["type"] == "int_type":
                raise ValueError("reference_count must be an integer") from error
            raise ValueError(f"invalid comparison result {field}: {first['msg']}") from error


def published_comparison_result(result: ComparisonResult) -> PublishedComparisonResult:
    """Revalidate one operational result as the exact required publication wire root."""
    if result.input_identities is None:
        raise ValueError("input content identities are required for a similarity artifact")
    raw_methods = cast(object, result.methods)
    if isinstance(raw_methods, ComparisonMethods):
        methods: Mapping[str, object] = dict(raw_methods.items())
    elif isinstance(raw_methods, Mapping):
        methods = cast(Mapping[str, object], raw_methods)
    else:
        raise ValueError("comparison methods must be a canonical methods object")
    raw_identities = cast(object, result.input_identities)
    identities: object = (
        raw_identities.model_dump(mode="python")
        if isinstance(raw_identities, ComparisonInputIdentities)
        else raw_identities
    )
    return PublishedComparisonResult.model_validate(
        {
            "aggregate_score": result.aggregate_score,
            "input_identities": identities,
            "methods": {
                name: method.model_dump(mode="python") if isinstance(method, BaseModel) else method
                for name, method in methods.items()
            },
            "observation_window_seconds": result.observation_window_seconds,
        }
    )


def operational_comparison_result(published: PublishedComparisonResult) -> ComparisonResult:
    """Build the in-process comparison value from one validated publication wire root."""
    return ComparisonResult.model_validate(published.model_dump(mode="python"))
