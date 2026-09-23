from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from trailforge.database.base import utc_now
from trailforge.domain.enums import (
    PLAN_TRANSITIONS,
    SESSION_TRANSITIONS,
    AuditAction,
    PlanStatus,
    SessionStatus,
)
from trailforge.errors import ConflictError, InvalidStateError, NotFoundError, ValidationError
from trailforge.models.training import (
    TrainingExercise,
    TrainingPlan,
    TrainingRecord,
    TrainingSession,
)
from trailforge.repositories.base import apply_version
from trailforge.repositories.training import TrainingRepository
from trailforge.repositories.users import UserRepository
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
from trailforge.services.base import ServiceBase


class TrainingService(ServiceBase):
    def __init__(self, session: Session) -> None:
        super().__init__(session)
        self.training = TrainingRepository(session)
        self.users = UserRepository(session)

    def create_plan(self, data: TrainingPlanCreate, *, actor_id: int) -> TrainingPlanResponse:
        self.users.require(data.user_id)
        plan_data = data.model_dump(exclude={"exercises"})
        plan = TrainingPlan(**plan_data)
        plan.exercises = [TrainingExercise(**item.model_dump()) for item in data.exercises]
        self.session.add(plan)
        self.session.flush()
        self.audit(
            actor_id=actor_id,
            entity_type="training_plan",
            entity_id=plan.id,
            action=AuditAction.CREATED,
            after=self.snapshot(plan),
            context={"exercise_count": len(plan.exercises)},
        )
        return TrainingPlanResponse.model_validate(plan)

    def get_plan(self, plan_id: int) -> TrainingPlanResponse:
        plan = self.training.get_plan_detail(plan_id)
        if plan is None:
            raise NotFoundError(f"TrainingPlan {plan_id} was not found")
        return TrainingPlanResponse.model_validate(plan)

    def list_plans(self, filters: TrainingPlanFilter) -> Page[TrainingPlanResponse]:
        result = self.training.list_plans(filters)
        return Page[TrainingPlanResponse].build(
            [TrainingPlanResponse.model_validate(item) for item in result.items],
            page=result.page,
            page_size=result.page_size,
            total=result.total,
        )

    def update_plan(
        self, plan_id: int, data: TrainingPlanUpdate, *, actor_id: int
    ) -> TrainingPlanResponse:
        plan = self.training.get_plan_detail(plan_id, for_update=True)
        if plan is None:
            raise NotFoundError(f"TrainingPlan {plan_id} was not found")
        if PlanStatus(plan.status) not in {PlanStatus.DRAFT, PlanStatus.PAUSED}:
            raise InvalidStateError("only draft or paused plans can be edited")
        apply_version(plan, data.expected_version)
        before = self.snapshot(plan)
        changes = data.model_dump(exclude_unset=True, exclude={"expected_version"})
        start_at = changes.get("start_at", plan.start_at)
        end_at = changes.get("end_at", plan.end_at)
        if end_at <= start_at:
            raise ValidationError("end_at must be later than start_at")
        for field, value in changes.items():
            setattr(plan, field, value)
        self.session.flush()
        self.audit(
            actor_id=actor_id,
            entity_type="training_plan",
            entity_id=plan.id,
            action=AuditAction.UPDATED,
            before=before,
            after=self.snapshot(plan),
        )
        return TrainingPlanResponse.model_validate(plan)

    def change_plan_status(
        self,
        plan_id: int,
        target_status: PlanStatus,
        *,
        actor_id: int,
        reason: str = "",
        expected_version: int | None = None,
    ) -> TrainingPlanResponse:
        plan = self.training.get_plan_detail(plan_id, for_update=True)
        if plan is None:
            raise NotFoundError(f"TrainingPlan {plan_id} was not found")
        current = PlanStatus(plan.status)
        if target_status not in PLAN_TRANSITIONS[current]:
            raise InvalidStateError(
                f"cannot change training plan from {current} to {target_status}",
                context={"allowed": sorted(PLAN_TRANSITIONS[current])},
            )
        if target_status == PlanStatus.ACTIVE and not plan.exercises:
            raise ValidationError("a training plan requires exercises before activation")
        apply_version(plan, expected_version)
        before = {"status": current.value, "version": plan.version - 1}
        plan.status = target_status
        self.session.flush()
        self.audit(
            actor_id=actor_id,
            entity_type="training_plan",
            entity_id=plan.id,
            action=AuditAction.STATUS_CHANGED,
            before=before,
            after={"status": target_status.value, "version": plan.version},
            context={"reason": reason},
        )
        return TrainingPlanResponse.model_validate(plan)

    def schedule_session(
        self, data: TrainingSessionCreate, *, actor_id: int
    ) -> TrainingSessionResponse:
        plan = self.training.get_plan_detail(data.plan_id)
        if plan is None:
            raise NotFoundError(f"TrainingPlan {data.plan_id} was not found")
        if PlanStatus(plan.status) not in {PlanStatus.ACTIVE, PlanStatus.PAUSED}:
            raise InvalidStateError("sessions can only be scheduled for active or paused plans")
        if data.planned_start_at < plan.start_at or data.planned_end_at > plan.end_at:
            raise ValidationError("session must fit within plan dates")
        existing = self.training.list_sessions(
            user_id=plan.user_id,
            start_at=data.planned_start_at,
            end_at=data.planned_end_at,
        )
        for item in existing:
            overlaps = (
                item.planned_start_at < data.planned_end_at
                and item.planned_end_at > data.planned_start_at
                and item.status not in {SessionStatus.CANCELLED, SessionStatus.SKIPPED}
            )
            if overlaps:
                raise ConflictError("training session overlaps an existing session")
        session = TrainingSession(user_id=plan.user_id, **data.model_dump())
        self.session.add(session)
        self.session.flush()
        self.audit(
            actor_id=actor_id,
            entity_type="training_session",
            entity_id=session.id,
            action=AuditAction.CREATED,
            after=self.snapshot(session),
        )
        return TrainingSessionResponse.model_validate(session)

    def start_session(
        self, session_id: int, *, actor_id: int, started_at: datetime | None = None
    ) -> TrainingSessionResponse:
        session = self.training.get_session(session_id, for_update=True)
        if session is None:
            raise NotFoundError(f"TrainingSession {session_id} was not found")
        self._change_session_status(session, SessionStatus.IN_PROGRESS, actor_id)
        session.actual_start_at = started_at or utc_now()
        self.session.flush()
        return TrainingSessionResponse.model_validate(session)

    def complete_session(
        self, session_id: int, data: SessionCompleteRequest, *, actor_id: int
    ) -> list[TrainingRecordResponse]:
        session = self.training.get_session(session_id, for_update=True)
        if session is None:
            raise NotFoundError(f"TrainingSession {session_id} was not found")
        if SessionStatus(session.status) != SessionStatus.IN_PROGRESS:
            raise InvalidStateError("only an in-progress session can be completed")
        apply_version(session, data.expected_version)
        if session.actual_start_at is None or data.completed_at < session.actual_start_at:
            raise ValidationError("completed_at cannot be earlier than actual_start_at")
        if session.records:
            raise ConflictError("session already has training records")
        records: list[TrainingRecord] = []
        for item in data.records:
            exercise = self.training.get_exercise(item.exercise_id)
            if exercise is None or exercise.plan_id != session.plan_id:
                raise ValidationError(
                    "record exercise does not belong to the session plan",
                    context={"exercise_id": item.exercise_id},
                )
            load = self.calculate_load(
                duration_minutes=item.duration_minutes,
                distance_km=item.distance_km,
                load_kg=item.load_kg,
                repetitions=item.repetitions,
                sets=item.sets,
                rpe=item.perceived_exertion,
            )
            record = TrainingRecord(
                session_id=session.id,
                training_load=load,
                **item.model_dump(),
            )
            self.session.add(record)
            records.append(record)
        session.actual_end_at = data.completed_at
        session.status = SessionStatus.COMPLETED
        self.session.flush()
        self.audit(
            actor_id=actor_id,
            entity_type="training_session",
            entity_id=session.id,
            action=AuditAction.STATUS_CHANGED,
            before={"status": SessionStatus.IN_PROGRESS.value},
            after={"status": SessionStatus.COMPLETED.value},
            context={
                "record_count": len(records),
                "total_load": round(sum(item.training_load for item in records), 2),
            },
        )
        return [TrainingRecordResponse.model_validate(item) for item in records]

    def skip_session(
        self, session_id: int, *, actor_id: int, reason: str
    ) -> TrainingSessionResponse:
        session = self.training.get_session(session_id, for_update=True)
        if session is None:
            raise NotFoundError(f"TrainingSession {session_id} was not found")
        self._change_session_status(session, SessionStatus.SKIPPED, actor_id, reason)
        session.notes = f"{session.notes}\nSkipped: {reason}".strip()
        return TrainingSessionResponse.model_validate(session)

    def statistics(
        self,
        user_id: int,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> TrainingStatistics:
        self.users.require(user_id)
        sessions = self.training.list_sessions(user_id=user_id, start_at=start_at, end_at=end_at)
        records = self.training.records_for_user(user_id, start_at, end_at)
        completed = sum(item.status == SessionStatus.COMPLETED for item in sessions)
        skipped = sum(item.status == SessionStatus.SKIPPED for item in sessions)
        loads_by_type: dict[str, float] = {}
        for record, exercise in records:
            key = str(exercise.training_type)
            loads_by_type[key] = round(loads_by_type.get(key, 0) + record.training_load, 2)
        rpes = [record.perceived_exertion for record, _ in records]
        return TrainingStatistics(
            user_id=user_id,
            period_start=start_at,
            period_end=end_at,
            planned_sessions=len(sessions),
            completed_sessions=completed,
            skipped_sessions=skipped,
            completion_rate=round(completed / len(sessions) * 100, 2) if sessions else 0,
            total_duration_minutes=sum(record.duration_minutes for record, _ in records),
            total_distance_km=round(sum(record.distance_km for record, _ in records), 2),
            total_training_load=round(sum(record.training_load for record, _ in records), 2),
            average_rpe=round(sum(rpes) / len(rpes), 2) if rpes else 0,
            load_by_type=loads_by_type,
        )

    @staticmethod
    def calculate_load(
        *,
        duration_minutes: int,
        distance_km: float,
        load_kg: float,
        repetitions: int,
        sets: int,
        rpe: int,
    ) -> float:
        duration_component = duration_minutes * rpe
        distance_component = distance_km * 10 * rpe
        strength_component = load_kg * repetitions * max(sets, 1) / 100
        return round(duration_component + distance_component + strength_component, 2)

    def _change_session_status(
        self,
        session: TrainingSession,
        target: SessionStatus,
        actor_id: int,
        reason: str = "",
    ) -> None:
        current = SessionStatus(session.status)
        if target not in SESSION_TRANSITIONS[current]:
            raise InvalidStateError(f"cannot change training session from {current} to {target}")
        apply_version(session, None)
        session.status = target
        self.session.flush()
        self.audit(
            actor_id=actor_id,
            entity_type="training_session",
            entity_id=session.id,
            action=AuditAction.STATUS_CHANGED,
            before={"status": current.value},
            after={"status": target.value},
            context={"reason": reason},
        )
