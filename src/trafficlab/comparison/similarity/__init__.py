"""Exact, interpretable similarity metrics for canonical traffic traces."""

from trafficlab.comparison.similarity.common import SimilarityResult
from trafficlab.comparison.similarity.ks import frame_size_ks, iat_ks

__all__ = ["SimilarityResult", "frame_size_ks", "iat_ks"]
