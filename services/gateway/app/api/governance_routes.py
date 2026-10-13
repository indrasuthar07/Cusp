"""Governed run, preflight, completion, and cooperative-control endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from app.auth.dependencies import AgentIdentity, get_current_agent
from app.config.settings import get_settings
from app.db import get_db
from app.db.models import (
    ActionAttempt,
    Decision,
    DecisionVerdict,
    PolicySet,
    PolicyVersion,
    Session,
    SessionStatus,
)
from app.governance.engine import (
    DecisionTokenSigner,
    GovernanceContext,
    PolicyResolver,
    ProgressSignal,
)
from app.state.budget import budget_engine
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/v1", tags=["governance"])


class RunCreateRequest(BaseModel):
    budget_cap_micro_usd: int = Field(ge=0)
    metadata: dict[str, str] = Field(default_factory=dict)


class RunResponse(BaseModel):
    run_id: uuid.UUID
    status: str
    budget_cap_micro_usd: int


class ProgressRequest(BaseModel):
    fingerprint: str = ""
    diff_bytes: int = Field(default=0, ge=0)
    tests_passed: int = Field(default=0, ge=0)
    tests_failed: int = Field(default=0, ge=0)
    elapsed_ms: int = Field(default=0, ge=0)


class PreflightRequest(BaseModel):
    run_id: uuid.UUID
    action_type: Literal["model_call", "tool_call", "test_run", "file_change", "context_read"]
    provider_profile: str = Field(min_length=1, max_length=128)
    estimated_cost_micro_usd: int = Field(ge=0)
    idempotency_key: str = Field(min_length=16, max_length=255)
    sequence: int = Field(ge=1)
    data_classification: Literal["public", "internal", "confidential", "restricted"] = "internal"
    summary: str = Field(default="", max_length=2000)
    progress: ProgressRequest = Field(default_factory=ProgressRequest)
    provider_error_code: str | None = None


class DecisionResponse(BaseModel):
    decision_id: uuid.UUID
    action_id: uuid.UUID
    verdict: str
    reason_code: str
    cost_provenance: str
    policy_version: int
    approved_profile: str | None
    loop_score: float | None
    expires_at: datetime
    decision_token: str


class CompletionRequest(BaseModel):
    action_id: uuid.UUID
    estimated_cost_micro_usd: int = Field(ge=0)
    actual_cost_micro_usd: int = Field(ge=0)
    status: Literal["completed", "failed"] = "completed"
    provider_profile: str
    decision_token: str


class ControlRequest(BaseModel):
    action: Literal["continue", "pause", "resume", "terminate"]
    reason: str = Field(default="", max_length=500)


class FinishRequest(BaseModel):
    state: Literal["completed", "terminated", "failed"]


class ControlResponse(BaseModel):
    action: str
    reason: str = ""


def _decision_response(decision: Decision, action: ActionAttempt) -> DecisionResponse:
    if not decision.decision_token or decision.expires_at is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Decision is not replayable")
    return DecisionResponse(
        decision_id=decision.public_id,
        action_id=action.public_id,
        verdict=decision.verdict.value,
        reason_code=decision.reason,
        cost_provenance=decision.cost_provenance,
        policy_version=decision.policy_version,
        approved_profile=decision.approved_profile,
        loop_score=decision.loop_score,
        expires_at=decision.expires_at,
        decision_token=decision.decision_token,
    )


def _signer() -> DecisionTokenSigner:
    return DecisionTokenSigner(get_settings().cusp_session_secret.get_secret_value())


async def _load_run(db: AsyncSession, agent: AgentIdentity, run_id: uuid.UUID) -> Session:
    result = await db.execute(
        select(Session).where(
            Session.public_id == run_id,
            Session.org_id == agent.org_id,
            Session.agent_id == agent.agent_id,
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


async def _load_policy(db: AsyncSession, org_id: int) -> PolicyVersion | None:
    result = await db.execute(
        select(PolicyVersion)
        .join(PolicySet)
        .where(
            PolicySet.org_id == org_id,
            PolicySet.is_active.is_(True),
            PolicyVersion.is_published.is_(True),
        )
        .order_by(PolicyVersion.version_number.desc())
    )
    return result.scalars().first()


@router.post("/runs", response_model=RunResponse, status_code=status.HTTP_201_CREATED)
async def create_run(
    body: RunCreateRequest,
    agent: AgentIdentity = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> RunResponse:
    run = Session(
        public_id=uuid.uuid4(),
        org_id=agent.org_id,
        agent_id=agent.agent_id,
        status=SessionStatus.running,
        budget_cap_micro_usd=body.budget_cap_micro_usd,
    )
    db.add(run)
    await db.flush()
    await db.commit()
    await budget_engine.initialize_run(str(run.public_id), str(agent.org_id), body.budget_cap_micro_usd)
    return RunResponse(run_id=run.public_id, status=run.status.value, budget_cap_micro_usd=run.budget_cap_micro_usd)


@router.post("/actions/preflight", response_model=DecisionResponse)
async def preflight(
    body: PreflightRequest,
    agent: AgentIdentity = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> DecisionResponse:
    run = await _load_run(db, agent, body.run_id)
    if run.status is not SessionStatus.running:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"run_{run.status.value}")
    policy = await _load_policy(db, agent.org_id)
    key_result = await db.execute(
        select(ActionAttempt).where(ActionAttempt.idempotency_key == body.idempotency_key)
    )
    key_owner = key_result.scalar_one_or_none()
    if key_owner is not None and key_owner.session_id != run.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="idempotency_key_conflict")
    existing_result = await db.execute(
        select(ActionAttempt, Decision)
        .join(Decision, Decision.action_id == ActionAttempt.id)
        .where(
            ActionAttempt.idempotency_key == body.idempotency_key,
            ActionAttempt.session_id == run.id,
        )
        .order_by(Decision.created_at.desc())
    )
    existing = existing_result.first()
    if existing is not None:
        existing_action, existing_decision = existing
        return _decision_response(existing_decision, existing_action)

    prior_result = await db.execute(
        select(ActionAttempt.progress)
        .where(ActionAttempt.session_id == run.id)
        .order_by(ActionAttempt.sequence_number.desc())
        .limit(20)
    )
    recent_progress = tuple(ProgressSignal(**item) for item in prior_result.scalars().all() if item)
    rules = policy.rules if policy is not None else {"default_action": "pause", "fail_safe": {"on_policy_resolution_failure": "pause"}}
    resolver = PolicyResolver(rules, policy_version=policy.version_number if policy else 1, signer=_signer())
    context = GovernanceContext(
        action_id=str(uuid.uuid4()),
        provider_profile=body.provider_profile,
        estimated_cost_micro_usd=body.estimated_cost_micro_usd,
        run_spent_micro_usd=0,
        run_reserved_micro_usd=0,
        data_classification=body.data_classification,
        hosted_egress_allowed=bool(agent.scopes.get("hosted_egress")),
        provider_error_code=body.provider_error_code,
        progress=ProgressSignal(**body.progress.model_dump()),
        recent_progress=recent_progress,
    )
    decision = resolver.resolve(context)
    action = ActionAttempt(
        public_id=uuid.UUID(context.action_id),
        session_id=run.id,
        sequence_number=body.sequence,
        action_type=body.action_type,
        idempotency_key=body.idempotency_key,
        provider_profile=body.provider_profile,
        estimated_cost_micro_usd=body.estimated_cost_micro_usd,
        progress=body.progress.model_dump(),
    )
    db.add(action)
    await db.flush()
    db.add(
        Decision(
            public_id=uuid.UUID(decision.decision_id),
            session_id=run.id,
            action_id=action.id,
            verdict=DecisionVerdict(decision.verdict),
            reason=decision.reason_code,
            cost_provenance=decision.cost_provenance,
            approved_profile=decision.approved_profile,
            policy_version=decision.policy_version,
            loop_score=decision.loop_score,
            expires_at=datetime.fromtimestamp(decision.expires_at, tz=UTC),
            decision_token=decision.decision_token,
            policy_version_id=policy.id if policy else None,
        )
    )
    if decision.verdict in {"pause", "escalate"}:
        run.status = SessionStatus.paused
    elif decision.verdict == "kill":
        run.status = SessionStatus.terminated
    if decision.verdict in {"allow", "downgrade"}:
        try:
            await budget_engine.reserve(
                str(run.public_id),
                str(agent.org_id),
                body.idempotency_key,
                body.estimated_cost_micro_usd,
                int(rules.get("budgets", {}).get("run_cap_micro_usd", run.budget_cap_micro_usd)),
                int(rules.get("budgets", {}).get("organization_cap_micro_usd", 2**63 - 1)),
                action_id=str(action.public_id),
            )
        except Exception:
            decision = resolver._decision(context, "pause", "budget_check_failed", None)
            stored_decision = await db.execute(select(Decision).where(Decision.action_id == action.id))
            decision_row = stored_decision.scalar_one()
            decision_row.verdict = DecisionVerdict.pause
            decision_row.reason = decision.reason_code
            decision_row.cost_provenance = decision.cost_provenance
            decision_row.approved_profile = decision.approved_profile
            decision_row.policy_version = decision.policy_version
            decision_row.loop_score = decision.loop_score
            decision_row.expires_at = datetime.fromtimestamp(decision.expires_at, tz=UTC)
            decision_row.decision_token = decision.decision_token
            run.status = SessionStatus.paused
    await db.commit()
    stored_decision = await db.execute(select(Decision).where(Decision.action_id == action.id))
    return _decision_response(stored_decision.scalar_one(), action)


@router.post("/actions/completion", status_code=status.HTTP_204_NO_CONTENT)
async def report_completion(
    body: CompletionRequest,
    agent: AgentIdentity = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> None:
    signer = _signer()
    try:
        signer.verify(body.decision_token, action_id=str(body.action_id))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid decision token") from exc
    result = await db.execute(
        select(ActionAttempt, Session, Decision)
        .join(Session, ActionAttempt.session_id == Session.id)
        .join(Decision, Decision.action_id == ActionAttempt.id)
        .where(
            ActionAttempt.public_id == body.action_id,
            Session.org_id == agent.org_id,
            Session.agent_id == agent.agent_id,
        )
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found")
    action, run, decision = row
    if decision.verdict not in {DecisionVerdict.allow, DecisionVerdict.downgrade}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Action was not authorized")
    release_result = await budget_engine.release(
        str(run.public_id), str(agent.org_id), body.estimated_cost_micro_usd, body.actual_cost_micro_usd, action_id=str(action.public_id)
    )
    if isinstance(release_result, (list, tuple)) and len(release_result) > 1 and str(release_result[1]) == "overrun":
        run.status = SessionStatus.failed
    await db.commit()


@router.get("/runs/{run_id}/control", response_model=ControlResponse)
async def poll_control(
    run_id: uuid.UUID,
    agent: AgentIdentity = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> ControlResponse:
    run = await _load_run(db, agent, run_id)
    control = await budget_engine.poll_control(str(run.public_id))
    return ControlResponse(**control)


@router.post("/runs/{run_id}/control", response_model=ControlResponse)
async def set_control(
    run_id: uuid.UUID,
    body: ControlRequest,
    agent: AgentIdentity = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> ControlResponse:
    run = await _load_run(db, agent, run_id)
    if body.action == "pause":
        run.status = SessionStatus.paused
    elif body.action == "resume":
        if run.status is not SessionStatus.paused:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run_is_not_paused")
        run.status = SessionStatus.running
    elif body.action == "terminate":
        run.status = SessionStatus.terminated
    await db.commit()
    await budget_engine.set_control(str(run.public_id), body.action, body.reason)
    return ControlResponse(action=body.action, reason=body.reason)


@router.post("/runs/{run_id}/finish", status_code=status.HTTP_204_NO_CONTENT)
async def finish_run(
    run_id: uuid.UUID,
    body: FinishRequest,
    agent: AgentIdentity = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> None:
    run = await _load_run(db, agent, run_id)
    run.status = SessionStatus(body.state)
    await db.commit()