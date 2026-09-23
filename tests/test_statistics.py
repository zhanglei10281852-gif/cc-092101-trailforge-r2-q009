from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.conftest import create_expedition, create_route, create_user
from trailforge.schemas.activities import ActivityStateChange
from trailforge.schemas.gear import GearCatalogCreate, GearInventoryCreate, GearLoanCreate
from trailforge.schemas.safety import RiskAssessmentCreate
from trailforge.services.activities import ExpeditionService
from trailforge.services.gear import GearService
from trailforge.services.safety import SafetyService
from trailforge.services.statistics import StatisticsService

UTC = UTC


def test_completed_expedition_contributes_distance_and_elevation(session) -> None:
    organizer = create_user(session)
    route = create_route(session, actor_id=organizer)
    expedition = create_expedition(session, organizer_id=organizer, route_id=route)
    service = ExpeditionService(session)
    for target in ("open", "assembling", "departed", "in_progress", "completed"):
        service.change_status(
            expedition,
            ActivityStateChange(target_status=target, actor_id=organizer),
        )
    dashboard = StatisticsService(session).dashboard()
    assert dashboard.completed_expeditions == 1
    assert dashboard.total_hiking_distance_km == 10
    assert dashboard.total_elevation_gain_m == 500
    activity_stats = StatisticsService(session).activities(organizer_id=organizer)
    assert activity_stats.completion_rate == 100
    assert activity_stats.confirmed_registrations == 1


def test_gear_utilization_statistics(session) -> None:
    owner = create_user(session)
    borrower = create_user(session, email="stats-borrower@example.com", name="Borrower")
    service = GearService(session)
    catalog = service.create_catalog(
        GearCatalogCreate(sku="POLE", name="Trekking Pole", category="walking"),
        actor_id=owner,
    )
    inventory = service.create_inventory(
        GearInventoryCreate(
            catalog_id=catalog.id,
            ownership="club",
            quantity_total=4,
            actor_id=owner,
            idempotency_key="stats-inventory",
        )
    )
    now = datetime.now(UTC)
    service.loan(
        GearLoanCreate(
            inventory_id=inventory.id,
            borrower_id=borrower,
            quantity=1,
            loaned_at=now,
            due_at=now + timedelta(days=1),
            actor_id=owner,
            idempotency_key="stats-loan",
        )
    )
    stats = StatisticsService(session).gear(now=now)
    assert stats.total_catalog_items == 1
    assert stats.total_inventory_units == 4
    assert stats.available_inventory_units == 3
    assert stats.active_loans == 1
    assert stats.utilization_rate == 25
    assert stats.loans_by_catalog["Trekking Pole"] == 1


def test_risk_statistics_average_score(session) -> None:
    organizer = create_user(session)
    route = create_route(session, actor_id=organizer)
    expedition = create_expedition(session, organizer_id=organizer, route_id=route)
    safety = SafetyService(session)
    for likelihood, impact in ((2, 2), (4, 5)):
        safety.assess_risk(
            RiskAssessmentCreate(
                expedition_id=expedition,
                assessor_id=organizer,
                category="terrain",
                hazard=f"Hazard {likelihood}",
                likelihood=likelihood,
                impact=impact,
                mitigation="Documented mitigation",
            )
        )
    stats = StatisticsService(session).risks()
    assert stats.average_assessment_score == 12
    assert stats.total_incidents == 0
