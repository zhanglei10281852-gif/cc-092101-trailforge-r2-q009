from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import selectinload

from trailforge.models.training import (
    TrainingExercise,
    TrainingPlan,
    TrainingRecord,
    TrainingSession,
)
from trailforge.repositories.base import BaseRepository, PageResult
from trailforge.schemas.training import TrainingPlanFilter


class TrainingRepository(BaseRepository[TrainingPlan]):
    model = TrainingPlan
    sortable = {
        "created_at": TrainingPlan.created_at,
        "start_at": TrainingPlan.start_at,
        "end_at": TrainingPlan.end_at,
        "name": TrainingPlan.name,
        "status": TrainingPlan.status,
    }

    def get_plan_detail(self, plan_id: int, *, for_update: bool = False) -> TrainingPlan | None:
        statement = (
            select(TrainingPlan)
            .options(selectinload(TrainingPlan.exercises), selectinload(TrainingPlan.sessions))
            .where(TrainingPlan.id == plan_id)
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def list_plans(self, filters: TrainingPlanFilter) -> PageResult[TrainingPlan]:
        statement: Select = select(TrainingPlan).options(selectinload(TrainingPlan.exercises))
        if filters.user_id is not None:
            statement = statement.where(TrainingPlan.user_id == filters.user_id)
        if filters.status is not None:
            statement = statement.where(TrainingPlan.status == filters.status)
        if filters.training_type is not None:
            statement = statement.join(TrainingExercise).where(
                TrainingExercise.training_type == filters.training_type
            )
        if filters.starts_after is not None:
            statement = statement.where(TrainingPlan.start_at >= filters.starts_after)
        if filters.ends_before is not None:
            statement = statement.where(TrainingPlan.end_at <= filters.ends_before)
        return self.paginate(
            statement.distinct(),
            page=filters.page,
            page_size=filters.page_size,
            sort=filters.sort,
            direction=filters.direction,
        )

    def get_exercise(self, exercise_id: int) -> TrainingExercise | None:
        return self.session.get(TrainingExercise, exercise_id)

    def get_session(self, session_id: int, *, for_update: bool = False) -> TrainingSession | None:
        statement = (
            select(TrainingSession)
            .options(selectinload(TrainingSession.records))
            .where(TrainingSession.id == session_id)
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def list_sessions(
        self,
        *,
        user_id: int,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[TrainingSession]:
        statement = select(TrainingSession).where(TrainingSession.user_id == user_id)
        if start_at is not None:
            statement = statement.where(TrainingSession.planned_start_at >= start_at)
        if end_at is not None:
            statement = statement.where(TrainingSession.planned_start_at <= end_at)
        return list(self.session.scalars(statement.order_by(TrainingSession.planned_start_at)))

    def records_for_user(
        self,
        user_id: int,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> list[tuple[TrainingRecord, TrainingExercise]]:
        statement = (
            select(TrainingRecord, TrainingExercise)
            .join(TrainingSession, TrainingSession.id == TrainingRecord.session_id)
            .join(TrainingExercise, TrainingExercise.id == TrainingRecord.exercise_id)
            .where(TrainingSession.user_id == user_id)
        )
        if start_at is not None:
            statement = statement.where(TrainingSession.planned_start_at >= start_at)
        if end_at is not None:
            statement = statement.where(TrainingSession.planned_start_at <= end_at)
        return list(self.session.execute(statement).tuples())
