"""Deterministic genetic-search contracts and coordinate primitives."""

from trafficlab.genetic.coordinates import CandidateEvaluationError, GeneCoordinate
from trafficlab.genetic.types import Candidate, CandidateId, MethodTrialResult, TrialResult

__all__ = [
    "Candidate",
    "CandidateEvaluationError",
    "CandidateId",
    "GeneCoordinate",
    "MethodTrialResult",
    "TrialResult",
]
