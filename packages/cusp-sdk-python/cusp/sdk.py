"""Cusp SDK run lifecycle and gateway transport contracts."""

from __future__ import annotations

import asyncio
import secrets
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Protocol

from cusp.providers.base import ProviderAdapter, ProviderRequest, ProviderResponse


class RunState(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    TERMINATED = "terminated"
    FAILED = "failed"


class DecisionVerdict(StrEnum):
    ALLOW = "allow"
    DOWNGRADE = "downgrade"
    PAUSE = "pause"
    ESCALATE = "escalate"
    KILL = "kill"


class ControlAction(StrEnum):
    CONTINUE = "continue"
    PAUSE = "pause"
    RESUME = "resume"
    TERMINATE = "terminate"


@dataclass(frozen=True, slots=True)
class Run:
    run_id: str
    budget_cap_micro_usd: int
    state: RunState = RunState.RUNNING
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActionRequest:
    action_id: str
    run_id: str
    sequence: int
    action_type: str
    provider_profile: str
    estimated_cost_micro_usd: int
    idempotency_key: str
    data_classification: str = "internal"
    summary: str = ""
    progress: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Decision:
    action_id: str
    verdict: DecisionVerdict
    reason_code: str
    approved_profile: str | None = None
    policy_version: int = 1
    decision_token: str | None = None


@dataclass(frozen=True, slots=True)
class CompletionReport:
    action_id: str
    status: str
    actual_cost_micro_usd: int
    usage: Any = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ControlCommand:
    action: ControlAction
    reason: str = ""


class GatewayTransport(Protocol):
    """Async boundary between the SDK and a Cusp gateway implementation."""

    async def create_run(
        self, budget_cap_micro_usd: int, metadata: dict[str, str]
    ) -> Run: ...

    async def preflight(self, action: ActionRequest) -> Decision: ...

    async def report_completion(self, report: CompletionReport) -> None: ...

    async def poll_control(self, run_id: str) -> ControlCommand: ...

    async def finish_run(self, run_id: str, state: RunState) -> None: ...


class GovernanceBlocked(RuntimeError):
    """Raised when governance does not authorize provider invocation."""

    def __init__(self, decision: Decision) -> None:
        super().__init__(
            f"action blocked: {decision.verdict.value} ({decision.reason_code})"
        )
        self.decision = decision


class BudgetOverrun(RuntimeError):
    """Raised after actual provider usage exceeds the run budget."""

    def __init__(
        self, action_id: str, actual_cost_micro_usd: int, budget_cap_micro_usd: int
    ) -> None:
        super().__init__(
            f"action {action_id} exceeded budget: "
            f"{actual_cost_micro_usd} > {budget_cap_micro_usd} micro-usd"
        )
        self.action_id = action_id
        self.actual_cost_micro_usd = actual_cost_micro_usd
        self.budget_cap_micro_usd = budget_cap_micro_usd


class CuspClient:
    """Entry point for creating governed SDK runs."""

    def __init__(self, transport: GatewayTransport) -> None:
        self.transport = transport

    async def create_run(
        self,
        *,
        budget_cap_micro_usd: int,
        metadata: dict[str, str] | None = None,
    ) -> CuspRun:
        if budget_cap_micro_usd < 0:
            raise ValueError("budget_cap_micro_usd cannot be negative")
        run = await self.transport.create_run(budget_cap_micro_usd, metadata or {})
        return CuspRun(self.transport, run)


class CuspRun:
    """A single agent run with ordered preflight and completion operations."""

    def __init__(self, transport: GatewayTransport, run: Run) -> None:
        self._transport = transport
        self.run = run
        self._sequence = 0
        self._actions: dict[str, ActionRequest] = {}
        self._decisions: dict[str, Decision] = {}

    async def preflight(
        self,
        *,
        action_type: str,
        provider_profile: str,
        estimated_cost_micro_usd: int,
        data_classification: str = "internal",
        summary: str = "",
        progress: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[ActionRequest, Decision]:
        if self.run.state is not RunState.RUNNING:
            raise GovernanceBlocked(
                Decision("", DecisionVerdict.PAUSE, f"run_{self.run.state.value}")
            )
        if estimated_cost_micro_usd < 0:
            raise ValueError("estimated_cost_micro_usd cannot be negative")
        self._sequence += 1
        action = ActionRequest(
            action_id=str(uuid.uuid4()),
            run_id=self.run.run_id,
            sequence=self._sequence,
            action_type=action_type,
            provider_profile=provider_profile,
            estimated_cost_micro_usd=estimated_cost_micro_usd,
            idempotency_key=idempotency_key
            or f"{self.run.run_id}:{self._sequence}:{secrets.token_hex(8)}",
            data_classification=data_classification,
            summary=summary,
            progress=progress or {},
        )
        decision = await self._transport.preflight(action)
        if decision.action_id != action.action_id:
            action = replace(action, action_id=decision.action_id)
        self._actions[action.action_id] = action
        self._decisions[action.action_id] = decision
        if (
            decision.verdict in {DecisionVerdict.PAUSE, DecisionVerdict.ESCALATE}
            and decision.reason_code != "budget_cap_exceeded"
        ):
            self.run = Run(
                self.run.run_id,
                self.run.budget_cap_micro_usd,
                RunState.PAUSED,
                self.run.metadata,
            )
        elif decision.verdict is DecisionVerdict.KILL:
            self.run = Run(
                self.run.run_id,
                self.run.budget_cap_micro_usd,
                RunState.TERMINATED,
                self.run.metadata,
            )
        return action, decision

    async def invoke(
        self,
        action: ActionRequest,
        adapter: ProviderAdapter,
        *,
        prompt: str,
        model: str,
        stream: bool = False,
        tools: tuple[dict[str, Any], ...] = (),
    ) -> ProviderResponse:
        decision = self._decisions.get(action.action_id)
        if decision is None:
            decision = await self._transport.preflight(action)
            self._decisions[action.action_id] = decision
        if decision.verdict not in {DecisionVerdict.ALLOW, DecisionVerdict.DOWNGRADE}:
            raise GovernanceBlocked(decision)
        approved_profile = decision.approved_profile or action.provider_profile
        if adapter.provider_kind != approved_profile:
            raise ValueError(
                f"adapter {adapter.provider_kind!r} does not match approved profile {approved_profile!r}"
            )
        request = ProviderRequest(
            prompt=prompt, model=model, stream=stream, tools=tools
        )
        try:
            response = await asyncio.to_thread(adapter.generate, request)
        except Exception as exc:
            error_code = getattr(exc, "code", "provider_error")
            await self._transport.report_completion(
                CompletionReport(action.action_id, "failed", 0, error_code=error_code)
            )
            raise
        actual_cost = (
            response.usage.provider_cost_micro_usd
            if response.usage.provider_cost_micro_usd is not None
            else response.usage.virtual_cost_micro_usd
        )
        try:
            await self._transport.report_completion(
                CompletionReport(
                    action.action_id, "completed", actual_cost, response.usage
                )
            )
        except BudgetOverrun:
            self.run = Run(
                self.run.run_id,
                self.run.budget_cap_micro_usd,
                RunState.FAILED,
                self.run.metadata,
            )
            raise
        return response

    async def report_completion(self, report: CompletionReport) -> None:
        if report.action_id not in self._actions:
            raise ValueError("completion references an unknown action")
        if report.actual_cost_micro_usd < 0:
            raise ValueError("actual_cost_micro_usd cannot be negative")
        try:
            await self._transport.report_completion(report)
        except BudgetOverrun:
            self.run = Run(
                self.run.run_id,
                self.run.budget_cap_micro_usd,
                RunState.FAILED,
                self.run.metadata,
            )
            raise

    async def poll_control(self) -> ControlCommand:
        command = await self._transport.poll_control(self.run.run_id)
        if command.action is ControlAction.PAUSE:
            self.run = Run(
                self.run.run_id,
                self.run.budget_cap_micro_usd,
                RunState.PAUSED,
                self.run.metadata,
            )
        elif command.action is ControlAction.RESUME:
            self.run = Run(
                self.run.run_id,
                self.run.budget_cap_micro_usd,
                RunState.RUNNING,
                self.run.metadata,
            )
        elif command.action is ControlAction.TERMINATE:
            self.run = Run(
                self.run.run_id,
                self.run.budget_cap_micro_usd,
                RunState.TERMINATED,
                self.run.metadata,
            )
        return command

    async def control_loop(
        self, interval_seconds: float = 1.0
    ) -> AsyncIterator[ControlCommand]:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        while self.run.state not in {
            RunState.COMPLETED,
            RunState.TERMINATED,
            RunState.FAILED,
        }:
            command = await self.poll_control()
            yield command
            if command.action is ControlAction.TERMINATE:
                return
            await asyncio.sleep(interval_seconds)

    async def finish(self, state: RunState = RunState.COMPLETED) -> None:
        if state not in {RunState.COMPLETED, RunState.TERMINATED, RunState.FAILED}:
            raise ValueError("finish state must be terminal")
        await self._transport.finish_run(self.run.run_id, state)
        self.run = Run(
            self.run.run_id, self.run.budget_cap_micro_usd, state, self.run.metadata
        )


class InMemoryGatewayTransport:
    """Deterministic local gateway for offline SDK/reference-agent runs."""

    def __init__(self) -> None:
        self.runs: dict[str, Run] = {}
        self.actions: dict[str, ActionRequest] = {}
        self.reservations: dict[str, int] = {}
        self.completions: dict[str, CompletionReport] = {}
        self.decisions: dict[str, Decision] = {}
        self.idempotency: dict[str, tuple[ActionRequest, Decision]] = {}
        self.spent: dict[str, int] = {}
        self.reserved: dict[str, int] = {}
        self.controls: dict[str, ControlCommand] = {}
        self._lock = asyncio.Lock()

    async def create_run(
        self, budget_cap_micro_usd: int, metadata: dict[str, str]
    ) -> Run:
        run = Run(str(uuid.uuid4()), budget_cap_micro_usd, metadata=metadata)
        self.runs[run.run_id] = run
        self.spent[run.run_id] = 0
        self.reserved[run.run_id] = 0
        self.controls[run.run_id] = ControlCommand(ControlAction.CONTINUE)
        return run

    async def preflight(self, action: ActionRequest) -> Decision:
        async with self._lock:
            existing = self.idempotency.get(action.idempotency_key)
            if existing is not None:
                original_action, original_decision = existing
                if (
                    original_action.run_id != action.run_id
                    or original_action.action_type != action.action_type
                    or original_action.provider_profile != action.provider_profile
                    or original_action.estimated_cost_micro_usd
                    != action.estimated_cost_micro_usd
                    or original_action.data_classification != action.data_classification
                    or original_action.summary != action.summary
                    or original_action.progress != action.progress
                ):
                    raise ValueError("idempotency_key_conflict")
                return original_decision

            run = self.runs[action.run_id]
            if run.state is not RunState.RUNNING:
                return Decision(
                    action.action_id, DecisionVerdict.PAUSE, f"run_{run.state.value}"
                )
            if (
                self.spent[action.run_id]
                + self.reserved[action.run_id]
                + action.estimated_cost_micro_usd
                > run.budget_cap_micro_usd
            ):
                # Rejected reservations are deliberately not entered into the
                # idempotency map, so a later retry can succeed after release.
                return Decision(
                    action.action_id, DecisionVerdict.PAUSE, "budget_cap_exceeded"
                )

            decision = Decision(
                action.action_id,
                DecisionVerdict.ALLOW,
                "policy_allow",
                action.provider_profile,
            )
            self.reserved[action.run_id] += action.estimated_cost_micro_usd
            self.reservations[action.action_id] = action.estimated_cost_micro_usd
            self.actions[action.action_id] = action
            self.decisions[action.action_id] = decision
            self.idempotency[action.idempotency_key] = (action, decision)
            return decision

    async def report_completion(self, report: CompletionReport) -> None:
        if report.actual_cost_micro_usd < 0:
            raise ValueError("actual_cost_micro_usd cannot be negative")
        async with self._lock:
            if report.action_id in self.completions:
                return
            action = self.actions[report.action_id]
            reservation = self.reservations.pop(report.action_id, 0)
            if reservation > self.reserved[action.run_id]:
                raise RuntimeError("reservation_invariant_violation")
            self.reserved[action.run_id] -= reservation
            self.spent[action.run_id] += report.actual_cost_micro_usd
            self.completions[report.action_id] = report
            if (
                self.spent[action.run_id]
                > self.runs[action.run_id].budget_cap_micro_usd
            ):
                self.completions[report.action_id] = replace(
                    report,
                    status="overrun",
                    error_code="budget_overrun",
                )
                self.runs[action.run_id] = Run(
                    action.run_id,
                    self.runs[action.run_id].budget_cap_micro_usd,
                    RunState.FAILED,
                    self.runs[action.run_id].metadata,
                )
                raise BudgetOverrun(
                    report.action_id,
                    self.spent[action.run_id],
                    self.runs[action.run_id].budget_cap_micro_usd,
                )

    async def poll_control(self, run_id: str) -> ControlCommand:
        command = self.controls[run_id]
        self.controls[run_id] = ControlCommand(ControlAction.CONTINUE)
        return command

    async def finish_run(self, run_id: str, state: RunState) -> None:
        run = self.runs[run_id]
        self.runs[run_id] = Run(
            run.run_id, run.budget_cap_micro_usd, state, run.metadata
        )
