"""Exact, interpretable similarity metrics for canonical traffic traces."""

from trafficlab.comparison.similarity.common import SimilarityResult
from trafficlab.comparison.similarity.ecdf import anderson_darling_similarity, cramer_von_mises_similarity
from trafficlab.comparison.similarity.ks import frame_size_ks, iat_ks

__all__ = [
    "SimilarityResult",
    "anderson_darling_similarity",
    "cramer_von_mises_similarity",
    "frame_size_ks",
    "iat_ks",
]
