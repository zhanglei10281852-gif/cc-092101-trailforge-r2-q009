from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from trailforge.database.base import utc_now
from trailforge.domain.enums import ActivityStatus, EmergencyStatus, LoanStatus, PlanStatus
from trailforge.models.activities import Expedition, ExpeditionRegistration
from trailforge.models.gear import GearCatalog, GearInventory, GearLoan
from trailforge.models.routes import TrailRoute
from trailforge.models.safety import EmergencyIncident, ItineraryCheckIn, RiskAssessment
from trailforge.models.training import TrainingPlan
from trailforge.models.users import User
from trailforge.schemas.activities import ActivityStatistics
from trailforge.schemas.audit import DashboardStatistics
from trailforge.schemas.gear import GearStatistics
from trailforge.schemas.safety import RiskStatistics
from trailforge.services.base import ServiceBase


class StatisticsService(ServiceBase):
    def dashboard(self, *, now: datetime | None = None) -> DashboardStatistics:
        current = now or utc_now()
        active_users = self._count(select(func.count()).where(User.is_active.is_(True)))
        published_routes = self._count(
            select(func.count()).where(TrailRoute.is_published.is_(True))
        )
        active_plans = self._count(
            select(func.count()).where(TrainingPlan.status == PlanStatus.ACTIVE)
        )
        upcoming = self._count(
            select(func.count()).where(
                Expedition.start_at >= current,
                Expedition.status.in_(
                    {ActivityStatus.OPEN, ActivityStatus.ASSEMBLING, ActivityStatus.DEPARTED}
                ),
            )
        )
        completed = self._count(
            select(func.count()).where(Expedition.status == ActivityStatus.COMPLETED)
        )
        distance, gain = self.session.execute(
            select(
                func.coalesce(func.sum(TrailRoute.distance_km), 0),
                func.coalesce(func.sum(TrailRoute.elevation_gain_m), 0),
            )
            .select_from(Expedition)
            .join(TrailRoute, TrailRoute.id == Expedition.route_id)
            .where(Expedition.status == ActivityStatus.COMPLETED)
        ).one()
        overdue = self._count(
            select(func.count()).where(
                ItineraryCheckIn.due_at < current,
                ItineraryCheckIn.checked_in_at.is_(None),
            )
        )
        open_emergencies = self._count(
            select(func.count()).where(
                EmergencyIncident.status.in_({EmergencyStatus.OPEN, EmergencyStatus.MONITORING})
            )
        )
        active_loans = self._count(
            select(func.count()).where(GearLoan.status.in_({LoanStatus.ACTIVE, LoanStatus.OVERDUE}))
        )
        return DashboardStatistics(
            generated_at=current,
            active_users=active_users,
            published_routes=published_routes,
            active_training_plans=active_plans,
            upcoming_expeditions=upcoming,
            completed_expeditions=completed,
            total_hiking_distance_km=round(float(distance), 2),
            total_elevation_gain_m=int(gain),
            overdue_check_ins=overdue,
            open_emergencies=open_emergencies,
            active_gear_loans=active_loans,
        )

    def activities(self, organizer_id: int | None = None) -> ActivityStatistics:
        statement = select(Expedition)
        if organizer_id is not None:
            statement = statement.where(Expedition.organizer_id == organizer_id)
        activities = list(self.session.scalars(statement))
        activity_ids = [item.id for item in activities]
        registrations = (
            list(
                self.session.scalars(
                    select(ExpeditionRegistration).where(
                        ExpeditionRegistration.expedition_id.in_(activity_ids)
                    )
                )
            )
            if activity_ids
            else []
        )
        by_status: dict[str, int] = {}
        by_risk: dict[str, int] = {}
        for activity in activities:
            status = str(activity.status)
            risk = str(activity.risk_level)
            by_status[status] = by_status.get(status, 0) + 1
            by_risk[risk] = by_risk.get(risk, 0) + 1
        completed = sum(item.status == ActivityStatus.COMPLETED for item in activities)
        cancelled = sum(item.status == ActivityStatus.CANCELLED for item in activities)
        confirmed = sum(str(item.status) == "confirmed" for item in registrations)
        return ActivityStatistics(
            organizer_id=organizer_id,
            total_activities=len(activities),
            completed_activities=completed,
            cancelled_activities=cancelled,
            completion_rate=round(completed / len(activities) * 100, 2) if activities else 0,
            total_registrations=len(registrations),
            confirmed_registrations=confirmed,
            average_participants=round(confirmed / len(activities), 2) if activities else 0,
            activities_by_status=by_status,
            activities_by_risk_level=by_risk,
        )

    def gear(self, *, now: datetime | None = None) -> GearStatistics:
        current = now or utc_now()
        inventories = list(self.session.scalars(select(GearInventory)))
        loans = list(self.session.scalars(select(GearLoan)))
        active = [item for item in loans if item.status in {LoanStatus.ACTIVE, LoanStatus.OVERDUE}]
        overdue = [item for item in active if item.due_at < current]
        by_condition: dict[str, int] = {}
        for inventory in inventories:
            key = str(inventory.condition)
            by_condition[key] = by_condition.get(key, 0) + inventory.quantity_total
        loan_counts: dict[str, int] = {}
        rows = self.session.execute(
            select(GearCatalog.name, func.coalesce(func.sum(GearLoan.quantity), 0))
            .join(GearInventory, GearInventory.catalog_id == GearCatalog.id)
            .join(GearLoan, GearLoan.inventory_id == GearInventory.id)
            .group_by(GearCatalog.id, GearCatalog.name)
        )
        for name, quantity in rows:
            loan_counts[str(name)] = int(quantity)
        total = sum(item.quantity_total for item in inventories)
        available = sum(item.quantity_available for item in inventories)
        return GearStatistics(
            total_catalog_items=self._count(select(func.count()).select_from(GearCatalog)),
            total_inventory_units=total,
            available_inventory_units=available,
            active_loans=len(active),
            overdue_loans=len(overdue),
            loaned_units=sum(item.quantity - item.returned_quantity for item in active),
            utilization_rate=round((total - available) / total * 100, 2) if total else 0,
            items_by_condition=by_condition,
            loans_by_catalog=loan_counts,
        )

    def risks(self, *, now: datetime | None = None) -> RiskStatistics:
        current = now or utc_now()
        incidents = list(self.session.scalars(select(EmergencyIncident)))
        assessments = list(self.session.scalars(select(RiskAssessment)))
        check_ins = list(self.session.scalars(select(ItineraryCheckIn)))
        by_type: dict[str, int] = {}
        by_level: dict[str, int] = {}
        for incident in incidents:
            incident_type = str(incident.incident_type)
            level = str(incident.risk_level)
            by_type[incident_type] = by_type.get(incident_type, 0) + 1
            by_level[level] = by_level.get(level, 0) + 1
        open_count = sum(
            item.status in {EmergencyStatus.OPEN, EmergencyStatus.MONITORING} for item in incidents
        )
        resolved = sum(item.status == EmergencyStatus.RESOLVED for item in incidents)
        overdue = sum(item.checked_in_at is None and item.due_at < current for item in check_ins)
        unsafe = sum(item.is_safe is False for item in check_ins)
        scores = [item.score for item in assessments]
        return RiskStatistics(
            total_incidents=len(incidents),
            open_incidents=open_count,
            resolved_incidents=resolved,
            incidents_by_type=by_type,
            incidents_by_level=by_level,
            overdue_check_ins=overdue,
            unsafe_check_ins=unsafe,
            average_assessment_score=round(sum(scores) / len(scores), 2) if scores else 0,
        )

    def _count(self, statement: object) -> int:
        return int(self.session.scalar(statement) or 0)
