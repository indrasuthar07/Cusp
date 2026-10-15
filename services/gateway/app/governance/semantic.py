"""Semantic-loop score primitives used by deterministic governance."""

from __future__ import annotations

import math
from collections.abc import Sequence


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("embedding vectors must have equal non-zero dimensions")
    denominator = math.sqrt(sum(v * v for v in left) * sum(v * v for v in right))
    if denominator == 0:
        return 0.0
    return max(
        -1.0,
        min(1.0, sum(a * b for a, b in zip(left, right, strict=True)) / denominator),
    )


def semantic_loop_score(
    similarities: Sequence[float], *, threshold: float = 0.97
) -> float:
    """Return a bounded score; values below threshold are not loop evidence."""
    if not 0 <= threshold <= 1:
        raise ValueError("similarity threshold must be between 0 and 1")
    qualifying = [value for value in similarities if value >= threshold]
    if not qualifying:
        return 0.0
    return min(1.0, max(qualifying))
