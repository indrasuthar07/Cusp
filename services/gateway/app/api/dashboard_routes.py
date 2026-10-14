"""Tenant-scoped read models used by the dashboard."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.auth.dependencies import UserIdentity, get_current_user
from app.db import get_db
from app.db.models import ModelProfile, Session, SessionStatus
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
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
