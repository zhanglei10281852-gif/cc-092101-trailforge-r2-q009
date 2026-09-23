from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from trailforge.domain.enums import EmergencyStatus
from trailforge.models.safety import (
    EmergencyIncident,
    ItineraryCheckIn,
    RiskAssessment,
    WeatherSnapshot,
)
from trailforge.repositories.base import BaseRepository


class SafetyRepository(BaseRepository[ItineraryCheckIn]):
    model = ItineraryCheckIn
    sortable = {
        "created_at": ItineraryCheckIn.created_at,
        "due_at": ItineraryCheckIn.due_at,
        "checked_in_at": ItineraryCheckIn.checked_in_at,
    }

    def get_check_in(
        self, check_in_id: int, *, for_update: bool = False
    ) -> ItineraryCheckIn | None:
        statement = select(ItineraryCheckIn).where(ItineraryCheckIn.id == check_in_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def check_ins(self, expedition_id: int) -> list[ItineraryCheckIn]:
        return list(
            self.session.scalars(
                select(ItineraryCheckIn)
                .where(ItineraryCheckIn.expedition_id == expedition_id)
                .order_by(ItineraryCheckIn.due_at)
            )
        )

    def overdue_check_ins(self, now: datetime) -> list[ItineraryCheckIn]:
        statement = (
            select(ItineraryCheckIn)
            .where(
                ItineraryCheckIn.due_at < now,
                ItineraryCheckIn.checked_in_at.is_(None),
            )
            .order_by(ItineraryCheckIn.due_at)
        )
        return list(self.session.scalars(statement))

    def get_incident(
        self, incident_id: int, *, for_update: bool = False
    ) -> EmergencyIncident | None:
        statement = select(EmergencyIncident).where(EmergencyIncident.id == incident_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def incidents(
        self,
        expedition_id: int | None = None,
        *,
        status: EmergencyStatus | None = None,
    ) -> list[EmergencyIncident]:
        statement = select(EmergencyIncident)
        if expedition_id is not None:
            statement = statement.where(EmergencyIncident.expedition_id == expedition_id)
        if status is not None:
            statement = statement.where(EmergencyIncident.status == status)
        return list(self.session.scalars(statement.order_by(EmergencyIncident.occurred_at.desc())))

    def assessments(self, expedition_id: int | None = None) -> list[RiskAssessment]:
        statement = select(RiskAssessment)
        if expedition_id is not None:
            statement = statement.where(RiskAssessment.expedition_id == expedition_id)
        return list(self.session.scalars(statement.order_by(RiskAssessment.score.desc())))

    def weather_snapshots(self, expedition_id: int) -> list[WeatherSnapshot]:
        statement = (
            select(WeatherSnapshot)
            .where(WeatherSnapshot.expedition_id == expedition_id)
            .order_by(WeatherSnapshot.observed_at.desc())
        )
        return list(self.session.scalars(statement))

    def latest_weather(self, expedition_id: int) -> WeatherSnapshot | None:
        statement = (
            select(WeatherSnapshot)
            .where(WeatherSnapshot.expedition_id == expedition_id)
            .order_by(WeatherSnapshot.observed_at.desc())
            .limit(1)
        )
        return self.session.scalar(statement)
