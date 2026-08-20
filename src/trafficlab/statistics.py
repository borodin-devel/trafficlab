"""Reproducible descriptive statistical summaries used by study reporting."""

import copy
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol, cast

import numpy as np
from scipy import stats as scipy_stats  # pyright: ignore[reportMissingTypeStubs]

from trafficlab.errors import TrafficlabError


class _ConfidenceInterval(Protocol):
    """The lower and upper values returned by SciPy bootstrap."""

    low: float
    high: float


class _BootstrapResult(Protocol):
    """The result fields Trafficlab consumes from SciPy bootstrap."""

    confidence_interval: _ConfidenceInterval


class _Bootstrap(Protocol):
    """Typed boundary around the SciPy bootstrap callable."""

    def __call__(
        self,
        data: tuple[np.ndarray[tuple[int], np.dtype[np.float64]], ...],
        statistic: Callable[..., object],
        *,
        n_resamples: int,
        confidence_level: float,
        method: str,
        rng: np.random.Generator,
    ) -> _BootstrapResult: ...


_bootstrap = cast(_Bootstrap, cast(Any, scipy_stats).bootstrap)


@dataclass(frozen=True, slots=True)
class BootstrapInterval:
    """A complete, reproducible percentile-bootstrap mean interval record."""

    confidence_level: float
    generator: str
    generator_state: dict[str, object]
    lower_bound: float
    method: str
    n_resamples: int
    sample_size: int
    seed: int
    statistic: str
    upper_bound: float

    def as_dict(self) -> dict[str, object]:
        """Return a detached JSON-compatible record suitable for report inputs."""
        return {
            "confidence_level": self.confidence_level,
            "generator": self.generator,
            "generator_state": copy.deepcopy(self.generator_state),
            "lower_bound": self.lower_bound,
            "method": self.method,
            "n_resamples": self.n_resamples,
            "sample_size": self.sample_size,
            "seed": self.seed,
            "statistic": self.statistic,
            "upper_bound": self.upper_bound,
        }


def _validated_sample(values: Iterable[object]) -> tuple[float, ...]:
    try:
        materialized = tuple(values)
    except TypeError as error:
        raise TrafficlabError(
            "invalid bootstrap sample: values must be iterable",
            corrective_action="provide a nonempty iterable of finite numeric values",
        ) from error
    if not materialized:
        raise TrafficlabError(
            "invalid bootstrap sample: at least one value is required",
            corrective_action="provide a nonempty iterable of finite numeric values",
        )
    sample: list[float] = []
    for value in materialized:
        if type(value) is not int and type(value) is not float:
            raise TrafficlabError(
                "invalid bootstrap sample: values must be finite numbers",
                corrective_action="provide a nonempty iterable of finite numeric values",
            )
        try:
            converted = float(value)
        except OverflowError as error:
            raise TrafficlabError(
                "invalid bootstrap sample: values must be finite numbers",
                corrective_action="provide a nonempty iterable of finite numeric values",
            ) from error
        if not math.isfinite(converted):
            raise TrafficlabError(
                "invalid bootstrap sample: values must be finite numbers",
                corrective_action="provide a nonempty iterable of finite numeric values",
            )
        sample.append(converted)
    return tuple(sample)


def _validated_seed(seed: object) -> int:
    if type(seed) is not int or seed < 0:
        raise TrafficlabError(
            "invalid bootstrap seed: it must be a nonnegative integer",
            corrective_action="provide a nonnegative integer seed for PCG64",
        )
    return seed


def _validated_resamples(n_resamples: object) -> int:
    if type(n_resamples) is not int or n_resamples <= 0:
        raise TrafficlabError(
            "invalid bootstrap resamples: it must be a positive integer",
            corrective_action="provide a positive number of bootstrap resamples",
        )
    return n_resamples


def _validated_confidence_level(confidence_level: object) -> float:
    if type(confidence_level) is not float or not math.isfinite(confidence_level) or not 0.0 < confidence_level < 1.0:
        raise TrafficlabError(
            "invalid bootstrap confidence level: it must be a finite float strictly between zero and one",
            corrective_action="provide a confidence level with 0 < confidence_level < 1",
        )
    return confidence_level


def bootstrap_interval(
    values: Iterable[object],
    *,
    seed: object,
    n_resamples: object = 10_000,
    confidence_level: object = 0.95,
) -> BootstrapInterval:
    """Return a seeded percentile-bootstrap mean interval without hypothesis-test output."""
    sample = _validated_sample(values)
    validated_seed = _validated_seed(seed)
    validated_resamples = _validated_resamples(n_resamples)
    validated_confidence = _validated_confidence_level(confidence_level)
    try:
        generator = np.random.Generator(np.random.PCG64(validated_seed))
        initial_state = copy.deepcopy(cast(dict[str, object], generator.bit_generator.state))
        result = _bootstrap(
            (np.asarray(sample, dtype=np.float64),),
            np.mean,
            n_resamples=validated_resamples,
            confidence_level=validated_confidence,
            method="percentile",
            rng=generator,
        )
        lower_bound = float(result.confidence_interval.low)
        upper_bound = float(result.confidence_interval.high)
        if all(value == sample[0] for value in sample[1:]):
            lower_bound = upper_bound = sample[0]
    except (ArithmeticError, TypeError, ValueError) as error:
        raise TrafficlabError(
            "invalid bootstrap sample: percentile interval could not be evaluated",
            corrective_action="provide finite numeric values and supported bootstrap settings",
        ) from error
    if not math.isfinite(lower_bound) or not math.isfinite(upper_bound) or lower_bound > upper_bound:
        raise TrafficlabError(
            "invalid bootstrap interval: computation produced nonfinite or inverted bounds",
            corrective_action="provide finite numeric values and supported bootstrap settings",
        )
    return BootstrapInterval(
        confidence_level=validated_confidence,
        generator="PCG64",
        generator_state=initial_state,
        lower_bound=lower_bound,
        method="percentile",
        n_resamples=validated_resamples,
        sample_size=len(sample),
        seed=validated_seed,
        statistic="mean",
        upper_bound=upper_bound,
    )
