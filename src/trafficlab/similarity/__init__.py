"""Exact, interpretable similarity metrics for canonical traffic traces."""

from trafficlab.similarity.common import SimilarityResult
from trafficlab.similarity.ks import frame_size_ks, iat_ks

__all__ = ["SimilarityResult", "frame_size_ks", "iat_ks"]
