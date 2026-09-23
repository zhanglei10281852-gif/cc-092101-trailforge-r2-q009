from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from tests.conftest import create_expedition, create_route, create_user
from trailforge.domain.enums import LoanStatus, RiskLevel
from trailforge.errors import IdempotencyConflictError, InventoryError
from trailforge.models.audit import AuditLog
from trailforge.models.gear import GearInventory, InventoryMovement
from trailforge.schemas.gear import (
    GearCatalogCreate,
    GearCheckUpsert,
    GearInventoryCreate,
    GearLoanCreate,
    GearLoanReturn,
    GearRequirementCreate,
    InventoryAdjustment,
)
from trailforge.schemas.safety import (
    CheckInScheduleCreate,
    CheckInSubmit,
    EmergencyIncidentCreate,
    EmergencyIncidentUpdate,
    RiskAssessmentCreate,
    WeatherSnapshotCreate,
)
from trailforge.services.gear import GearService
from trailforge.services.safety import SafetyService

UTC = UTC


def _inventory(session, quantity: int = 3) -> tuple[int, int, int]:
    owner = create_user(session)
    service = GearService(session)
    catalog = service.create_catalog(
        GearCatalogCreate(
            sku="TENT-2P",
            name="Two person tent",
            category="shelter",
            default_weight_grams=2200,
            safety_critical=True,
        ),
        actor_id=owner,
    )
    inventory = service.create_inventory(
        GearInventoryCreate(
            catalog_id=catalog.id,
            ownership="club",
            quantity_total=quantity,
            condition="good",
            actor_id=owner,
            idempotency_key="initial-inventory",
        )
    )
    return owner, catalog.id, inventory.id


def test_inventory_creation_has_initial_movement_and_audit(session) -> None:
    owner, _, inventory_id = _inventory(session, quantity=5)
    inventory = session.get(GearInventory, inventory_id)
    assert inventory.quantity_total == 5
    assert inventory.quantity_available == 5
    movement = session.scalar(
        select(InventoryMovement).where(InventoryMovement.inventory_id == inventory_id)
    )
    assert movement.quantity_delta == 5
    assert movement.quantity_after == 5
    assert (
        session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.entity_type == "gear_inventory",
                AuditLog.actor_id == owner,
            )
        )
        == 1
    )


def test_inventory_adjustment_is_idempotent(session) -> None:
    owner, _, inventory_id = _inventory(session)
    service = GearService(session)
    request = InventoryAdjustment(
        quantity_delta=2,
        reason="Donation",
        actor_id=owner,
        idempotency_key="donation-adjustment",
    )
    first = service.adjust_inventory(inventory_id, request)
    second = service.adjust_inventory(inventory_id, request)
    assert first.quantity_total == second.quantity_total == 5
    assert (
        session.scalar(
            select(func.count())
            .select_from(InventoryMovement)
            .where(InventoryMovement.inventory_id == inventory_id)
        )
        == 2
    )


def test_inventory_adjustment_cannot_go_negative(session) -> None:
    owner, _, inventory_id = _inventory(session, quantity=2)
    with pytest.raises(InventoryError, match="negative"):
        GearService(session).adjust_inventory(
            inventory_id,
            InventoryAdjustment(
                quantity_delta=-3,
                reason="Bad count",
                actor_id=owner,
                idempotency_key="negative-adjustment",
            ),
        )


def test_loan_and_partial_return_update_inventory_atomically(session) -> None:
    owner, _, inventory_id = _inventory(session, quantity=4)
    borrower = create_user(session, email="borrower@example.com", name="Borrower")
    service = GearService(session)
    now = datetime.now(UTC)
    loan = service.loan(
        GearLoanCreate(
            inventory_id=inventory_id,
            borrower_id=borrower,
            quantity=2,
            loaned_at=now,
            due_at=now + timedelta(days=3),
            actor_id=owner,
            idempotency_key="loan-two-tents",
        )
    )
    assert session.get(GearInventory, inventory_id).quantity_available == 2
    partial = service.return_loan(
        loan.id,
        GearLoanReturn(
            quantity=1,
            returned_at=now + timedelta(days=1),
            condition_in="good",
            actor_id=owner,
            idempotency_key="partial-return",
        ),
    )
    assert partial.status == LoanStatus.ACTIVE
    assert partial.returned_quantity == 1
    assert session.get(GearInventory, inventory_id).quantity_available == 3
    completed = service.return_loan(
        loan.id,
        GearLoanReturn(
            quantity=1,
            returned_at=now + timedelta(days=2),
            condition_in="fair",
            actor_id=owner,
            idempotency_key="final-return",
        ),
    )
    assert completed.status == LoanStatus.RETURNED
    assert session.get(GearInventory, inventory_id).quantity_available == 4


def test_duplicate_active_loan_and_insufficient_stock_are_rejected(session) -> None:
    owner, _, inventory_id = _inventory(session, quantity=1)
    borrower = create_user(session, email="loaner@example.com", name="Loaner")
    now = datetime.now(UTC)
    service = GearService(session)
    service.loan(
        GearLoanCreate(
            inventory_id=inventory_id,
            borrower_id=borrower,
            quantity=1,
            loaned_at=now,
            due_at=now + timedelta(days=1),
            actor_id=owner,
            idempotency_key="first-loan-key",
        )
    )
    with pytest.raises(InventoryError, match="insufficient"):
        service.loan(
            GearLoanCreate(
                inventory_id=inventory_id,
                borrower_id=borrower,
                quantity=1,
                loaned_at=now,
                due_at=now + timedelta(days=2),
                actor_id=owner,
                idempotency_key="second-loan-key",
            )
        )


def test_return_idempotency_rejects_changed_quantity(session) -> None:
    owner, _, inventory_id = _inventory(session, quantity=2)
    borrower = create_user(session, email="return@example.com", name="Returner")
    now = datetime.now(UTC)
    service = GearService(session)
    loan = service.loan(
        GearLoanCreate(
            inventory_id=inventory_id,
            borrower_id=borrower,
            quantity=2,
            loaned_at=now,
            due_at=now + timedelta(days=3),
            actor_id=owner,
            idempotency_key="return-test-loan",
        )
    )
    service.return_loan(
        loan.id,
        GearLoanReturn(
            quantity=1,
            returned_at=now + timedelta(days=1),
            condition_in="good",
            actor_id=owner,
            idempotency_key="shared-return-key",
        ),
    )
    with pytest.raises(IdempotencyConflictError):
        service.return_loan(
            loan.id,
            GearLoanReturn(
                quantity=2,
                returned_at=now + timedelta(days=1),
                condition_in="good",
                actor_id=owner,
                idempotency_key="shared-return-key",
            ),
        )


def _expedition(session) -> tuple[int, int, int]:
    organizer = create_user(session)
    route = create_route(session, actor_id=organizer)
    expedition = create_expedition(session, organizer_id=organizer, route_id=route)
    return organizer, route, expedition


def test_missing_gear_report_accounts_for_participants(session) -> None:
    organizer, _, expedition = _expedition(session)
    gear = GearService(session)
    catalog = gear.create_catalog(
        GearCatalogCreate(sku="HEADLAMP", name="Headlamp", category="lighting"),
        actor_id=organizer,
    )
    gear.add_requirement(
        expedition,
        GearRequirementCreate(catalog_id=catalog.id, quantity_per_person=1),
        actor_id=organizer,
    )
    report = gear.missing_report(expedition)
    assert report.participant_count == 1
    assert report.is_ready is False
    assert report.missing_items[0].missing_quantity == 1
    gear.upsert_check(
        expedition,
        GearCheckUpsert(
            user_id=organizer,
            catalog_id=catalog.id,
            quantity=1,
            status="verified",
            verified_by=organizer,
        ),
    )
    assert gear.missing_report(expedition).is_ready is True


def test_overdue_check_in_risk_and_submission_side_effects(session) -> None:
    organizer, _, expedition_id = _expedition(session)
    expedition = GearService(session).expeditions.get(expedition_id)
    due = expedition.start_at + timedelta(hours=1)
    service = SafetyService(session)
    check = service.schedule_check_in(
        expedition_id,
        CheckInScheduleCreate(user_id=organizer, check_in_type="routine", due_at=due),
        actor_id=organizer,
    )
    overdue = service.overdue(now=due + timedelta(hours=3))
    assert overdue[0].check_in_id == check.id
    assert overdue[0].risk_level == RiskLevel.HIGH
    submitted = service.submit_check_in(
        check.id,
        CheckInSubmit(
            checked_in_at=due + timedelta(minutes=45),
            is_safe=True,
            note="Delayed by terrain",
            idempotency_key="routine-checkin",
        ),
    )
    assert submitted.late_minutes == 45
    assert service.overdue(now=due + timedelta(hours=3)) == []


def test_risk_assessment_computes_score_and_level(session) -> None:
    organizer, _, expedition = _expedition(session)
    assessment = SafetyService(session).assess_risk(
        RiskAssessmentCreate(
            expedition_id=expedition,
            assessor_id=organizer,
            category="terrain",
            hazard="Loose rock",
            likelihood=4,
            impact=5,
            mitigation="Helmets and spacing",
        )
    )
    assert assessment.score == 20
    assert assessment.risk_level == RiskLevel.CRITICAL


def test_incident_requires_resolution_when_closed(session) -> None:
    organizer, _, expedition = _expedition(session)
    service = SafetyService(session)
    now = datetime.now(UTC)
    incident = service.record_incident(
        EmergencyIncidentCreate(
            expedition_id=expedition,
            reported_by=organizer,
            incident_type="injury",
            risk_level="moderate",
            occurred_at=now,
            description="Minor ankle pain",
            idempotency_key="ankle-incident",
        )
    )
    updated = service.update_incident(
        incident.id,
        EmergencyIncidentUpdate(
            status="resolved",
            actions_taken="Rested and evaluated",
            resolution="Participant walked out safely",
            resolved_at=now + timedelta(hours=1),
            actor_id=organizer,
        ),
    )
    assert updated.status == "resolved"
    assert updated.resolution == "Participant walked out safely"


def test_weather_snapshot_is_explicitly_offline_and_in_summary(session) -> None:
    organizer, _, expedition = _expedition(session)
    service = SafetyService(session)
    snapshot = service.add_weather_snapshot(
        WeatherSnapshotCreate(
            expedition_id=expedition,
            recorded_by=organizer,
            observed_at=datetime.now(UTC),
            location_label="Trailhead board",
            temperature_c=12,
            wind_speed_kph=15,
            precipitation_mm=0,
            visibility_km=10,
            conditions="Cloudy",
            source_note="Copied manually from posted morning bulletin",
            is_manual_observation=True,
        )
    )
    summary = service.summary(expedition)
    assert snapshot.is_manual_observation is True
    assert summary.latest_weather_snapshot.id == snapshot.id
    assert "no offline weather snapshot recorded" not in summary.warnings
