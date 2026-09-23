from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event, func, select, text

from tests.conftest import create_expedition, create_route, create_user
from trailforge.config import Settings
from trailforge.database.migrations import initialize_database
from trailforge.database.session import Database
from trailforge.domain.enums import RegistrationStatus
from trailforge.errors import (
    ConflictError,
    DatabaseBusyError,
    DuplicateRegistrationError,
    IdempotencyConflictError,
)
from trailforge.models.activities import Expedition, ExpeditionRegistration
from trailforge.models.audit import AuditLog, IdempotencyRecord
from trailforge.schemas.activities import (
    ActivityStateChange,
    RegistrationCreate,
    WithdrawalRequest,
)
from trailforge.services.activities import ExpeditionService


class _FlushGate:
    """Hold the first connection that flushes a registration row.

    With ``BEGIN IMMEDIATE`` every competing request blocks on SQLite's writer
    lock while the holder keeps its transaction open, which deterministically
    creates the contention window instead of relying on thread timing.
    """

    def __init__(self, *, hold_seconds: float = 0.3) -> None:
        self.armed = True
        self.entered = threading.Event()
        self._hold_seconds = hold_seconds

    def arm(self, session) -> None:
        gate = self

        @event.listens_for(session, "after_flush")
        def _on_flush(flush_session, _context) -> None:
            if not gate.armed:
                return
            if any(
                isinstance(obj, ExpeditionRegistration) for obj in flush_session.new
            ):
                gate.armed = False
                gate.entered.set()
                threading.Event().wait(gate._hold_seconds)


_open_counter = 0


def _open_expedition(database: Database, *, capacity: int, offset_days: int) -> tuple[int, int]:
    global _open_counter
    _open_counter += 1
    seed = _open_counter
    with database.session() as session:
        organizer = create_user(
            session,
            email=f"organizer-{seed}@example.com",
            name=f"Organizer {seed}",
        )
        route_id = create_route(session, actor_id=organizer, name=f"Route {seed}")
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


def _run_concurrent(
    database: Database, operations: Sequence[Callable]
) -> list[tuple[str, object]]:
    """Run each operation on its own connection/thread, serialized by SQLite."""
    results: list[tuple[str, object]] = [None] * len(operations)  # type: ignore[list-item]
    gate = _FlushGate()

    def worker(index: int) -> None:
        with database.session() as session:
            gate.arm(session)
            try:
                results[index] = ("ok", operations[index](session))
            except Exception as exc:  # noqa: BLE001 - asserted by callers
                results[index] = (
                    "error",
                    {
                        "type": type(exc).__name__,
                        "code": getattr(exc, "code", None),
                        "message": str(exc),
                    },
                )

    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        futures = [pool.submit(worker, index) for index in range(len(operations))]
        assert gate.entered.wait(timeout=10), "no registration reached the flush phase"
        for future in futures:
            future.result(timeout=30)
    return results


def _make_user(database: Database, email: str, name: str) -> int:
    with database.session() as session:
        return create_user(session, email=email, name=name)


def _roster(database: Database, expedition_id: int):
    with database.session() as session:
        return ExpeditionService(session).roster(expedition_id)


def test_last_seat_race_confirms_exactly_one_more_member(database: Database) -> None:
    _, expedition_id = _open_expedition(database, capacity=2, offset_days=10)
    first = _make_user(database, "race-first@example.com", "Race First")
    second = _make_user(database, "race-second@example.com", "Race Second")

    results = _run_concurrent(
        database,
        [
            lambda session: str(
                ExpeditionService(session)
                .register(
                    expedition_id,
                    RegistrationCreate(user_id=first, idempotency_key="race-seat-first"),
                )
                .status
            ),
            lambda session: str(
                ExpeditionService(session)
                .register(
                    expedition_id,
                    RegistrationCreate(user_id=second, idempotency_key="race-seat-second"),
                )
                .status
            ),
        ],
    )

    statuses = sorted(result[1] for result in results if result[0] == "ok")
    assert statuses == ["confirmed", "waitlisted"]
    roster = _roster(database, expedition_id)
    assert roster.confirmed_count == 2
    assert roster.waitlisted_count == 1
    assert roster.available_places == 0


def test_same_user_overlapping_activities_race_allows_only_one(database: Database) -> None:
    _, first_expedition = _open_expedition(database, capacity=5, offset_days=20)
    _, second_expedition = _open_expedition(database, capacity=5, offset_days=20)
    participant = _make_user(database, "race-busy@example.com", "Race Busy")

    results = _run_concurrent(
        database,
        [
            lambda session: ExpeditionService(session).register(
                first_expedition,
                RegistrationCreate(user_id=participant, idempotency_key="overlap-key-one"),
            ),
            lambda session: ExpeditionService(session).register(
                second_expedition,
                RegistrationCreate(user_id=participant, idempotency_key="overlap-key-two"),
            ),
        ],
    )

    successes = [result for result in results if result[0] == "ok"]
    failures = [result for result in results if result[0] == "error"]
    assert len(successes) == 1
    assert len(failures) == 1
    # A genuine schedule conflict must stay a business conflict, never surface
    # as a generic database-busy failure.
    assert failures[0][1]["code"] == ConflictError.code
    assert "another expedition" in failures[0][1]["message"]

    with database.session() as session:
        registrations = session.scalar(
            select(func.count())
            .select_from(ExpeditionRegistration)
            .where(ExpeditionRegistration.user_id == participant)
        )
        audits = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.entity_type == "expedition_registration",
                AuditLog.actor_id == participant,
                AuditLog.action == "registered",
            )
        )
        idempotent_rows = session.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(IdempotencyRecord.idempotency_key == "overlap-key-two")
        )
    assert registrations == 1
    assert audits == 1
    assert idempotent_rows == 0


def test_duplicate_registration_race_is_reported_distinctly(database: Database) -> None:
    _, expedition_id = _open_expedition(database, capacity=5, offset_days=30)
    participant = _make_user(database, "race-duplicate@example.com", "Race Duplicate")

    results = _run_concurrent(
        database,
        [
            lambda session: ExpeditionService(session).register(
                expedition_id,
                RegistrationCreate(user_id=participant, idempotency_key="duplicate-key-one"),
            ),
            lambda session: ExpeditionService(session).register(
                expedition_id,
                RegistrationCreate(user_id=participant, idempotency_key="duplicate-key-two"),
            ),
        ],
    )

    codes = sorted(
        result[1]["code"] if result[0] == "error" else "ok" for result in results
    )
    assert codes == [DuplicateRegistrationError.code, "ok"]
    with database.session() as session:
        count = session.scalar(
            select(func.count())
            .select_from(ExpeditionRegistration)
            .where(
                ExpeditionRegistration.expedition_id == expedition_id,
                ExpeditionRegistration.user_id == participant,
            )
        )
    assert count == 1


def test_same_idempotency_key_race_converges_to_one_result(database: Database) -> None:
    _, expedition_id = _open_expedition(database, capacity=5, offset_days=40)
    participant = _make_user(database, "race-idem@example.com", "Race Idem")
    key = "shared-idempotency-key"

    def operation(session) -> int:
        return ExpeditionService(session).register(
            expedition_id,
            RegistrationCreate(user_id=participant, idempotency_key=key),
        ).id

    results = _run_concurrent(database, [operation, operation])
    registration_ids = {result[1] for result in results if result[0] == "ok"}
    assert registration_ids and len(registration_ids) == 1
    with database.session() as session:
        idempotent_rows = session.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(IdempotencyRecord.idempotency_key == key)
        )
        registrations = session.scalar(
            select(func.count())
            .select_from(ExpeditionRegistration)
            .where(ExpeditionRegistration.user_id == participant)
        )
    assert idempotent_rows == 1
    assert registrations == 1


def test_reused_key_with_different_payload_race_conflicts_and_rolls_back(
    database: Database,
) -> None:
    _, expedition_id = _open_expedition(database, capacity=5, offset_days=50)
    participant = _make_user(database, "race-reused@example.com", "Race Reused")
    key = "reused-idempotency-key"

    results = _run_concurrent(
        database,
        [
            lambda session: ExpeditionService(session).register(
                expedition_id,
                RegistrationCreate(
                    user_id=participant, role="member", idempotency_key=key
                ),
            ),
            lambda session: ExpeditionService(session).register(
                expedition_id,
                RegistrationCreate(
                    user_id=participant, role="medic", idempotency_key=key
                ),
            ),
        ],
    )

    codes = sorted(
        result[1]["code"] if result[0] == "error" else "ok" for result in results
    )
    assert codes == [IdempotencyConflictError.code, "ok"]
    with database.session() as session:
        registrations = session.scalar(
            select(func.count())
            .select_from(ExpeditionRegistration)
            .where(ExpeditionRegistration.user_id == participant)
        )
        idempotent_rows = session.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(IdempotencyRecord.idempotency_key == key)
        )
        winning_role = session.scalar(
            select(ExpeditionRegistration.role).where(
                ExpeditionRegistration.user_id == participant
            )
        )
    assert registrations == 1
    assert idempotent_rows == 1
    assert winning_role == "member"


def test_waitlist_order_survives_concurrent_registrations(database: Database) -> None:
    _, expedition_id = _open_expedition(database, capacity=2, offset_days=60)
    users = [
        _make_user(database, f"batch-{index}@example.com", f"Batch {index}")
        for index in range(4)
    ]

    def operation(user_id: int, key: str):
        def _inner(session):
            return (
                ExpeditionService(session)
                .register(
                    expedition_id,
                    RegistrationCreate(user_id=user_id, idempotency_key=key),
                )
                .id
            )

        return _inner

    _run_concurrent(
        database,
        [
            operation(user_id, f"batch-key-{index:02d}")
            for index, user_id in enumerate(users)
        ],
    )

    with database.session() as session:
        roster = ExpeditionService(session).roster(expedition_id)
        # organizer + one member confirmed; the remaining three are waitlisted.
        assert roster.confirmed_count == 2
        assert roster.waitlisted_count == 3
        waiting = [member for member in roster.members if member.status == "waitlisted"]
        ordered = sorted(waiting, key=lambda member: (member.registered_at, member.registration_id))
        assert waiting == ordered

        # Withdrawing the confirmed contender promotes the head of the queue.
        expected_promotion = ordered[0].user_id
        organizer_id = _organizer_id(session, expedition_id)
        confirmed_member = next(
            member
            for member in roster.members
            if member.status == "confirmed" and member.user_id != organizer_id
        )
        ExpeditionService(session).withdraw(
            expedition_id,
            WithdrawalRequest(
                user_id=confirmed_member.user_id,
                reason="stepped down",
                idempotency_key="batch-withdrawal-key",
            ),
        )
        roster_after = ExpeditionService(session).roster(expedition_id)
    promoted = next(
        member
        for member in roster_after.members
        if member.user_id == expected_promotion
    )
    assert promoted.status == "confirmed"


def _organizer_id(session, expedition_id: int) -> int:
    return session.get(Expedition, expedition_id).organizer_id


def test_failed_transaction_leaves_no_fragments(database: Database) -> None:
    # Two overlapping expeditions; the contender is already confirmed on the
    # second one, so registering on the first must abort as a schedule
    # conflict while a concurrent member takes the last seat.
    _, blocking_expedition = _open_expedition(database, capacity=5, offset_days=70)
    _, contended_expedition = _open_expedition(database, capacity=2, offset_days=70)
    blocked = _make_user(database, "race-failed@example.com", "Race Failed")
    contender = _make_user(database, "race-contender@example.com", "Race Contender")

    with database.session() as session:
        ExpeditionService(session).register(
            blocking_expedition,
            RegistrationCreate(user_id=blocked, idempotency_key="preexisting-key"),
        )

    results = _run_concurrent(
        database,
        [
            lambda session: ExpeditionService(session).register(
                contended_expedition,
                RegistrationCreate(user_id=contender, idempotency_key="waitlisted-key"),
            ),
            lambda session: ExpeditionService(session).register(
                contended_expedition,
                RegistrationCreate(user_id=blocked, idempotency_key="fragmented-key"),
            ),
        ],
    )
    codes = {
        result[1]["code"] if result[0] == "error" else "ok" for result in results
    }
    assert codes == {ConflictError.code, "ok"}

    with database.session() as session:
        fragment_registrations = session.scalar(
            select(func.count())
            .select_from(ExpeditionRegistration)
            .where(
                ExpeditionRegistration.user_id == blocked,
                ExpeditionRegistration.expedition_id == contended_expedition,
            )
        )
        fragment_idempotency = session.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(IdempotencyRecord.idempotency_key == "fragmented-key")
        )
        fragment_audits = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.correlation_id == "fragmented-key")
        )
        successful_rows = session.scalar(
            select(func.count())
            .select_from(ExpeditionRegistration)
            .where(
                ExpeditionRegistration.user_id == contender,
                ExpeditionRegistration.status == RegistrationStatus.CONFIRMED,
            )
        )
    assert fragment_registrations == 0
    assert fragment_idempotency == 0
    assert fragment_audits == 0
    assert successful_rows == 1


def test_persistent_lock_contention_raises_database_busy_not_conflict(
    settings: Settings,
) -> None:
    busy_settings = Settings(
        database_url=f"sqlite:///{settings.database_path.parent / 'busy.db'}",
        sqlite_timeout_seconds=1,
        sqlite_busy_retries=0,
        sqlite_busy_backoff_seconds=0.01,
    )
    database = Database(busy_settings)
    initialize_database(database)
    _, expedition_id = _open_expedition(database, capacity=5, offset_days=90)
    participant = _make_user(database, "race-busy-lock@example.com", "Race Busy Lock")

    release = threading.Event()

    def hold_writer_lock() -> None:
        with database.session() as holder:
            holder.execute(text("BEGIN IMMEDIATE"))
            release.wait(timeout=3)

    holder_thread = threading.Thread(target=hold_writer_lock)
    holder_thread.start()
    try:
        with database.session() as session, pytest.raises(DatabaseBusyError) as exc_info:
            ExpeditionService(session).register(
                expedition_id,
                RegistrationCreate(user_id=participant, idempotency_key="busy-lock-key"),
            )
        assert exc_info.value.code == DatabaseBusyError.code
    finally:
        release.set()
        holder_thread.join(timeout=5)

    # The timed-out attempt rolled back: lock contention leaves no fragments.
    with database.session() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ExpeditionRegistration)
                .where(ExpeditionRegistration.user_id == participant)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(IdempotencyRecord.idempotency_key == "busy-lock-key")
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.correlation_id == "busy-lock-key")
            )
            == 0
        )
    database.engine.dispose()


def test_state_persists_after_database_reopen(settings: Settings) -> None:
    first = Database(settings)
    initialize_database(first)
    _, expedition_id = _open_expedition(first, capacity=2, offset_days=80)
    winner = _make_user(first, "reopen-winner@example.com", "Reopen Winner")
    loser = _make_user(first, "reopen-loser@example.com", "Reopen Loser")

    race_results = _run_concurrent(
        first,
        [
            lambda session: ExpeditionService(session).register(
                expedition_id,
                RegistrationCreate(user_id=winner, idempotency_key="reopen-key-winner"),
            ),
            lambda session: ExpeditionService(session).register(
                expedition_id,
                RegistrationCreate(user_id=loser, idempotency_key="reopen-key-loser"),
            ),
        ],
    )
    expected_status = {
        result[1].user_id: str(result[1].status)
        for result in race_results
        if result[0] == "ok"
    }
    assert set(expected_status.values()) == {
        RegistrationStatus.CONFIRMED,
        RegistrationStatus.WAITLISTED,
    }
    first.engine.dispose()

    second = Database(settings)
    initialize_database(second)
    try:
        with second.session() as session:
            expedition = session.get(Expedition, expedition_id)
            assert expedition is not None
            roster = ExpeditionService(session).roster(expedition_id)
            assert roster.confirmed_count == 2
            assert roster.waitlisted_count == 1
            statuses = {member.user_id: str(member.status) for member in roster.members}
            assert statuses[winner] == expected_status[winner]
            assert statuses[loser] == expected_status[loser]
            assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 2
            audit_count = session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.entity_type == "expedition_registration",
                    AuditLog.action == "registered",
                )
            )
            assert audit_count == 2
    finally:
        second.engine.dispose()
