from __future__ import annotations

from datetime import UTC

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select

from tests.conftest import create_expedition, create_route, create_user
from trailforge.domain.enums import ActivityStatus, RegistrationStatus
from trailforge.errors import ConflictError, IdempotencyConflictError, InvalidStateError
from trailforge.models.activities import ExpeditionRegistration
from trailforge.models.audit import AuditLog, IdempotencyRecord
from trailforge.schemas.activities import ActivityStateChange, RegistrationCreate, WithdrawalRequest
from trailforge.schemas.routes import RouteFilter, RouteSegmentCreate, TrailRouteCreate
from trailforge.services.activities import ExpeditionService
from trailforge.services.routes import RouteService

UTC = UTC


def test_route_segment_distances_must_match_total() -> None:
    with pytest.raises(PydanticValidationError, match="segment distances"):
        TrailRouteCreate(
            name="Mismatch",
            region="Test",
            distance_km=20,
            elevation_gain_m=100,
            estimated_duration_minutes=100,
            difficulty="easy",
            segments=[
                RouteSegmentCreate(
                    sequence=1,
                    name="Short",
                    distance_km=2,
                    estimated_duration_minutes=30,
                    difficulty="easy",
                    start_latitude=0,
                    start_longitude=0,
                    end_latitude=0.1,
                    end_longitude=0.1,
                )
            ],
        )


def test_route_search_combines_filters(session) -> None:
    user_id = create_user(session)
    create_route(session, actor_id=user_id, name="Alpine Ridge")
    create_route(session, actor_id=user_id, name="Forest Ridge")
    result = RouteService(session).list_routes(
        RouteFilter(
            search="Alpine",
            region="Test",
            difficulty="moderate",
            max_distance_km=10,
            is_loop=True,
            is_published=True,
        )
    )
    assert result.meta.total == 1
    assert result.items[0].name == "Alpine Ridge"


def test_route_readiness_warns_about_missing_supply_on_long_route(session) -> None:
    user_id = create_user(session)
    route = RouteService(session).create_route(
        TrailRouteCreate(
            name="Long Traverse",
            region="Test",
            distance_km=20,
            elevation_gain_m=900,
            estimated_duration_minutes=480,
            difficulty="hard",
            segments=[
                RouteSegmentCreate(
                    sequence=1,
                    name="Traverse",
                    distance_km=20,
                    elevation_gain_m=900,
                    estimated_duration_minutes=480,
                    difficulty="hard",
                    start_latitude=30,
                    start_longitude=120,
                    end_latitude=31,
                    end_longitude=121,
                )
            ],
        ),
        actor_id=user_id,
    )
    readiness = RouteService(session).route_readiness(route.id)
    assert readiness["is_publishable"] is False
    assert "long route has no documented supply point" in readiness["warnings"]
    assert "difficult route has no risk tags" in readiness["warnings"]


def test_expedition_requires_published_route(session) -> None:
    organizer_id = create_user(session)
    route_id = create_route(session, actor_id=organizer_id, published=False)
    with pytest.raises(Exception, match="published route"):
        create_expedition(session, organizer_id=organizer_id, route_id=route_id)


def _open_expedition(session, capacity: int = 3, offset_days: int = 10) -> tuple[int, int]:
    organizer = create_user(session)
    route = create_route(session, actor_id=organizer)
    expedition = create_expedition(
        session,
        organizer_id=organizer,
        route_id=route,
        capacity=capacity,
        offset_days=offset_days,
    )
    ExpeditionService(session).change_status(
        expedition,
        ActivityStateChange(target_status="open", actor_id=organizer),
    )
    return organizer, expedition


def test_expedition_creator_is_confirmed_leader(session) -> None:
    organizer, expedition_id = _open_expedition(session)
    roster = ExpeditionService(session).roster(expedition_id)
    assert roster.confirmed_count == 1
    assert roster.members[0].user_id == organizer
    assert roster.members[0].role == "leader"


def test_registration_capacity_creates_waitlist(session) -> None:
    _, expedition_id = _open_expedition(session, capacity=2)
    second = create_user(session, email="second@example.com", name="Second")
    third = create_user(session, email="third@example.com", name="Third")
    service = ExpeditionService(session)
    confirmed = service.register(
        expedition_id,
        RegistrationCreate(user_id=second, idempotency_key="register-second"),
    )
    waiting = service.register(
        expedition_id,
        RegistrationCreate(user_id=third, idempotency_key="register-third"),
    )
    assert confirmed.status == RegistrationStatus.CONFIRMED
    assert waiting.status == RegistrationStatus.WAITLISTED
    assert service.roster(expedition_id).available_places == 0


def test_registration_idempotency_returns_same_resource(session) -> None:
    _, expedition_id = _open_expedition(session)
    user_id = create_user(session, email="idem@example.com", name="Idempotent")
    service = ExpeditionService(session)
    request = RegistrationCreate(user_id=user_id, idempotency_key="same-register-key")
    first = service.register(expedition_id, request)
    second = service.register(expedition_id, request)
    assert first.id == second.id
    assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1
    assert (
        session.scalar(
            select(func.count())
            .select_from(ExpeditionRegistration)
            .where(ExpeditionRegistration.user_id == user_id)
        )
        == 1
    )


def test_registration_idempotency_rejects_changed_payload(session) -> None:
    _, expedition_id = _open_expedition(session)
    user_id = create_user(session, email="changed@example.com", name="Changed")
    service = ExpeditionService(session)
    service.register(
        expedition_id,
        RegistrationCreate(user_id=user_id, role="member", idempotency_key="changed-key"),
    )
    with pytest.raises(IdempotencyConflictError):
        service.register(
            expedition_id,
            RegistrationCreate(user_id=user_id, role="medic", idempotency_key="changed-key"),
        )


def test_registration_time_conflict_is_rejected(session) -> None:
    organizer, first_id = _open_expedition(session, offset_days=10)
    second_route = create_route(session, actor_id=organizer, name="Other Route")
    second_id = create_expedition(
        session,
        organizer_id=organizer,
        route_id=second_route,
        offset_days=10,
    )
    service = ExpeditionService(session)
    service.change_status(
        second_id,
        ActivityStateChange(target_status="open", actor_id=organizer),
    )
    participant = create_user(session, email="busy@example.com", name="Busy")
    service.register(
        first_id,
        RegistrationCreate(user_id=participant, idempotency_key="first-event-key"),
    )
    with pytest.raises(ConflictError, match="another expedition"):
        service.register(
            second_id,
            RegistrationCreate(user_id=participant, idempotency_key="second-event-key"),
        )


def test_withdrawal_promotes_oldest_waitlisted_member(session) -> None:
    _, expedition_id = _open_expedition(session, capacity=2)
    confirmed_user = create_user(session, email="confirmed@example.com", name="Confirmed")
    waiting_user = create_user(session, email="waiting@example.com", name="Waiting")
    service = ExpeditionService(session)
    service.register(
        expedition_id,
        RegistrationCreate(user_id=confirmed_user, idempotency_key="confirmed-key"),
    )
    service.register(
        expedition_id,
        RegistrationCreate(user_id=waiting_user, idempotency_key="waiting-key"),
    )
    service.withdraw(
        expedition_id,
        WithdrawalRequest(
            user_id=confirmed_user,
            reason="Schedule changed",
            idempotency_key="withdraw-confirmed",
        ),
    )
    promoted = service.expeditions.get_registration(expedition_id, waiting_user)
    assert promoted.status == RegistrationStatus.CONFIRMED
    audit = session.scalar(
        select(AuditLog).where(
            AuditLog.entity_type == "expedition_registration",
            AuditLog.action == "withdrawn",
        )
    )
    assert audit.context["reason"] == "Schedule changed"


def test_illegal_activity_transition_is_rejected(session) -> None:
    organizer, expedition_id = _open_expedition(session)
    service = ExpeditionService(session)
    with pytest.raises(InvalidStateError):
        service.change_status(
            expedition_id,
            ActivityStateChange(target_status=ActivityStatus.COMPLETED, actor_id=organizer),
        )
