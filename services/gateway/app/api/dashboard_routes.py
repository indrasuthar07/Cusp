"""Tenant-scoped read models used by the dashboard."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, cast

from app.auth.dependencies import UserIdentity, get_current_user, require_role
from app.db import get_db
from app.db.models import (
    ActionAttempt,
    AuditEvent,
    Decision,
    MembershipRole,
    ModelProfile,
    PolicySet,
    PolicyVersion,
    Session,
    SessionStatus,
    UsageRecord,
)
from app.db.redis import get_redis_client
from app.events.schemas import EVENTS_STREAM_NAME, redact_payload
from app.state.budget import budget_engine
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, select
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


class PolicyVersionView(BaseModel):
    version_id: uuid.UUID
    version_number: int
    rules: dict
    is_published: bool
    created_at: datetime


class PolicySetView(BaseModel):
    policy_set_id: uuid.UUID
    name: str
    schema_version: int
    is_active: bool
    versions: list[PolicyVersionView]


class PolicyListResponse(BaseModel):
    policies: list[PolicySetView]


class PolicyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    rules: dict


class PolicyVersionCreateRequest(BaseModel):
    rules: dict


class PolicyVersionResponse(BaseModel):
    policy_set_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    is_published: bool


class LiveEvent(BaseModel):
    event_id: str
    stream_id: str
    event_type: str
    session_id: str | None
    payload: dict
    created_at: datetime | None = None


class LiveEventsResponse(BaseModel):
    events: list[LiveEvent]


class ProviderMetric(BaseModel):
    provider_kind: str
    model_profile: str
    actions: int
    virtual_cost_micro_usd: int
    provider_cost_micro_usd: int | None
    cost_provenance: str
    avg_latency_ms: float | None
    energy_wh: float | None
    quota_status: str
    health_status: str


class MetricsResponse(BaseModel):
    providers: list[ProviderMetric]
    total_virtual_cost_micro_usd: int
    total_provider_cost_micro_usd: int | None
    total_energy_wh: float | None


class ModelEvaluation(BaseModel):
    model_profile: str
    provider_kind: str
    samples: int
    average_latency_ms: float | None
    average_virtual_cost_micro_usd: float | None
    average_ev_score: float | None
    average_loop_score: float | None
    evaluation_available: bool


class ModelEvaluationResponse(BaseModel):
    models: list[ModelEvaluation]


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


def _policy_view(policy: PolicySet) -> PolicySetView:
    versions = sorted(policy.versions, key=lambda version: version.version_number, reverse=True)
    return PolicySetView(
        policy_set_id=policy.public_id,
        name=policy.name,
        schema_version=policy.schema_version,
        is_active=policy.is_active,
        versions=[
            PolicyVersionView(
                version_id=version.public_id,
                version_number=version.version_number,
                rules=redact_payload(version.rules),
                is_published=version.is_published,
                created_at=version.created_at,
            )
            for version in versions
        ],
    )


@router.get("/policies", response_model=PolicyListResponse)
async def list_policies(
    user: UserIdentity = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PolicyListResponse:
    result = await db.execute(
        select(PolicySet)
        .options(selectinload(PolicySet.versions))
        .where(PolicySet.org_id == user.org_id)
        .order_by(PolicySet.created_at.desc())
    )
    return PolicyListResponse(policies=[_policy_view(policy) for policy in result.scalars().all()])


def _validate_policy_rules(rules: dict) -> None:
    allowed_actions = {"allow", "downgrade", "pause", "escalate", "kill"}
    default_action = rules.get("default_action", "pause")
    if default_action not in allowed_actions:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="default_action is invalid")
    if "budgets" in rules and not isinstance(rules["budgets"], dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="budgets must be an object")
    if "fail_safe" in rules and not isinstance(rules["fail_safe"], dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="fail_safe must be an object")


@router.post("/policies", response_model=PolicyVersionResponse, status_code=status.HTTP_201_CREATED)
async def create_policy(
    body: PolicyCreateRequest,
    user: UserIdentity = Depends(require_role(MembershipRole.owner, MembershipRole.admin)),
    db: AsyncSession = Depends(get_db),
) -> PolicyVersionResponse:
    _validate_policy_rules(body.rules)
    policy = PolicySet(
        public_id=uuid.uuid4(), org_id=user.org_id, name=body.name, schema_version=1, is_active=True
    )
    db.add(policy)
    await db.flush()
    version = PolicyVersion(
        public_id=uuid.uuid4(), policy_set_id=policy.id, version_number=1,
        rules=redact_payload(body.rules), is_published=False,
    )
    db.add(version)
    await db.commit()
    return PolicyVersionResponse(
        policy_set_id=policy.public_id, version_id=version.public_id,
        version_number=version.version_number, is_published=version.is_published,
    )


async def _load_policy_set(db: AsyncSession, user: UserIdentity, policy_set_id: uuid.UUID) -> PolicySet:
    result = await db.execute(
        select(PolicySet)
        .options(selectinload(PolicySet.versions))
        .where(PolicySet.public_id == policy_set_id, PolicySet.org_id == user.org_id)
    )
    policy = result.scalar_one_or_none()
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy set not found")
    return policy


@router.post("/policies/{policy_set_id}/versions", response_model=PolicyVersionResponse, status_code=status.HTTP_201_CREATED)
async def create_policy_version(
    policy_set_id: uuid.UUID,
    body: PolicyVersionCreateRequest,
    user: UserIdentity = Depends(require_role(MembershipRole.owner, MembershipRole.admin)),
    db: AsyncSession = Depends(get_db),
) -> PolicyVersionResponse:
    _validate_policy_rules(body.rules)
    policy = await _load_policy_set(db, user, policy_set_id)
    next_number = max((version.version_number for version in policy.versions), default=0) + 1
    version = PolicyVersion(
        public_id=uuid.uuid4(), policy_set_id=policy.id, version_number=next_number,
        rules=redact_payload(body.rules), is_published=False,
    )
    db.add(version)
    await db.commit()
    return PolicyVersionResponse(
        policy_set_id=policy.public_id, version_id=version.public_id,
        version_number=version.version_number, is_published=False,
    )


@router.post("/policies/{policy_set_id}/versions/{version_number}/publish", response_model=PolicyVersionResponse)
async def publish_policy_version(
    policy_set_id: uuid.UUID,
    version_number: int,
    user: UserIdentity = Depends(require_role(MembershipRole.owner, MembershipRole.admin)),
    db: AsyncSession = Depends(get_db),
) -> PolicyVersionResponse:
    policy = await _load_policy_set(db, user, policy_set_id)
    version = next((item for item in policy.versions if item.version_number == version_number), None)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy version not found")
    for item in policy.versions:
        item.is_published = item.id == version.id
    await db.commit()
    return PolicyVersionResponse(
        policy_set_id=policy.public_id, version_id=version.public_id,
        version_number=version.version_number, is_published=True,
    )


def _decode_stream_value(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


@router.get("/events", response_model=LiveEventsResponse)
async def list_live_events(
    after: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    user: UserIdentity = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LiveEventsResponse:
    statement = (
        select(AuditEvent)
        .where(AuditEvent.org_id == user.org_id)
        .order_by(AuditEvent.created_at.asc())
        .limit(limit)
    )
    if after is not None:
        statement = statement.where(AuditEvent.created_at > after)
    result = await db.execute(statement)
    return LiveEventsResponse(events=[
        LiveEvent(
            event_id=event.event_id, stream_id=event.event_id, event_type=event.event_type,
            session_id=str(event.correlation_id), payload=redact_payload(event.payload), created_at=event.created_at,
        )
        for event in result.scalars().all()
    ])


@router.get("/metrics", response_model=MetricsResponse)
async def dashboard_metrics(
    user: UserIdentity = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MetricsResponse:
    """Return cost, latency, energy, and provider health for one organization."""
    usage_result = await db.execute(
        select(
            UsageRecord.provider_kind,
            UsageRecord.model_profile_name,
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.actual_virtual_cost_micro_usd), 0),
            func.sum(UsageRecord.provider_reported_cost_micro_usd),
            func.avg(UsageRecord.latency_ms),
            func.sum(UsageRecord.energy_wh),
        )
        .join(ActionAttempt, UsageRecord.action_id == ActionAttempt.id)
        .join(Session, ActionAttempt.session_id == Session.id)
        .where(Session.org_id == user.org_id)
        .group_by(UsageRecord.provider_kind, UsageRecord.model_profile_name)
    )
    profiles_result = await db.execute(
        select(ModelProfile).where(ModelProfile.org_id == user.org_id)
    )
    profile_status = {(profile.provider_kind.value, profile.name): profile.is_enabled for profile in profiles_result.scalars().all()}
    metrics: list[ProviderMetric] = []
    for provider, model, actions, virtual_cost, provider_cost, latency, energy in usage_result.all():
        provider_name = provider.value
        metrics.append(
            ProviderMetric(
                provider_kind=provider_name,
                model_profile=model,
                actions=int(actions),
                virtual_cost_micro_usd=int(virtual_cost or 0),
                provider_cost_micro_usd=int(provider_cost) if provider_cost is not None else None,
                cost_provenance="provider_reported" if provider_cost is not None else "virtual",
                avg_latency_ms=float(latency) if latency is not None else None,
                energy_wh=float(energy) if energy is not None else None,
                quota_status="unknown",
                health_status="healthy" if profile_status.get((provider_name, model), True) else "disabled",
            )
        )
    total_virtual = sum(metric.virtual_cost_micro_usd for metric in metrics)
    provider_costs = [metric.provider_cost_micro_usd for metric in metrics if metric.provider_cost_micro_usd is not None]
    energy_values = [metric.energy_wh for metric in metrics if metric.energy_wh is not None]
    return MetricsResponse(
        providers=metrics,
        total_virtual_cost_micro_usd=total_virtual,
        total_provider_cost_micro_usd=sum(provider_costs) if provider_costs else None,
        total_energy_wh=sum(energy_values) if energy_values else None,
    )


@router.get("/model-evaluations", response_model=ModelEvaluationResponse)
async def model_evaluations(
    user: UserIdentity = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ModelEvaluationResponse:
    """Compare persisted telemetry and governance signals without inventing scores."""
    usage_result = await db.execute(
        select(
            UsageRecord.provider_kind,
            UsageRecord.model_profile_name,
            func.count(UsageRecord.id),
            func.avg(UsageRecord.latency_ms),
            func.avg(UsageRecord.actual_virtual_cost_micro_usd),
        )
        .join(ActionAttempt, UsageRecord.action_id == ActionAttempt.id)
        .join(Session, ActionAttempt.session_id == Session.id)
        .where(Session.org_id == user.org_id)
        .group_by(UsageRecord.provider_kind, UsageRecord.model_profile_name)
    )
    decision_result = await db.execute(
        select(
            ActionAttempt.provider_profile,
            func.avg(Decision.ev_score),
            func.avg(Decision.loop_score),
        )
        .join(Decision, Decision.action_id == ActionAttempt.id)
        .join(Session, ActionAttempt.session_id == Session.id)
        .where(Session.org_id == user.org_id)
        .group_by(ActionAttempt.provider_profile)
    )
    decision_scores = {profile: (ev, loop) for profile, ev, loop in decision_result.all()}
    models: list[ModelEvaluation] = []
    for provider, model, samples, latency, cost in usage_result.all():
        ev_score, loop_score = decision_scores.get(model, (None, None))
        models.append(
            ModelEvaluation(
                model_profile=model,
                provider_kind=provider.value,
                samples=int(samples),
                average_latency_ms=float(latency) if latency is not None else None,
                average_virtual_cost_micro_usd=float(cost) if cost is not None else None,
                average_ev_score=float(ev_score) if ev_score is not None else None,
                average_loop_score=float(loop_score) if loop_score is not None else None,
                evaluation_available=ev_score is not None or loop_score is not None,
            )
        )
    return ModelEvaluationResponse(models=models)


@router.websocket("/live")
async def live_feed(websocket: WebSocket, user: UserIdentity = Depends(get_current_user)) -> None:
    """Stream redacted events for the authenticated organization only."""
    await websocket.accept()
    try:
        request = await websocket.receive_json()
        last_id = str(request.get("last_event_id") or "$")
        client = await get_redis_client()
        while True:
            entries = cast(Any, await client.xread({EVENTS_STREAM_NAME: last_id}, count=100, block=5000))
            for _stream, messages in entries:
                for stream_id, fields in messages:
                    last_id = _decode_stream_value(stream_id)
                    decoded = {_decode_stream_value(key): _decode_stream_value(value) for key, value in fields.items()}
                    if decoded.get("org_id") != str(user.org_id):
                        continue
                    raw_payload = decoded.get("payload", "{}")
                    try:
                        payload = json.loads(raw_payload)
                    except json.JSONDecodeError:
                        payload = {}
                    await websocket.send_json({
                        "event_id": decoded.get("event_id", last_id), "stream_id": last_id,
                        "event_type": decoded.get("event_type", "UNKNOWN"),
                        "session_id": decoded.get("session_id"), "payload": redact_payload(payload),
                    })
    except WebSocketDisconnect:
        return


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
