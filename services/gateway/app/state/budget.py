"""
Python wrapper for Redis Lua budget scripts.
Loads and caches Lua scripts via EVALSHA for atomic execution.
"""
import os
import time
from typing import Any

from app.db.redis import get_redis_client

LUA_DIR = os.path.join(os.path.dirname(__file__), "lua")


class BudgetStateEngine:
    def __init__(self) -> None:
        self.redis = get_redis_client()
        self._reserve_sha: str | None = None
        self._release_sha: str | None = None
        self._rate_limit_sha: str | None = None

    async def _load_scripts(self) -> None:
        """Load Lua scripts into Redis and cache their SHAs."""
        if self._reserve_sha and self._release_sha and self._rate_limit_sha:
            return

        with open(os.path.join(LUA_DIR, "reserve_budget.lua"), "r") as f:
            self._reserve_sha = await self.redis.script_load(f.read())
        with open(os.path.join(LUA_DIR, "release_budget.lua"), "r") as f:
            self._release_sha = await self.redis.script_load(f.read())
        with open(os.path.join(LUA_DIR, "check_rate_limit.lua"), "r") as f:
            self._rate_limit_sha = await self.redis.script_load(f.read())

    async def initialize_run(self, run_id: str, org_id: str, budget_cap: int) -> None:
        """Initialize durable run budget and cooperative-control state."""
        await self.redis.hset(
            f"cusp:v1:run:{run_id}:state",
            mapping={"status": "running", "spent": 0, "reserved": 0, "cap": budget_cap, "org_id": org_id},
        )
        await self.redis.hset(
            f"cusp:v1:run:{run_id}:control",
            mapping={"action": "continue", "reason": ""},
        )

    async def set_control(self, run_id: str, action: str, reason: str = "") -> None:
        await self.redis.hset(
            f"cusp:v1:run:{run_id}:control",
            mapping={"action": action, "reason": reason},
        )

    async def poll_control(self, run_id: str) -> dict[str, str]:
        key = f"cusp:v1:run:{run_id}:control"
        result = await self.redis.hgetall(key)
        action = str(result.get("action", "continue"))
        reason = str(result.get("reason", ""))
        await self.redis.hset(key, mapping={"action": "continue", "reason": ""})
        return {"action": action, "reason": reason}

    async def reserve(
        self,
        run_id: str,
        org_id: str,
        idempotency_key: str,
        estimated_cost: int,
        run_cap: int,
        org_cap: int,
        task_group_id: str = "",
        task_group_cap: int = 0,
        *,
        action_id: str,
    ) -> dict[str, Any]:
        await self._load_scripts()
        assert self._reserve_sha is not None

        keys = [
            f"cusp:v1:run:{run_id}:state",
            f"cusp:v1:idem:{idempotency_key}" if idempotency_key else "",
            f"cusp:v1:budget:org:{org_id}",
            f"cusp:v1:budget:task:{task_group_id}" if task_group_id else "",
            f"cusp:v1:reservation:{action_id}",
        ]
        args = [
            idempotency_key,
            str(estimated_cost),
            str(run_cap),
            str(org_cap),
            str(task_group_cap),
            str(int(time.time())),
            action_id,
        ]

        # Use evalsha for atomic execution
        result = await self.redis.evalsha(self._reserve_sha, len(keys), *keys, *args)
        return result  # type: ignore

    async def release(
        self,
        run_id: str,
        org_id: str,
        estimated_cost: int,
        actual_cost: int,
        task_group_id: str = "",
        *,
        action_id: str,
    ) -> dict[str, Any]:
        await self._load_scripts()
        assert self._release_sha is not None

        keys = [
            f"cusp:v1:run:{run_id}:state",
            f"cusp:v1:budget:org:{org_id}",
            f"cusp:v1:budget:task:{task_group_id}" if task_group_id else "",
            f"cusp:v1:reservation:{action_id}",
        ]
        args = [
            str(actual_cost),
            str(int(time.time())),
            action_id,
        ]

        result = await self.redis.evalsha(self._release_sha, len(keys), *keys, *args)
        return result  # type: ignore

    async def check_rate_limit(
        self,
        principal_id: str,
        capacity: int,
        fill_rate: int,
        requested_tokens: int = 1,
    ) -> dict[str, Any]:
        await self._load_scripts()
        assert self._rate_limit_sha is not None

        keys = [f"cusp:v1:rate:{principal_id}"]
        args = [
            str(capacity),
            str(fill_rate),
            str(requested_tokens),
            str(int(time.time())),
        ]

        result = await self.redis.evalsha(self._rate_limit_sha, len(keys), *keys, *args)
        return result  # type: ignore

budget_engine = BudgetStateEngine()