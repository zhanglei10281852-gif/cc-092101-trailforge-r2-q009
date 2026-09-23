from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from trailforge.api.dependencies import get_session
from trailforge.repositories.audit import AuditRepository
from trailforge.schemas.activities import ActivityStatistics
from trailforge.schemas.audit import AuditFilter, AuditLogResponse, DashboardStatistics
from trailforge.schemas.common import Page
from trailforge.schemas.gear import GearStatistics
from trailforge.schemas.safety import RiskStatistics
from trailforge.services.statistics import StatisticsService

router = APIRouter(tags=["statistics", "audit"])
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/statistics/dashboard", response_model=DashboardStatistics)
def dashboard_statistics(
    session: SessionDep,
    now: datetime | None = None,
) -> DashboardStatistics:
    return StatisticsService(session).dashboard(now=now)


@router.get("/statistics/activities", response_model=ActivityStatistics)
def activity_statistics(
    session: SessionDep,
    organizer_id: int | None = Query(default=None, gt=0),
) -> ActivityStatistics:
    return StatisticsService(session).activities(organizer_id=organizer_id)


@router.get("/statistics/gear", response_model=GearStatistics)
def gear_statistics(
    session: SessionDep,
    now: datetime | None = None,
) -> GearStatistics:
    return StatisticsService(session).gear(now=now)


@router.get("/statistics/risks", response_model=RiskStatistics)
def risk_statistics(
    session: SessionDep,
    now: datetime | None = None,
) -> RiskStatistics:
    return StatisticsService(session).risks(now=now)


@router.get("/audit-logs", response_model=Page[AuditLogResponse])
def list_audit_logs(
    session: SessionDep,
    actor_id: int | None = Query(default=None, gt=0),
    entity_type: str | None = None,
    entity_id: int | None = Query(default=None, gt=0),
    occurred_after: datetime | None = None,
    occurred_before: datetime | None = None,
    correlation_id: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    sort: str = "occurred_at",
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
) -> Page[AuditLogResponse]:
    filters = AuditFilter(
        actor_id=actor_id,
        entity_type=entity_type,
        entity_id=entity_id,
        occurred_after=occurred_after,
        occurred_before=occurred_before,
        correlation_id=correlation_id,
        page=page,
        page_size=page_size,
        sort=sort,
        direction=direction,
    )
    result = AuditRepository(session).list_logs(filters)
    return Page[AuditLogResponse].build(
        [AuditLogResponse.model_validate(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )
