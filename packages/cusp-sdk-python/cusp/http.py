"""Small standard-library HTTP transport for the Cusp SDK lifecycle."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, is_dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .sdk import (
    ActionRequest,
    CompletionReport,
    ControlAction,
    ControlCommand,
    Decision,
    DecisionVerdict,
    Run,
    RunState,
)


class GatewayTransportError(RuntimeError):
    """A safe, provider-neutral gateway transport failure."""


class HttpGatewayTransport:
    """Async transport for the versioned Cusp gateway REST lifecycle."""

    def __init__(self, base_url: str, service_token: str, timeout_seconds: float = 15.0) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("gateway base_url must use http:// or https://")
        if not service_token.strip():
            raise ValueError("service_token must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.base_url = base_url.rstrip("/") + "/"
        self._service_token = service_token
        self.timeout_seconds = timeout_seconds
        self._actions: dict[str, ActionRequest] = {}
        self._decisions: dict[str, Decision] = {}

    async def create_run(self, budget_cap_micro_usd: int, metadata: dict[str, str]) -> Run:
        payload = await self._call(
            "POST",
            "/api/v1/runs",
            {"budget_cap_micro_usd": budget_cap_micro_usd, "metadata": metadata},
        )
        return Run(
            run_id=str(payload["run_id"]),
            budget_cap_micro_usd=int(payload["budget_cap_micro_usd"]),
            state=RunState(payload.get("state", RunState.RUNNING)),
            metadata={str(k): str(v) for k, v in payload.get("metadata", {}).items()},
        )

    async def preflight(self, action: ActionRequest) -> Decision:
        payload = await self._call("POST", "/api/v1/actions/preflight", _action_payload(action))
        decision = Decision(
            action_id=str(payload.get("action_id", action.action_id)),
            verdict=DecisionVerdict(payload["verdict"]),
            reason_code=str(payload.get("reason_code", "unknown")),
            approved_profile=payload.get("approved_profile"),
            policy_version=int(payload.get("policy_version", 1)),
            decision_token=payload.get("decision_token"),
        )
        self._actions[decision.action_id] = action
        self._decisions[decision.action_id] = decision
        return decision

    async def report_completion(self, report: CompletionReport) -> None:
        action = self._actions.get(report.action_id)
        decision = self._decisions.get(report.action_id)
        if action is None or decision is None or not decision.decision_token:
            raise GatewayTransportError("completion references an unknown or unsigned action")
        await self._call("POST", "/api/v1/actions/completion", _completion_payload(report, action, decision))

    async def poll_control(self, run_id: str) -> ControlCommand:
        payload = await self._call("GET", f"/api/v1/runs/{run_id}/control")
        return ControlCommand(ControlAction(payload.get("action", ControlAction.CONTINUE)), str(payload.get("reason", "")))

    async def finish_run(self, run_id: str, state: RunState) -> None:
        await self._call("POST", f"/api/v1/runs/{run_id}/finish", {"state": state.value})

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(self._request, method, path, body)

    def _request(self, method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(
            urljoin(self.base_url, path.lstrip("/")),
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._service_token}",
            },
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise GatewayTransportError(f"gateway request failed with HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise GatewayTransportError("gateway request failed") from exc
        if not isinstance(decoded, dict):
            raise GatewayTransportError("gateway returned an invalid response")
        return decoded


def _action_payload(action: ActionRequest) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "action_id": action.action_id,
        "run_id": action.run_id,
        "sequence": action.sequence,
        "action_type": action.action_type,
        "provider_profile": action.provider_profile,
        "estimated_cost_micro_usd": action.estimated_cost_micro_usd,
        "idempotency_key": action.idempotency_key,
        "data_classification": action.data_classification,
        "summary": action.summary,
    }


def _completion_payload(report: CompletionReport, action: ActionRequest, decision: Decision) -> dict[str, Any]:
    usage = report.usage
    return {
        "schema_version": 1,
        "action_id": report.action_id,
        "status": report.status,
        "actual_cost_micro_usd": report.actual_cost_micro_usd,
        "estimated_cost_micro_usd": action.estimated_cost_micro_usd,
        "provider_profile": action.provider_profile,
        "decision_token": decision.decision_token,
        "error_code": report.error_code,
        "usage": asdict(usage) if is_dataclass(usage) else usage,
    }