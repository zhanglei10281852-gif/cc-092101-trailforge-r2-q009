from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.conftest import create_expedition, create_route, create_user
from trailforge.config import Settings
from trailforge.database.migrations import initialize_database
from trailforge.database.session import Database
from trailforge.domain.enums import RegistrationStatus
from trailforge.errors import ConflictError, DatabaseBusyError, IdempotencyConflictError
from trailforge.main import create_app
from trailforge.models.activities import ExpeditionRegistration
from trailforge.models.audit import AuditLog, IdempotencyRecord
from trailforge.repositories.activities import ExpeditionRepository
from trailforge.schemas.activities import (
    ActivityStateChange,
    RegistrationCreate,
    WithdrawalRequest,
)
from trailforge.services.activities import ExpeditionService
from trailforge.services.base import ServiceBase

RACE_WINDOW_SECONDS = 0.25


@pytest.fixture
def slow_capacity_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Widen the check-then-write window so concurrent transactions overlap."""
    original = ExpeditionRepository.confirmed_count

    def slowed(self: ExpeditionRepository, expedition_id: int) -> int:
        value = original(self, expedition_id)
        time.sleep(RACE_WINDOW_SECONDS)
        return value

    monkeypatch.setattr(ExpeditionRepository, "confirmed_count", slowed)


@pytest.fixture
def slow_conflict_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Widen the itinerary-conflict check so concurrent transactions overlap."""
    original = ExpeditionRepository.conflicting_registration

    def slowed(self: ExpeditionRepository, *args: Any, **kwargs: Any) -> Any:
        value = original(self, *args, **kwargs)
        time.sleep(RACE_WINDOW_SECONDS)
        return value

    monkeypatch.setattr(ExpeditionRepository, "conflicting_registration", slowed)


def _run_concurrently(*tasks: Callable[[], Any]) -> list[tuple[str, Any]]:
    """Release all tasks at the same instant; collect ("ok"/"error", value)."""
    barrier = threading.Barrier(len(tasks))
    results: list[tuple[str, Any] | None] = [None] * len(tasks)

    def run(index: int, task: Callable[[], Any]) -> None:
        barrier.wait(timeout=10)
        try:
            results[index] = ("ok", task())
        except Exception as exc:  # noqa: BLE001 - outcomes are asserted by callers
            results[index] = ("error", exc)

    threads = [
        threading.Thread(target=run, args=(index, task), daemon=True)
        for index, task in enumerate(tasks)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert all(not thread.is_alive() for thread in threads), "a concurrent task hung"
    assert all(result is not None for result in results)
    return [result for result in results if result is not None]


def _seed_open_expedition(
    database: Database,
    *,
    capacity: int,
    extra_users: int,
    prefix: str,
    offset_days: int = 10,
) -> tuple[int, int, list[int]]:
    """Create and commit an open expedition plus extra users with profiles."""
    with database.session() as setup:
        organizer = create_user(
            setup, email=f"{prefix}-organizer@example.com", name=f"{prefix} organizer"
        )
        route_id = create_route(setup, actor_id=organizer, name=f"{prefix} route")
        expedition_id = create_expedition(
            setup,
            organizer_id=organizer,
            route_id=route_id,
            capacity=capacity,
            offset_days=offset_days,
        )
        ExpeditionService(setup).change_status(
            expedition_id,
            ActivityStateChange(target_status="open", actor_id=organizer),
        )
        users = [
            create_user(
                setup, email=f"{prefix}-user-{index}@example.com", name=f"{prefix} user {index}"
            )
            for index in range(extra_users)
        ]
    return organizer, expedition_id, users


def _register(
    database: Database, expedition_id: int, user_id: int, key: str, **overrides: Any
) -> Any:
    with database.session() as session:
        return ExpeditionService(session).register(
            expedition_id,
            RegistrationCreate(user_id=user_id, idempotency_key=key, **overrides),
        )


def _withdraw(database: Database, expedition_id: int, user_id: int, key: str) -> Any:
    with database.session() as session:
        return ExpeditionService(session).withdraw(
            expedition_id,
            WithdrawalRequest(
                user_id=user_id, reason="plans changed", idempotency_key=key
            ),
        )


def _roster(database: Database, expedition_id: int) -> Any:
    with database.session() as session:
        return ExpeditionService(session).roster(expedition_id)


def _count(database: Database, model: type, *conditions: Any) -> int:
    with database.session() as session:
        statement = select(func.count()).select_from(model)
        if conditions:
            statement = statement.where(*conditions)
        return int(session.scalar(statement) or 0)


def _errors(results: list[tuple[str, Any]]) -> list[Exception]:
    return [value for kind, value in results if kind == "error"]


def test_concurrent_registration_for_last_spot_stays_within_capacity(
    database: Database, slow_capacity_check: None
) -> None:
    _, expedition_id, (user_a, user_b) = _seed_open_expedition(
        database, capacity=2, extra_users=2, prefix="race"
    )

    results = _run_concurrently(
        lambda: _register(database, expedition_id, user_a, "race-slot-key-a"),
        lambda: _register(database, expedition_id, user_b, "race-slot-key-b"),
    )

    assert [kind for kind, _ in results] == ["ok", "ok"]
    statuses = {value.status for _, value in results}
    assert statuses == {RegistrationStatus.CONFIRMED, RegistrationStatus.WAITLISTED}

    roster = _roster(database, expedition_id)
    assert roster.capacity == 2
    assert roster.confirmed_count == 2
    assert roster.waitlisted_count == 1
    assert _count(database, IdempotencyRecord) == 2
    assert _count(database, AuditLog, AuditLog.action == "registered") == 2


def test_concurrent_same_user_overlapping_expeditions_are_mutually_exclusive(
    database: Database, slow_conflict_check: None
) -> None:
    with database.session() as setup:
        organizer = create_user(setup, email="overlap-organizer@example.com", name="Organizer")
        route_one = create_route(setup, actor_id=organizer, name="Overlap One")
        route_two = create_route(setup, actor_id=organizer, name="Overlap Two")
        expedition_one = create_expedition(
            setup, organizer_id=organizer, route_id=route_one, offset_days=10
        )
        expedition_two = create_expedition(
            setup, organizer_id=organizer, route_id=route_two, offset_days=10
        )
        service = ExpeditionService(setup)
        for expedition_id in (expedition_one, expedition_two):
            service.change_status(
                expedition_id,
                ActivityStateChange(target_status="open", actor_id=organizer),
            )
        user_id = create_user(setup, email="overlap-user@example.com", name="Busy User")

    results = _run_concurrently(
        lambda: _register(database, expedition_one, user_id, "overlap-first-key"),
        lambda: _register(database, expedition_two, user_id, "overlap-second-key"),
    )

    assert sorted(kind for kind, _ in results) == ["error", "ok"]
    (error,) = _errors(results)
    assert isinstance(error, ConflictError)
    assert not isinstance(error, DatabaseBusyError)
    assert "another expedition" in str(error)

    with database.session() as session:
        active = session.scalars(
            select(ExpeditionRegistration).where(
                ExpeditionRegistration.user_id == user_id,
                ExpeditionRegistration.status.in_(
                    [RegistrationStatus.CONFIRMED, RegistrationStatus.WAITLISTED]
                ),
            )
        ).all()
        assert len(active) == 1
    assert _count(database, IdempotencyRecord) == 1
    assert _count(database, AuditLog, AuditLog.action == "registered") == 1


def test_concurrent_duplicate_registration_is_a_clean_business_conflict(
    database: Database, slow_capacity_check: None
) -> None:
    _, expedition_id, (user_id,) = _seed_open_expedition(
        database, capacity=3, extra_users=1, prefix="dup"
    )

    results = _run_concurrently(
        lambda: _register(database, expedition_id, user_id, "dup-first-key"),
        lambda: _register(database, expedition_id, user_id, "dup-second-key"),
    )

    assert sorted(kind for kind, _ in results) == ["error", "ok"]
    (error,) = _errors(results)
    assert isinstance(error, ConflictError)
    assert "already registered" in str(error)

    assert (
        _count(database, ExpeditionRegistration, ExpeditionRegistration.user_id == user_id)
        == 1
    )
    assert _count(database, IdempotencyRecord) == 1
    assert _count(database, AuditLog, AuditLog.action == "registered") == 1


def test_concurrent_same_idempotency_key_converges_to_one_registration(
    database: Database, slow_capacity_check: None
) -> None:
    _, expedition_id, (user_id,) = _seed_open_expedition(
        database, capacity=3, extra_users=1, prefix="idem"
    )

    results = _run_concurrently(
        lambda: _register(database, expedition_id, user_id, "shared-idem-key"),
        lambda: _register(database, expedition_id, user_id, "shared-idem-key"),
    )

    assert [kind for kind, _ in results] == ["ok", "ok"]
    first, second = (value for _, value in results)
    assert first.id == second.id
    assert first.status == second.status == RegistrationStatus.CONFIRMED

    assert (
        _count(database, ExpeditionRegistration, ExpeditionRegistration.user_id == user_id)
        == 1
    )
    assert _count(database, IdempotencyRecord) == 1
    assert _count(database, AuditLog, AuditLog.action == "registered") == 1


def test_concurrent_same_key_with_different_payload_conflicts(
    database: Database, slow_capacity_check: None
) -> None:
    _, expedition_id, (user_id,) = _seed_open_expedition(
        database, capacity=3, extra_users=1, prefix="payload"
    )

    results = _run_concurrently(
        lambda: _register(database, expedition_id, user_id, "reused-key-01", role="member"),
        lambda: _register(
            database, expedition_id, user_id, "reused-key-01", role="medic", notes="changed"
        ),
    )

    assert sorted(kind for kind, _ in results) == ["error", "ok"]
    (error,) = _errors(results)
    assert isinstance(error, IdempotencyConflictError)

    assert (
        _count(database, ExpeditionRegistration, ExpeditionRegistration.user_id == user_id)
        == 1
    )
    assert _count(database, IdempotencyRecord) == 1
    assert _count(database, AuditLog, AuditLog.action == "registered") == 1


def test_concurrent_waitlist_order_and_promotion_after_withdrawal(
    database: Database, slow_capacity_check: None
) -> None:
    organizer, expedition_id, users = _seed_open_expedition(
        database, capacity=2, extra_users=4, prefix="wait"
    )

    results = _run_concurrently(
        *[
            (lambda user=user, index=index: _register(
                database, expedition_id, user, f"waitlist-key-{index}"
            ))
            for index, user in enumerate(users)
        ]
    )
    assert [kind for kind, _ in results] == ["ok"] * 4

    with database.session() as session:
        service = ExpeditionService(session)
        roster = service.roster(expedition_id)
        assert roster.capacity == 2
        assert roster.confirmed_count == 2
        assert roster.waitlisted_count == 3
        confirmed = [
            member
            for member in roster.members
            if member.status == RegistrationStatus.CONFIRMED
        ]
        waitlisted = [
            member
            for member in roster.members
            if member.status == RegistrationStatus.WAITLISTED
        ]
        assert organizer in {member.user_id for member in confirmed}
        confirmed_member = next(
            member for member in confirmed if member.user_id != organizer
        )
        service.withdraw(
            expedition_id,
            WithdrawalRequest(
                user_id=confirmed_member.user_id,
                reason="schedule",
                idempotency_key="withdraw-key-1",
            ),
        )
        roster_after = service.roster(expedition_id)
        assert roster_after.confirmed_count == 2
        assert roster_after.waitlisted_count == 2
        confirmed_after = {
            member.user_id
            for member in roster_after.members
            if member.status == RegistrationStatus.CONFIRMED
        }
        # the oldest waitlisted entry was promoted exactly once
        assert confirmed_after == {organizer, waitlisted[0].user_id}
        assert confirmed_member.user_id not in {
            member.user_id for member in roster_after.members
        }


def test_concurrent_withdrawal_and_registration_keep_roster_consistent(
    database: Database, slow_capacity_check: None
) -> None:
    organizer, expedition_id, (member, waiting, newcomer) = _seed_open_expedition(
        database, capacity=2, extra_users=3, prefix="mix"
    )
    with database.session() as setup:
        service = ExpeditionService(setup)
        service.register(
            expedition_id, RegistrationCreate(user_id=member, idempotency_key="mix-member-key")
        )
        service.register(
            expedition_id, RegistrationCreate(user_id=waiting, idempotency_key="mix-waiting-key")
        )

    results = _run_concurrently(
        lambda: _withdraw(database, expedition_id, member, "mix-withdraw-key"),
        lambda: _register(database, expedition_id, newcomer, "mix-newcomer-key"),
    )
    assert [kind for kind, _ in results] == ["ok", "ok"]

    roster = _roster(database, expedition_id)
    assert roster.confirmed_count == 2
    confirmed_ids = {
        entry.user_id
        for entry in roster.members
        if entry.status == RegistrationStatus.CONFIRMED
    }
    # the waitlisted member was promoted exactly once, never exceeding capacity
    assert confirmed_ids == {organizer, waiting}
    (newcomer_entry,) = [
        entry for entry in roster.members if entry.user_id == newcomer
    ]
    assert newcomer_entry.status == RegistrationStatus.WAITLISTED


def test_failed_registration_leaves_no_registration_idempotency_or_audit(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, expedition_id, (user_id,) = _seed_open_expedition(
        database, capacity=3, extra_users=1, prefix="frag"
    )

    def broken_audit(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("audit sink exploded")

    monkeypatch.setattr(ServiceBase, "audit", broken_audit)
    with pytest.raises(RuntimeError, match="audit sink"), database.session() as session:
        ExpeditionService(session).register(
            expedition_id,
            RegistrationCreate(user_id=user_id, idempotency_key="fragment-check"),
        )

    assert (
        _count(database, ExpeditionRegistration, ExpeditionRegistration.user_id == user_id)
        == 0
    )
    assert _count(database, IdempotencyRecord) == 0
    assert _count(database, AuditLog, AuditLog.action == "registered") == 0


def test_write_lock_timeout_raises_database_busy_not_a_business_error(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'busy.db'}",
        sqlite_timeout_seconds=1,
        sqlite_busy_retries=1,
        sqlite_busy_backoff_seconds=0.01,
    )
    database = Database(settings)
    initialize_database(database)
    _, expedition_id, (user_id,) = _seed_open_expedition(
        database, capacity=3, extra_users=1, prefix="busy"
    )

    lock = database.engine.connect()
    lock.exec_driver_sql("BEGIN IMMEDIATE")
    try:
        with pytest.raises(DatabaseBusyError), database.session() as session:
            ExpeditionService(session).register(
                expedition_id,
                RegistrationCreate(user_id=user_id, idempotency_key="busy-timeout-key"),
            )
    finally:
        lock.rollback()
        lock.close()

    assert (
        _count(database, ExpeditionRegistration, ExpeditionRegistration.user_id == user_id)
        == 0
    )
    assert _count(database, IdempotencyRecord) == 0
    assert _count(database, AuditLog, AuditLog.action == "registered") == 0
    database.engine.dispose()


def test_run_write_retries_busy_errors_but_not_business_errors(database: Database) -> None:
    attempts = 0

    def busy_then_ok(session: Any) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise DatabaseBusyError("simulated lock contention")
        return "recovered"

    assert database.run_write(busy_then_ok) == "recovered"
    assert attempts == 2

    calls = 0

    def business_failure(session: Any) -> None:
        nonlocal calls
        calls += 1
        raise ConflictError("capacity is a business decision")

    with pytest.raises(ConflictError):
        database.run_write(business_failure)
    assert calls == 1


def test_api_concurrent_registrations_never_exceed_capacity(
    client: TestClient, slow_capacity_check: None
) -> None:
    database = client.app.state.database
    _, expedition_id, users = _seed_open_expedition(
        database, capacity=3, extra_users=6, prefix="api"
    )

    results = _run_concurrently(
        *[
            (lambda user=user, index=index: client.post(
                f"/api/v1/expeditions/{expedition_id}/registrations",
                json={"user_id": user, "idempotency_key": f"api-race-key-{index}"},
            ))
            for index, user in enumerate(users)
        ]
    )
    assert [kind for kind, _ in results] == ["ok"] * 6
    responses = [value for _, value in results]
    assert all(response.status_code == 201 for response in responses)
    statuses = [response.json()["status"] for response in responses]
    assert statuses.count("confirmed") == 2
    assert statuses.count("waitlisted") == 4

    roster = client.get(f"/api/v1/expeditions/{expedition_id}/roster").json()
    assert roster["capacity"] == 3
    assert roster["confirmed_count"] == 3
    assert roster["waitlisted_count"] == 4
    assert _count(database, IdempotencyRecord) == 6
    assert _count(database, AuditLog, AuditLog.action == "registered") == 6


def test_api_concurrent_same_idempotency_key_returns_same_registration(
    client: TestClient, slow_capacity_check: None
) -> None:
    database = client.app.state.database
    _, expedition_id, (user_id,) = _seed_open_expedition(
        database, capacity=2, extra_users=1, prefix="apiidem"
    )
    payload = {"user_id": user_id, "idempotency_key": "api-shared-key"}

    results = _run_concurrently(
        lambda: client.post(f"/api/v1/expeditions/{expedition_id}/registrations", json=payload),
        lambda: client.post(f"/api/v1/expeditions/{expedition_id}/registrations", json=payload),
    )
    assert [kind for kind, _ in results] == ["ok", "ok"]
    responses = [value for _, value in results]
    assert all(response.status_code == 201 for response in responses)
    bodies = [response.json() for response in responses]
    assert bodies[0]["id"] == bodies[1]["id"]

    assert (
        _count(database, ExpeditionRegistration, ExpeditionRegistration.user_id == user_id)
        == 1
    )
    assert _count(database, IdempotencyRecord) == 1
    assert _count(database, AuditLog, AuditLog.action == "registered") == 1


def test_api_busy_database_returns_503_not_a_business_error(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'api-busy.db'}",
        sqlite_timeout_seconds=1,
        sqlite_busy_retries=1,
        sqlite_busy_backoff_seconds=0.01,
    )
    app = create_app(settings)
    with TestClient(app) as busy_client:
        database = app.state.database
        _, expedition_id, (user_id,) = _seed_open_expedition(
            database, capacity=3, extra_users=1, prefix="apibusy"
        )
        lock = database.engine.connect()
        lock.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            response = busy_client.post(
                f"/api/v1/expeditions/{expedition_id}/registrations",
                json={"user_id": user_id, "idempotency_key": "api-busy-key-1"},
            )
        finally:
            lock.rollback()
            lock.close()
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "database_busy"


def test_capacity_waitlist_and_audit_survive_database_reopen(
    settings: Settings, slow_capacity_check: None
) -> None:
    database = Database(settings)
    initialize_database(database)
    _, expedition_id, users = _seed_open_expedition(
        database, capacity=3, extra_users=5, prefix="reopen"
    )

    results = _run_concurrently(
        *[
            (lambda user=user, index=index: _register(
                database, expedition_id, user, f"reopen-key-{index}"
            ))
            for index, user in enumerate(users)
        ]
    )
    assert [kind for kind, _ in results] == ["ok"] * 5

    with database.session() as session:
        committed_order = list(
            session.scalars(
                select(ExpeditionRegistration.user_id)
                .where(ExpeditionRegistration.expedition_id == expedition_id)
                .order_by(ExpeditionRegistration.registered_at, ExpeditionRegistration.id)
            )
        )
    database.engine.dispose()

    reopened = Database(settings)
    assert initialize_database(reopened) == []
    with reopened.session() as session:
        roster = ExpeditionService(session).roster(expedition_id)
        assert roster.capacity == 3
        assert roster.confirmed_count == 3
        assert roster.waitlisted_count == 3
        assert [member.user_id for member in roster.members] == committed_order

        records = session.scalars(select(IdempotencyRecord)).all()
        assert len(records) == 5
        registration_ids = set(session.scalars(select(ExpeditionRegistration.id)).all())
        assert all(record.resource_id in registration_ids for record in records)

        audits = session.scalars(
            select(AuditLog).where(AuditLog.action == "registered")
        ).all()
        assert len(audits) == 5
        assert {audit.correlation_id for audit in audits} == {
            f"reopen-key-{index}" for index in range(5)
        }
    reopened.engine.dispose()
