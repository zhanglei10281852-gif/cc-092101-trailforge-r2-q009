from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from trailforge.api.dependencies import get_session
from trailforge.domain.enums import ActivityStatus, RiskLevel
from trailforge.schemas.activities import (
    ActivityStateChange,
    ExpeditionCreate,
    ExpeditionFilter,
    ExpeditionResponse,
    ExpeditionRoster,
    ExpeditionUpdate,
    RegistrationCreate,
    RegistrationResponse,
    WithdrawalRequest,
)
from trailforge.schemas.common import Page
from trailforge.services.activities import ExpeditionService

router = APIRouter(prefix="/expeditions", tags=["expeditions"])
SessionDep = Annotated[Session, Depends(get_session)]


@router.post("", response_model=ExpeditionResponse, status_code=status.HTTP_201_CREATED)
def create_expedition(data: ExpeditionCreate, session: SessionDep) -> ExpeditionResponse:
    return ExpeditionService(session).create(data)


@router.get("", response_model=Page[ExpeditionResponse])
def list_expeditions(
    session: SessionDep,
    organizer_id: int | None = Query(default=None, gt=0),
    route_id: int | None = Query(default=None, gt=0),
    participant_id: int | None = Query(default=None, gt=0),
    expedition_status: ActivityStatus | None = Query(default=None, alias="status"),
    risk_level: RiskLevel | None = None,
    starts_after: datetime | None = None,
    starts_before: datetime | None = None,
    has_capacity: bool | None = None,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    sort: str = "start_at",
    direction: str = Query(default="asc", pattern="^(asc|desc)$"),
) -> Page[ExpeditionResponse]:
    filters = ExpeditionFilter(
        organizer_id=organizer_id,
        route_id=route_id,
        participant_id=participant_id,
        status=expedition_status,
        risk_level=risk_level,
        starts_after=starts_after,
        starts_before=starts_before,
        has_capacity=has_capacity,
        search=search,
        page=page,
        page_size=page_size,
        sort=sort,
        direction=direction,
    )
    return ExpeditionService(session).list(filters)


@router.get("/{expedition_id}", response_model=ExpeditionResponse)
def get_expedition(expedition_id: int, session: SessionDep) -> ExpeditionResponse:
    return ExpeditionService(session).get(expedition_id)


@router.patch("/{expedition_id}", response_model=ExpeditionResponse)
def update_expedition(
    expedition_id: int,
    data: ExpeditionUpdate,
    session: SessionDep,
    actor_id: int = Query(gt=0),
) -> ExpeditionResponse:
    return ExpeditionService(session).update(expedition_id, data, actor_id=actor_id)


@router.post("/{expedition_id}/status", response_model=ExpeditionResponse)
def change_expedition_status(
    expedition_id: int,
    data: ActivityStateChange,
    session: SessionDep,
) -> ExpeditionResponse:
    return ExpeditionService(session).change_status(expedition_id, data)


@router.post(
    "/{expedition_id}/registrations",
    response_model=RegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_member(
    expedition_id: int,
    data: RegistrationCreate,
    session: SessionDep,
) -> RegistrationResponse:
    return ExpeditionService(session).register(expedition_id, data)


@router.post("/{expedition_id}/withdrawals", response_model=RegistrationResponse)
def withdraw_member(
    expedition_id: int,
    data: WithdrawalRequest,
    session: SessionDep,
) -> RegistrationResponse:
    return ExpeditionService(session).withdraw(expedition_id, data)


@router.get("/{expedition_id}/roster", response_model=ExpeditionRoster)
def expedition_roster(expedition_id: int, session: SessionDep) -> ExpeditionRoster:
    return ExpeditionService(session).roster(expedition_id)
