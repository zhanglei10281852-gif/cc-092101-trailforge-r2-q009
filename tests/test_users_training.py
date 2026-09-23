from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select

from tests.conftest import create_user
from trailforge.domain.enums import PlanStatus, SessionStatus
from trailforge.errors import ConflictError, InvalidStateError
from trailforge.models.audit import AuditLog
from trailforge.models.training import TrainingRecord, TrainingSession
from trailforge.schemas.training import (
    SessionCompleteRequest,
    TrainingExerciseCreate,
    TrainingPlanCreate,
    TrainingRecordCreate,
    TrainingSessionCreate,
)
from trailforge.schemas.users import (
    EmergencyContactCreate,
    HealthRestrictionCreate,
    UserCreate,
    UserFilter,
)
from trailforge.services.training import TrainingService
from trailforge.services.users import UserService

UTC = UTC


def test_user_create_normalizes_email_and_writes_audit(session) -> None:
    user = UserService(session).create(
        UserCreate(email="  HIKER@Example.COM ", display_name="  Ridge   Walker ")
    )
    assert user.email == "hiker@example.com"
    assert user.display_name == "Ridge Walker"
    log = session.scalar(select(AuditLog).where(AuditLog.entity_id == user.id))
    assert log is not None
    assert log.entity_type == "user"
    assert log.after_state["email"] == "hiker@example.com"


def test_duplicate_user_email_is_rejected(session) -> None:
    service = UserService(session)
    service.create(UserCreate(email="unique@example.com", display_name="First"))
    with pytest.raises(ConflictError, match="already registered"):
        service.create(UserCreate(email="UNIQUE@example.com", display_name="Second"))


def test_invalid_email_is_rejected_before_database() -> None:
    with pytest.raises(PydanticValidationError):
        UserCreate(email="not-an-email", display_name="Bad")


def test_profile_contacts_and_restrictions_round_trip(session) -> None:
    user_id = create_user(session)
    service = UserService(session)
    contact = service.add_contact(
        user_id,
        EmergencyContactCreate(
            name="Emergency Person",
            relationship_label="Friend",
            phone="13800000000",
            priority=1,
        ),
        actor_id=user_id,
    )
    restriction = service.add_restriction(
        user_id,
        HealthRestrictionCreate(
            name="Knee strain",
            severity=2,
            activity_guidance="Avoid steep descents when painful",
        ),
        actor_id=user_id,
    )
    bundle = service.get(user_id)
    assert bundle.sport_profile is not None
    assert bundle.sport_profile.fitness_level == "intermediate"
    assert bundle.emergency_contacts[0].id == contact.id
    assert bundle.health_restrictions[0].id == restriction.id


def test_contact_priority_must_be_unique_per_user(session) -> None:
    user_id = create_user(session)
    service = UserService(session)
    first = EmergencyContactCreate(
        name="One", relationship_label="Friend", phone="100001", priority=1
    )
    second = EmergencyContactCreate(
        name="Two", relationship_label="Friend", phone="100002", priority=1
    )
    service.add_contact(user_id, first, actor_id=user_id)
    with pytest.raises(ConflictError):
        service.add_contact(user_id, second, actor_id=user_id)


def test_user_list_paginates_and_sorts(session) -> None:
    for index in range(5):
        UserService(session).create(
            UserCreate(email=f"user{index}@example.com", display_name=f"User {index}")
        )
    page = UserService(session).list(
        UserFilter(page=2, page_size=2, sort="display_name", direction="asc")
    )
    assert page.meta.total == 5
    assert page.meta.pages == 3
    assert [item.display_name for item in page.items] == ["User 2", "User 3"]


def _create_plan(session, user_id: int):
    now = datetime.now(UTC)
    return TrainingService(session).create_plan(
        TrainingPlanCreate(
            user_id=user_id,
            name="Mountain Base",
            goal="Build capacity",
            start_at=now - timedelta(days=1),
            end_at=now + timedelta(days=30),
            target_sessions_per_week=3,
            exercises=[
                TrainingExerciseCreate(
                    sequence=1,
                    name="Loaded walk",
                    training_type="loaded_walk",
                    target_duration_minutes=60,
                    target_distance_km=5,
                    target_load_kg=10,
                    planned_rpe=6,
                )
            ],
        ),
        actor_id=user_id,
    )


def test_training_plan_requires_target_for_exercise() -> None:
    with pytest.raises(PydanticValidationError, match="measurable target"):
        TrainingExerciseCreate(
            sequence=1,
            name="Nothing",
            training_type="recovery",
            planned_rpe=2,
        )


def test_training_plan_state_machine_rejects_invalid_transition(session) -> None:
    user_id = create_user(session)
    plan = _create_plan(session, user_id)
    service = TrainingService(session)
    active = service.change_plan_status(
        plan.id, PlanStatus.ACTIVE, actor_id=user_id, expected_version=plan.version
    )
    assert active.status == PlanStatus.ACTIVE
    with pytest.raises(InvalidStateError):
        service.change_plan_status(plan.id, PlanStatus.DRAFT, actor_id=user_id)


def test_training_session_completion_calculates_load_and_side_effects(session) -> None:
    user_id = create_user(session)
    plan = _create_plan(session, user_id)
    service = TrainingService(session)
    plan = service.change_plan_status(plan.id, PlanStatus.ACTIVE, actor_id=user_id)
    now = datetime.now(UTC)
    planned = service.schedule_session(
        TrainingSessionCreate(
            plan_id=plan.id,
            title="Hill repeats",
            planned_start_at=now,
            planned_end_at=now + timedelta(hours=2),
        ),
        actor_id=user_id,
    )
    started = service.start_session(planned.id, actor_id=user_id, started_at=now)
    records = service.complete_session(
        started.id,
        SessionCompleteRequest(
            completed_at=now + timedelta(hours=1),
            expected_version=started.version,
            records=[
                TrainingRecordCreate(
                    exercise_id=plan.exercises[0].id,
                    duration_minutes=60,
                    distance_km=5,
                    load_kg=10,
                    perceived_exertion=7,
                    completion_percent=100,
                )
            ],
        ),
        actor_id=user_id,
    )
    assert records[0].training_load == 770
    stored_session = session.get(TrainingSession, started.id)
    assert stored_session.status == SessionStatus.COMPLETED
    assert session.scalar(select(func.count()).select_from(TrainingRecord)) == 1
    assert (
        session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.entity_type == "training_session")
        )
        >= 3
    )


def test_training_statistics_are_accurate(session) -> None:
    user_id = create_user(session)
    plan = _create_plan(session, user_id)
    service = TrainingService(session)
    plan = service.change_plan_status(plan.id, PlanStatus.ACTIVE, actor_id=user_id)
    now = datetime.now(UTC)
    planned = service.schedule_session(
        TrainingSessionCreate(
            plan_id=plan.id,
            title="Endurance",
            planned_start_at=now,
            planned_end_at=now + timedelta(hours=2),
        ),
        actor_id=user_id,
    )
    service.start_session(planned.id, actor_id=user_id, started_at=now)
    service.complete_session(
        planned.id,
        SessionCompleteRequest(
            completed_at=now + timedelta(hours=1),
            records=[
                TrainingRecordCreate(
                    exercise_id=plan.exercises[0].id,
                    duration_minutes=30,
                    distance_km=3,
                    perceived_exertion=5,
                    completion_percent=80,
                )
            ],
        ),
        actor_id=user_id,
    )
    stats = service.statistics(user_id)
    assert stats.planned_sessions == 1
    assert stats.completed_sessions == 1
    assert stats.completion_rate == 100
    assert stats.total_duration_minutes == 30
    assert stats.total_distance_km == 3
    assert stats.total_training_load == 300
