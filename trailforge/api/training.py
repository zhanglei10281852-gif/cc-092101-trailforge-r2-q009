from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from trailforge.api.dependencies import get_session
from trailforge.domain.enums import PlanStatus, TrainingType
from trailforge.schemas.common import Page
from trailforge.schemas.training import (
    SessionCompleteRequest,
    TrainingPlanCreate,
    TrainingPlanFilter,
    TrainingPlanResponse,
    TrainingPlanUpdate,
    TrainingRecordResponse,
    TrainingSessionCreate,
    TrainingSessionResponse,
    TrainingStatistics,
)
from trailforge.services.training import TrainingService

router = APIRouter(prefix="/training", tags=["training"])
SessionDep = Annotated[Session, Depends(get_session)]


@router.post("/plans", response_model=TrainingPlanResponse, status_code=status.HTTP_201_CREATED)
def create_plan(
    data: TrainingPlanCreate,
    session: SessionDep,
    actor_id: int = Query(gt=0),
) -> TrainingPlanResponse:
    return TrainingService(session).create_plan(data, actor_id=actor_id)


@router.get("/plans", response_model=Page[TrainingPlanResponse])
def list_plans(
    session: SessionDep,
    user_id: int | None = Query(default=None, gt=0),
    plan_status: PlanStatus | None = Query(default=None, alias="status"),
    training_type: TrainingType | None = None,
    starts_after: datetime | None = None,
    ends_before: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    sort: str = "start_at",
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
) -> Page[TrainingPlanResponse]:
    filters = TrainingPlanFilter(
        user_id=user_id,
        status=plan_status,
        training_type=training_type,
        starts_after=starts_after,
        ends_before=ends_before,
        page=page,
        page_size=page_size,
        sort=sort,
        direction=direction,
    )
    return TrainingService(session).list_plans(filters)


@router.get("/plans/{plan_id}", response_model=TrainingPlanResponse)
def get_plan(plan_id: int, session: SessionDep) -> TrainingPlanResponse:
    return TrainingService(session).get_plan(plan_id)


@router.patch("/plans/{plan_id}", response_model=TrainingPlanResponse)
def update_plan(
    plan_id: int,
    data: TrainingPlanUpdate,
    session: SessionDep,
    actor_id: int = Query(gt=0),
) -> TrainingPlanResponse:
    return TrainingService(session).update_plan(plan_id, data, actor_id=actor_id)


@router.post("/plans/{plan_id}/status", response_model=TrainingPlanResponse)
def change_plan_status(
    plan_id: int,
    target_status: PlanStatus,
    session: SessionDep,
    actor_id: int = Query(gt=0),
    reason: str = "",
    expected_version: int | None = Query(default=None, ge=1),
) -> TrainingPlanResponse:
    return TrainingService(session).change_plan_status(
        plan_id,
        target_status,
        actor_id=actor_id,
        reason=reason,
        expected_version=expected_version,
    )


@router.post(
    "/sessions", response_model=TrainingSessionResponse, status_code=status.HTTP_201_CREATED
)
def schedule_session(
    data: TrainingSessionCreate,
    session: SessionDep,
    actor_id: int = Query(gt=0),
) -> TrainingSessionResponse:
    return TrainingService(session).schedule_session(data, actor_id=actor_id)


@router.post("/sessions/{session_id}/start", response_model=TrainingSessionResponse)
def start_session(
    session_id: int,
    session: SessionDep,
    actor_id: int = Query(gt=0),
    started_at: datetime | None = None,
) -> TrainingSessionResponse:
    return TrainingService(session).start_session(
        session_id, actor_id=actor_id, started_at=started_at
    )


@router.post("/sessions/{session_id}/complete", response_model=list[TrainingRecordResponse])
def complete_session(
    session_id: int,
    data: SessionCompleteRequest,
    session: SessionDep,
    actor_id: int = Query(gt=0),
) -> list[TrainingRecordResponse]:
    return TrainingService(session).complete_session(session_id, data, actor_id=actor_id)


@router.post("/sessions/{session_id}/skip", response_model=TrainingSessionResponse)
def skip_session(
    session_id: int,
    session: SessionDep,
    actor_id: int = Query(gt=0),
    reason: str = Query(min_length=1, max_length=1000),
) -> TrainingSessionResponse:
    return TrainingService(session).skip_session(session_id, actor_id=actor_id, reason=reason)


@router.get("/users/{user_id}/statistics", response_model=TrainingStatistics)
def training_statistics(
    user_id: int,
    session: SessionDep,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> TrainingStatistics:
    return TrainingService(session).statistics(user_id, start_at=start_at, end_at=end_at)
