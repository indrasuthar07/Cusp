"""Tenant-scoped read models used by the dashboard."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.auth.dependencies import UserIdentity, get_current_user, require_role
from app.db import get_db
from app.db.models import (
    ActionAttempt,
    AuditEvent,
    Decision,
    MembershipRole,
    ModelProfile,
    Session,
    SessionStatus,
)
from app.events.schemas import redact_payload
from app.state.budget import budget_engine
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


class DashboardRun(BaseModel):
    run_id: uuid.UUID
    status: str
    budget_cap_micro_usd: int
    agent_id: uuid.UUID
    agent_name: str
    started_at: datetime
    ended_at: datetime | None


class DashboardRunsResponse(BaseModel):
    runs: list[DashboardRun]
    count: int


class DashboardProviderProfile(BaseModel):
    profile_id: uuid.UUID
    name: str
    provider_kind: str
    model_name: str
    capabilities: dict
    cost_config: dict
    is_enabled: bool
    updated_at: datetime


class DashboardProviderProfilesResponse(BaseModel):
    profiles: list[DashboardProviderProfile]
    count: int


class ReplayDecision(BaseModel):
    verdict: str
    reason: str
    cost_provenance: str
    approved_profile: str | None
    loop_score: float | None
    created_at: datetime


class ReplayAction(BaseModel):
    action_id: uuid.UUID
    sequence: int
    action_type: str
    provider_profile: str
    progress: dict
    created_at: datetime
    decision: ReplayDecision | None


class ReplayAuditEvent(BaseModel):
    event_id: str
    event_type: str
    payload: dict
    created_at: datetime


class RunReplayResponse(BaseModel):
    run: DashboardRun
    actions: list[ReplayAction]
    audit_events: list[ReplayAuditEvent]


class InterventionRequest(BaseModel):
    action: str = Field(pattern="^(pause|resume|terminate|kill)$")
    reason: str = Field(default="", max_length=500)


class InterventionResponse(BaseModel):
    run_id: uuid.UUID
    action: str
    status: str
    reason: str


@router.get("/runs", response_model=DashboardRunsResponse)
async def list_runs(
    status_filter: SessionStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    user: UserIdentity = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DashboardRunsResponse:
    """List runs belonging only to the authenticated user's organization."""
    statement = (
        select(Session)
        .options(selectinload(Session.agent))
        .where(Session.org_id == user.org_id)
        .order_by(Session.started_at.desc())
        .limit(limit)
    )
    if status_filter is not None:
        statement = statement.where(Session.status == status_filter)
    result = await db.execute(statement)
    runs = [
        DashboardRun(
            run_id=run.public_id,
            status=run.status.value,
            budget_cap_micro_usd=run.budget_cap_micro_usd,
            agent_id=run.agent.public_id,
            agent_name=run.agent.name,
            started_at=run.started_at,
            ended_at=run.ended_at,
        )
        for run in result.scalars().all()
    ]
    return DashboardRunsResponse(runs=runs, count=len(runs))


@router.get("/provider-profiles", response_model=DashboardProviderProfilesResponse)
async def list_provider_profiles(
    user: UserIdentity = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DashboardProviderProfilesResponse:
    """List provider profiles owned by the authenticated user's organization."""
    result = await db.execute(
        select(ModelProfile)
        .where(ModelProfile.org_id == user.org_id)
        .order_by(ModelProfile.name.asc())
    )
    profiles = [
        DashboardProviderProfile(
            profile_id=profile.public_id,
            name=profile.name,
            provider_kind=profile.provider_kind.value,
            model_name=profile.model_name,
            capabilities=profile.capabilities,
            cost_config=profile.cost_config,
            is_enabled=profile.is_enabled,
            updated_at=profile.updated_at,
        )
        for profile in result.scalars().all()
    ]
    return DashboardProviderProfilesResponse(profiles=profiles, count=len(profiles))


async def _load_user_run(db: AsyncSession, user: UserIdentity, run_id: uuid.UUID) -> Session:
    result = await db.execute(
        select(Session)
        .options(selectinload(Session.agent))
        .where(Session.public_id == run_id, Session.org_id == user.org_id)
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


@router.get("/runs/{run_id}/replay", response_model=RunReplayResponse)
async def replay_run(
    run_id: uuid.UUID,
    user: UserIdentity = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RunReplayResponse:
    """Return a redacted, read-only replay for a run in the user's organization."""
    run = await _load_user_run(db, user, run_id)
    actions_result = await db.execute(
        select(ActionAttempt, Decision)
        .outerjoin(Decision, Decision.action_id == ActionAttempt.id)
        .where(ActionAttempt.session_id == run.id)
        .order_by(ActionAttempt.sequence_number.asc(), Decision.created_at.asc())
    )
    actions: list[ReplayAction] = []
    for action, decision in actions_result.all():
        actions.append(
            ReplayAction(
                action_id=action.public_id,
                sequence=action.sequence_number,
                action_type=action.action_type,
                provider_profile=action.provider_profile,
                progress=redact_payload(action.progress),
                created_at=action.created_at,
                decision=(
                    ReplayDecision(
                        verdict=decision.verdict.value,
                        reason=decision.reason,
                        cost_provenance=decision.cost_provenance,
                        approved_profile=decision.approved_profile,
                        loop_score=decision.loop_score,
                        created_at=decision.created_at,
                    )
                    if decision is not None
                    else None
                ),
            )
        )
    events_result = await db.execute(
        select(AuditEvent)
        .where(
            AuditEvent.org_id == user.org_id,
            AuditEvent.correlation_id == run.public_id,
        )
        .order_by(AuditEvent.created_at.asc())
    )
    audit_events = [
        ReplayAuditEvent(
            event_id=event.event_id,
            event_type=event.event_type,
            payload=redact_payload(event.payload),
            created_at=event.created_at,
        )
        for event in events_result.scalars().all()
    ]
    run_view = DashboardRun(
        run_id=run.public_id,
        status=run.status.value,
        budget_cap_micro_usd=run.budget_cap_micro_usd,
        agent_id=run.agent.public_id,
        agent_name=run.agent.name,
        started_at=run.started_at,
        ended_at=run.ended_at,
    )
    return RunReplayResponse(run=run_view, actions=actions, audit_events=audit_events)


@router.post(
    "/runs/{run_id}/intervention",
    response_model=InterventionResponse,
)
async def intervene_on_run(
    run_id: uuid.UUID,
    body: InterventionRequest,
    user: UserIdentity = Depends(require_role(MembershipRole.owner, MembershipRole.admin)),
    db: AsyncSession = Depends(get_db),
) -> InterventionResponse:
    """Apply an authenticated, organization-admin intervention to a run."""
    run = await _load_user_run(db, user, run_id)
    action = "terminate" if body.action == "kill" else body.action
    if action == "pause":
        if run.status is not SessionStatus.running:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run_is_not_running")
        run.status = SessionStatus.paused
    elif action == "resume":
        if run.status is not SessionStatus.paused:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run_is_not_paused")
        run.status = SessionStatus.running
    elif action == "terminate":
        if run.status in {SessionStatus.completed, SessionStatus.terminated, SessionStatus.failed}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run_is_finished")
        run.status = SessionStatus.terminated
    await db.commit()
    await budget_engine.set_control(str(run.public_id), body.action, body.reason)
    return InterventionResponse(
        run_id=run.public_id,
        action=body.action,
        status=run.status.value,
        reason=body.reason,
    )
