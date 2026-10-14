"""Offline embedding/cache and semantic-loop contract tests."""

from __future__ import annotations

import json

import pytest
from app.governance.engine import GovernanceContext, PolicyResolver, ProgressSignal
from app.governance.semantic import cosine_similarity, semantic_loop_score

from services.worker.app.embedding import (
    DeterministicEmbedder,
    EmbeddingCache,
    EmbeddingWorker,
)
from services.worker.app.embedding import cosine_similarity as worker_cosine_similarity


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiries: dict[str, int] = {}
        self.jobs: list[dict[str, str]] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.expiries[key] = ex

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.jobs.append({"stream": stream, **fields})
        return "1-0"


@pytest.mark.asyncio
async def test_embedding_cache_stores_only_digest_and_reuses_vector() -> None:
    redis = _Redis()
    worker = EmbeddingWorker(EmbeddingCache(redis), DeterministicEmbedder(16))
    first = await worker.embed("private source code and token=secret")
    second = await worker.embed("private source code and token=secret")
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert first.vector == second.vector
    assert (
        first.content_hash in redis.values
        or EmbeddingCache.key(first.content_hash) in redis.values
    )
    assert "private source" not in json.dumps(redis.values)


@pytest.mark.asyncio
async def test_embedding_job_contains_no_source_text() -> None:
    redis = _Redis()
    worker = EmbeddingWorker(EmbeddingCache(redis))
    await worker.enqueue(redis, text="do not persist this prompt", run_id="run-1")
    assert redis.jobs[0]["content_hash"]
    assert "do not persist" not in json.dumps(redis.jobs)


def test_cosine_and_score_are_bounded_and_deterministic() -> None:
    assert cosine_similarity([1, 0], [1, 0]) == 1
    assert cosine_similarity([1, 0], [0, 1]) == 0
    assert worker_cosine_similarity([1, 0], [1, 0]) == 1
    assert semantic_loop_score([0.2, 0.98, 0.99]) == 0.99
    assert semantic_loop_score([0.2, 0.5]) == 0


def test_semantic_no_progress_triggers_pause_only_with_embedding_evidence() -> None:
    from app.governance.engine import DecisionTokenSigner

    resolver = PolicyResolver(
        {
            "default_action": "allow",
            "loop_detection": {
                "semantic_similarity_threshold": 0.97,
                "semantic_repeat_limit": 1,
            },
        },
        policy_version=1,
        signer=DecisionTokenSigner("s" * 32),
    )
    decision = resolver.resolve(
        GovernanceContext(
            action_id="semantic-1",
            provider_profile="fake",
            estimated_cost_micro_usd=0,
            progress=ProgressSignal(semantic_similarity=0.99, embedding_available=True),
        )
    )
    assert decision.verdict == "pause"
    assert decision.reason_code == "semantic_repeat_no_progress"
    no_embedding = resolver.resolve(
        GovernanceContext(
            action_id="semantic-2",
            provider_profile="fake",
            estimated_cost_micro_usd=0,
            progress=ProgressSignal(semantic_similarity=0.99),
        )
    )
    assert no_embedding.verdict == "allow"
