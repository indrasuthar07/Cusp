"""Bounded local embedding jobs with a privacy-preserving Redis cache.

The cache stores vectors under a SHA-256 content key and never stores the text
that produced them. ``ONNXSessionEmbedder`` is injectable so a downloaded
MiniLM tokenizer/model can be supplied in production; deterministic hashing is
the safe offline fallback when ONNX is unavailable.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import redis.asyncio as aioredis

EMBEDDING_STREAM = "cusp:v1:embeddings"
DEFAULT_DIMENSION = 384


class Embedder(Protocol):
    def embed(self, text: str) -> Sequence[float]: ...


class DeterministicEmbedder:
    """Dependency-free normalized vector fallback for offline operation."""

    def __init__(self, dimension: int = DEFAULT_DIMENSION) -> None:
        if dimension < 8:
            raise ValueError("embedding dimension must be at least 8")
        self.dimension = dimension

    def embed(self, text: str) -> tuple[float, ...]:
        values = [0.0] * self.dimension
        encoded = text.encode("utf-8")
        for index in range(0, len(encoded), 4):
            digest = hashlib.blake2b(encoded[index : index + 4], digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            values[bucket] += sign
        return _normalize(values)


class ONNXSessionEmbedder:
    """Run a caller-supplied tokenizer and ONNX Runtime session.

    The tokenizer must return model input arrays (for example input IDs and
    attention mask). The first model output may be a pooled vector or a token
    matrix; token matrices are mean-pooled using the attention mask.
    """

    def __init__(
        self,
        model_path: str,
        tokenizer: Callable[[str], Mapping[str, Any]],
    ) -> None:
        try:
            import numpy as np  # type: ignore[reportMissingImports]
            import onnxruntime as ort  # type: ignore[reportMissingImports]
        except ImportError as exc:  # pragma: no cover - depends on optional runtime
            raise RuntimeError(
                "onnxruntime and numpy are required for ONNX embeddings"
            ) from exc
        self._np = np
        self._tokenizer = tokenizer
        self._session = ort.InferenceSession(
            model_path, providers=["CPUExecutionProvider"]
        )

    def embed(self, text: str) -> tuple[float, ...]:
        inputs = dict(self._tokenizer(text))
        outputs = self._session.run(None, inputs)
        if not outputs:
            raise RuntimeError("ONNX embedding model returned no output")
        vector = self._np.asarray(outputs[0], dtype=self._np.float32)
        if vector.ndim == 3:
            mask = self._np.asarray(
                inputs.get("attention_mask"), dtype=self._np.float32
            )
            pooled = (vector * mask[..., None]).sum(axis=1) / self._np.maximum(
                mask.sum(axis=1, keepdims=True), 1.0
            )
            vector = pooled
        vector = vector.reshape(-1)
        return _normalize(vector.tolist())


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    content_hash: str
    vector: tuple[float, ...]
    cache_hit: bool


class EmbeddingCache:
    """Redis vector cache keyed only by a one-way content digest."""

    def __init__(
        self, redis_client: aioredis.Redis, *, ttl_seconds: int = 86_400
    ) -> None:
        if ttl_seconds < 1:
            raise ValueError("embedding cache TTL must be positive")
        self.redis = redis_client
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def key(content_hash: str) -> str:
        return f"cusp:v1:embedding:{content_hash}"

    async def get(self, content_hash: str) -> tuple[float, ...] | None:
        raw = await self.redis.get(self.key(content_hash))
        if raw is None:
            return None
        try:
            values = json.loads(raw)
            if not isinstance(values, list) or not all(
                isinstance(v, (int, float)) for v in values
            ):
                return None
            return tuple(float(v) for v in values)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    async def set(self, content_hash: str, vector: Sequence[float]) -> None:
        await self.redis.set(
            self.key(content_hash),
            json.dumps([float(v) for v in vector], separators=(",", ":")),
            ex=self.ttl_seconds,
        )


class EmbeddingWorker:
    """Compute embeddings off the event loop and cache only vectors."""

    def __init__(self, cache: EmbeddingCache, embedder: Embedder | None = None) -> None:
        self.cache = cache
        self.embedder = embedder or DeterministicEmbedder()

    async def embed(self, text: str) -> EmbeddingResult:
        if not text.strip():
            raise ValueError("embedding text must not be empty")
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cached = await self.cache.get(content_hash)
        if cached is not None:
            return EmbeddingResult(content_hash, cached, True)
        vector = tuple(
            float(value) for value in await asyncio.to_thread(self.embedder.embed, text)
        )
        if not vector or not all(math.isfinite(value) for value in vector):
            raise ValueError("embedding backend returned an invalid vector")
        vector = _normalize(vector)
        await self.cache.set(content_hash, vector)
        return EmbeddingResult(content_hash, vector, False)

    async def enqueue(self, redis: aioredis.Redis, *, text: str, run_id: str) -> str:
        """Queue a job containing only a digest; source text is not persisted."""
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        message_id = await redis.xadd(
            EMBEDDING_STREAM,
            {"content_hash": content_hash, "run_id": run_id},
        )
        return message_id.decode() if isinstance(message_id, bytes) else str(message_id)


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


def _normalize(values: Sequence[float]) -> tuple[float, ...]:
    magnitude = math.sqrt(sum(value * value for value in values))
    if magnitude == 0:
        return tuple(0.0 for _ in values)
    return tuple(value / magnitude for value in values)
