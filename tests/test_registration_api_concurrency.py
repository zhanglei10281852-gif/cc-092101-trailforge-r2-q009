from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from tests.conftest import create_expedition, create_route, create_user
from trailforge.database.migrations import initialize_database
from trailforge.domain.enums import RegistrationStatus
from trailforge.models.activities import ExpeditionRegistration
from trailforge.models.audit import AuditLog, IdempotencyRecord
from trailforge.schemas.activities import ActivityStateChange
from trailforge.services.activities import ExpeditionService

_seed_counter = 0


def _seed_expedition(client: TestClient, *, capacity: int, offset_days: int):
    global _seed_counter
    _seed_counter += 1
    seed = _seed_counter
    database = client.app.state.database
    with database.session() as session:
        organizer = create_user(
            session,
            email=f"api-organizer-{seed}@example.com",
            name=f"API Organizer {seed}",
        )
        route_id = create_route(session, actor_id=organizer, name=f"API Route {seed}")
        expedition_id = create_expedition(
            session,
            organizer_id=organizer,
            route_id=route_id,
            capacity=capacity,
            offset_days=offset_days,
        )
        ExpeditionService(session).change_status(
            expedition_id,
            ActivityStateChange(target_status="open", actor_id=organizer),
        )
    return organizer, expedition_id


def _seed_user(client: TestClient, email: str, name: str) -> int:
    with client.app.state.database.session() as session:
        return create_user(session, email=email, name=name)


class _ApiFlushGate:
    """Hold the first HTTP request that flushes a registration row.

    Registered on the Session class so it applies to the per-request sessions
    the API dependency opens, giving competing connections a deterministic
    contention window.
    """

    def __init__(self, hold_seconds: float = 0.3) -> None:
        self.armed = True
        self.entered = threading.Event()
        self._hold_seconds = hold_seconds

    def __enter__(self) -> _ApiFlushGate:
        gate = self

        @event.listens_for(Session, "after_flush")
        def _on_flush(session, _context) -> None:
            if not gate.armed:
                return
            if any(isinstance(obj, ExpeditionRegistration) for obj in session.new):
                gate.armed = False
                gate.entered.set()
                threading.Event().wait(gate._hold_seconds)

        self._listener = _on_flush
        return self

    def __exit__(self, *exc) -> None:
        event.remove(Session, "after_flush", self._listener)


def _post_registration(client: TestClient, expedition_id: int, user_id: int, key: str):
    return client.post(
        f"/api/v1/expeditions/{expedition_id}/registrations",
        json={"user_id": user_id, "idempotency_key": key},
    )


def test_api_last_seat_race_returns_one_confirmed_one_waitlisted(client: TestClient) -> None:
    _, expedition_id = _seed_expedition(client, capacity=2, offset_days=10)
    first = _seed_user(client, "api-race-first@example.com", "API First")
    second = _seed_user(client, "api-race-second@example.com", "API Second")

    responses: list = [None, None]
    with _ApiFlushGate() as gate:

        def worker(index: int, user_id: int, key: str) -> None:
            responses[index] = _post_registration(client, expedition_id, user_id, key)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(worker, 0, first, "api-seat-first-key"),
                pool.submit(worker, 1, second, "api-seat-second-key"),
            ]
            assert gate.entered.wait(timeout=10)
            for future in futures:
                future.result(timeout=30)

    assert all(response.status_code == 201 for response in responses)
    statuses = sorted(response.json()["status"] for response in responses)
    assert statuses == ["confirmed", "waitlisted"]

    roster = client.get(f"/api/v1/expeditions/{expedition_id}/roster")
    assert roster.status_code == 200
    body = roster.json()
    assert body["confirmed_count"] == 2
    assert body["waitlisted_count"] == 1
    assert body["available_places"] == 0


def test_api_same_idempotency_key_race_converges(client: TestClient) -> None:
    _, expedition_id = _seed_expedition(client, capacity=5, offset_days=20)
    participant = _seed_user(client, "api-race-idem@example.com", "API Idem")
    key = "api-shared-idem-key"

    responses: list = [None, None]
    with _ApiFlushGate() as gate:

        def worker(index: int) -> None:
            responses[index] = _post_registration(client, expedition_id, participant, key)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, index) for index in range(2)]
            assert gate.entered.wait(timeout=10)
            for future in futures:
                future.result(timeout=30)

    assert all(response.status_code == 201 for response in responses)
    ids = {response.json()["id"] for response in responses}
    assert len(ids) == 1

    with client.app.state.database.session() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(IdempotencyRecord.idempotency_key == key)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ExpeditionRegistration)
                .where(ExpeditionRegistration.user_id == participant)
            )
            == 1
        )


def test_api_schedule_conflict_race_returns_business_conflict(client: TestClient) -> None:
    _, first_expedition = _seed_expedition(client, capacity=5, offset_days=30)
    _, second_expedition = _seed_expedition(client, capacity=5, offset_days=30)
    participant = _seed_user(client, "api-race-busy@example.com", "API Busy")

    responses: list = [None, None]
    with _ApiFlushGate() as gate:

        def worker(index: int, expedition_id: int, key: str) -> None:
            responses[index] = _post_registration(client, expedition_id, participant, key)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(worker, 0, first_expedition, "api-overlap-key-one"),
                pool.submit(worker, 1, second_expedition, "api-overlap-key-two"),
            ]
            assert gate.entered.wait(timeout=10)
            for future in futures:
                future.result(timeout=30)

    status_codes = sorted(response.status_code for response in responses)
    assert status_codes == [201, 409]
    failed = next(response for response in responses if response.status_code == 409)
    detail = failed.json()["detail"]
    assert detail["code"] == "conflict"
    assert "another expedition" in detail["message"]

    with client.app.state.database.session() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ExpeditionRegistration)
                .where(ExpeditionRegistration.user_id == participant)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.entity_type == "expedition_registration",
                    AuditLog.actor_id == participant,
                    AuditLog.action == "registered",
                )
            )
            == 1
        )


def test_api_state_persists_across_database_reopen(client: TestClient) -> None:
    database = client.app.state.database
    _, expedition_id = _seed_expedition(client, capacity=2, offset_days=40)
    winner = _seed_user(client, "api-reopen-winner@example.com", "Reopen Winner")
    loser = _seed_user(client, "api-reopen-loser@example.com", "Reopen Loser")

    responses: list = [None, None]
    with _ApiFlushGate() as gate:

        def worker(index: int, user_id: int, key: str) -> None:
            responses[index] = _post_registration(client, expedition_id, user_id, key)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(worker, 0, winner, "api-reopen-winner-key"),
                pool.submit(worker, 1, loser, "api-reopen-loser-key"),
            ]
            assert gate.entered.wait(timeout=10)
            for future in futures:
                future.result(timeout=30)

    assert {response.json()["status"] for response in responses} == {
        RegistrationStatus.CONFIRMED,
        RegistrationStatus.WAITLISTED,
    }
    expected_status = {
        response.json()["user_id"]: response.json()["status"] for response in responses
    }
    database.engine.dispose()

    # A second engine over the same file must observe the durable queue and
    # audit state without WAL/lock artefacts.
    from trailforge.database.session import Database as DatabaseClass

    reopened = DatabaseClass(database.settings)
    initialize_database(reopened)
    try:
        with reopened.session() as session:
            roster = ExpeditionService(session).roster(expedition_id)
            assert roster.confirmed_count == 2
            assert roster.waitlisted_count == 1
            by_user = {member.user_id: str(member.status) for member in roster.members}
            assert by_user[winner] == expected_status[winner]
            assert by_user[loser] == expected_status[loser]
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(IdempotencyRecord)
                    .where(
                        IdempotencyRecord.idempotency_key.in_(
                            ["api-reopen-winner-key", "api-reopen-loser-key"]
                        )
                    )
                )
                == 2
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.entity_type == "expedition_registration",
                        AuditLog.action == "registered",
                    )
                )
                == 2
            )
    finally:
        reopened.engine.dispose()
