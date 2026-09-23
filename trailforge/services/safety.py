from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from trailforge.database.base import utc_now
from trailforge.domain.enums import (
    ActivityStatus,
    AuditAction,
    EmergencyStatus,
    RiskLevel,
)
from trailforge.errors import ConflictError, InvalidStateError, NotFoundError, ValidationError
from trailforge.models.safety import (
    EmergencyIncident,
    ItineraryCheckIn,
    RiskAssessment,
    WeatherSnapshot,
)
from trailforge.repositories.activities import ExpeditionRepository
from trailforge.repositories.base import apply_version
from trailforge.repositories.safety import SafetyRepository
from trailforge.repositories.users import UserRepository
from trailforge.schemas.safety import (
    CheckInResponse,
    CheckInScheduleCreate,
    CheckInSubmit,
    EmergencyIncidentCreate,
    EmergencyIncidentResponse,
    EmergencyIncidentUpdate,
    OverdueCheckIn,
    RiskAssessmentCreate,
    RiskAssessmentResponse,
    SafetySummary,
    WeatherSnapshotCreate,
    WeatherSnapshotResponse,
)
from trailforge.services.base import ServiceBase


class SafetyService(ServiceBase):
    def __init__(self, session: Session) -> None:
        super().__init__(session)
        self.safety = SafetyRepository(session)
        self.expeditions = ExpeditionRepository(session)
        self.users = UserRepository(session)

    def schedule_check_in(
        self, expedition_id: int, data: CheckInScheduleCreate, *, actor_id: int
    ) -> CheckInResponse:
        expedition = self.expeditions.get_detail(expedition_id)
        if expedition is None:
            raise NotFoundError(f"Expedition {expedition_id} was not found")
        self.users.require(data.user_id)
        registration = self.expeditions.get_registration(expedition_id, data.user_id)
        if registration is None or str(registration.status) not in {"confirmed", "pending"}:
            raise ValidationError("check-in can only be scheduled for an active participant")
        lower_bound = min(expedition.meeting_at, expedition.start_at)
        upper_bound = expedition.end_at
        if data.due_at < lower_bound or data.due_at > upper_bound:
            raise ValidationError("check-in due time must fall within the expedition window")
        check_in = ItineraryCheckIn(expedition_id=expedition_id, **data.model_dump())
        try:
            with self.session.begin_nested():
                self.session.add(check_in)
                self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("the same check-in slot already exists") from exc
        self.audit(
            actor_id=actor_id,
            entity_type="itinerary_check_in",
            entity_id=check_in.id,
            action=AuditAction.CREATED,
            after=self.snapshot(check_in),
        )
        return CheckInResponse.model_validate(check_in)

    def submit_check_in(self, check_in_id: int, data: CheckInSubmit) -> CheckInResponse:
        scope = f"safety:check-in:{check_in_id}:submit"
        prior = self.find_idempotent(scope=scope, key=data.idempotency_key, payload=data)
        if prior is not None:
            check_in = self.safety.get_check_in(check_in_id)
            if check_in is None:
                raise ConflictError("idempotency record references missing check-in")
            return CheckInResponse.model_validate(check_in)
        check_in = self.safety.get_check_in(check_in_id, for_update=True)
        if check_in is None:
            raise NotFoundError(f"ItineraryCheckIn {check_in_id} was not found")
        if check_in.checked_in_at is not None:
            raise ConflictError("check-in has already been submitted")
        delta_seconds = (data.checked_in_at - check_in.due_at).total_seconds()
        check_in.checked_in_at = data.checked_in_at
        check_in.latitude = data.latitude
        check_in.longitude = data.longitude
        check_in.note = data.note
        check_in.is_safe = data.is_safe
        check_in.late_minutes = max(int(delta_seconds // 60), 0)
        self.session.flush()
        response = CheckInResponse.model_validate(check_in)
        self.save_idempotent(
            scope=scope,
            key=data.idempotency_key,
            payload=data,
            resource_type="itinerary_check_in",
            resource_id=check_in.id,
            response=response.model_dump(mode="json"),
        )
        self.audit(
            actor_id=check_in.user_id,
            entity_type="itinerary_check_in",
            entity_id=check_in.id,
            action=AuditAction.CHECKED_IN,
            after={
                "checked_in_at": check_in.checked_in_at,
                "is_safe": check_in.is_safe,
                "late_minutes": check_in.late_minutes,
            },
            correlation_id=data.idempotency_key,
        )
        return response

    def overdue(self, *, now: datetime | None = None) -> list[OverdueCheckIn]:
        current = now or utc_now()
        results: list[OverdueCheckIn] = []
        for check_in in self.safety.overdue_check_ins(current):
            expedition = self.expeditions.get(check_in.expedition_id)
            user = self.users.get(check_in.user_id)
            if expedition is None or user is None:
                continue
            overdue_minutes = max(int((current - check_in.due_at).total_seconds() // 60), 0)
            risk_level = self._overdue_risk(overdue_minutes)
            results.append(
                OverdueCheckIn(
                    check_in_id=check_in.id,
                    expedition_id=expedition.id,
                    expedition_name=expedition.name,
                    user_id=user.id,
                    display_name=user.display_name,
                    check_in_type=check_in.check_in_type,
                    due_at=check_in.due_at,
                    overdue_minutes=overdue_minutes,
                    risk_level=risk_level,
                )
            )
        return results

    def record_incident(self, data: EmergencyIncidentCreate) -> EmergencyIncidentResponse:
        scope = f"safety:expedition:{data.expedition_id}:incident"
        prior = self.find_idempotent(scope=scope, key=data.idempotency_key, payload=data)
        if prior is not None:
            incident = self.safety.get_incident(prior.resource_id)
            if incident is None:
                raise ConflictError("idempotency record references missing incident")
            return EmergencyIncidentResponse.model_validate(incident)
        expedition = self.expeditions.get(data.expedition_id)
        if expedition is None:
            raise NotFoundError(f"Expedition {data.expedition_id} was not found")
        self.users.require(data.reported_by)
        if expedition.status in {ActivityStatus.COMPLETED, ActivityStatus.CANCELLED}:
            raise InvalidStateError("cannot open an incident for a closed expedition")
        incident_data = data.model_dump(exclude={"idempotency_key"})
        incident = EmergencyIncident(**incident_data)
        self.session.add(incident)
        self.session.flush()
        response = EmergencyIncidentResponse.model_validate(incident)
        self.save_idempotent(
            scope=scope,
            key=data.idempotency_key,
            payload=data,
            resource_type="emergency_incident",
            resource_id=incident.id,
            response=response.model_dump(mode="json"),
        )
        self.audit(
            actor_id=data.reported_by,
            entity_type="emergency_incident",
            entity_id=incident.id,
            action=AuditAction.EMERGENCY_RECORDED,
            after=self.snapshot(incident),
            correlation_id=data.idempotency_key,
        )
        return response

    def update_incident(
        self, incident_id: int, data: EmergencyIncidentUpdate
    ) -> EmergencyIncidentResponse:
        incident = self.safety.get_incident(incident_id, for_update=True)
        if incident is None:
            raise NotFoundError(f"EmergencyIncident {incident_id} was not found")
        if incident.status in {EmergencyStatus.RESOLVED, EmergencyStatus.FALSE_ALARM}:
            raise InvalidStateError("closed incidents cannot be modified")
        apply_version(incident, data.expected_version)
        before = self.snapshot(incident, "status", "actions_taken", "resolution")
        incident.status = data.status
        if data.actions_taken is not None:
            incident.actions_taken = data.actions_taken
        if data.resolution is not None:
            incident.resolution = data.resolution
        incident.resolved_at = data.resolved_at
        self.session.flush()
        self.audit(
            actor_id=data.actor_id,
            entity_type="emergency_incident",
            entity_id=incident.id,
            action=AuditAction.STATUS_CHANGED,
            before=before,
            after=self.snapshot(incident, "status", "actions_taken", "resolution"),
        )
        return EmergencyIncidentResponse.model_validate(incident)

    def assess_risk(self, data: RiskAssessmentCreate) -> RiskAssessmentResponse:
        if self.expeditions.get(data.expedition_id) is None:
            raise NotFoundError(f"Expedition {data.expedition_id} was not found")
        self.users.require(data.assessor_id)
        score = data.likelihood * data.impact
        level = self._score_level(score)
        assessment = RiskAssessment(
            **data.model_dump(),
            score=score,
            risk_level=level,
        )
        self.session.add(assessment)
        self.session.flush()
        self.audit(
            actor_id=data.assessor_id,
            entity_type="risk_assessment",
            entity_id=assessment.id,
            action=AuditAction.RISK_RECORDED,
            after=self.snapshot(assessment),
        )
        return RiskAssessmentResponse.model_validate(assessment)

    def add_weather_snapshot(self, data: WeatherSnapshotCreate) -> WeatherSnapshotResponse:
        if self.expeditions.get(data.expedition_id) is None:
            raise NotFoundError(f"Expedition {data.expedition_id} was not found")
        self.users.require(data.recorded_by)
        snapshot = WeatherSnapshot(**data.model_dump())
        self.session.add(snapshot)
        self.session.flush()
        self.audit(
            actor_id=data.recorded_by,
            entity_type="weather_snapshot",
            entity_id=snapshot.id,
            action=AuditAction.CREATED,
            after=self.snapshot(snapshot),
            context={"offline_snapshot": True, "no_realtime_claim": True},
        )
        return WeatherSnapshotResponse.model_validate(snapshot)

    def summary(self, expedition_id: int, *, now: datetime | None = None) -> SafetySummary:
        if self.expeditions.get(expedition_id) is None:
            raise NotFoundError(f"Expedition {expedition_id} was not found")
        current = now or utc_now()
        check_ins = self.safety.check_ins(expedition_id)
        incidents = self.safety.incidents(expedition_id)
        assessments = self.safety.assessments(expedition_id)
        latest_weather = self.safety.latest_weather(expedition_id)
        overdue_count = sum(
            item.checked_in_at is None and item.due_at < current for item in check_ins
        )
        unsafe_count = sum(item.is_safe is False for item in check_ins)
        open_incidents = [
            item
            for item in incidents
            if item.status in {EmergencyStatus.OPEN, EmergencyStatus.MONITORING}
        ]
        warnings: list[str] = []
        if overdue_count:
            warnings.append(f"{overdue_count} overdue check-in(s)")
        if unsafe_count:
            warnings.append(f"{unsafe_count} unsafe check-in(s)")
        if open_incidents:
            warnings.append(f"{len(open_incidents)} open incident(s)")
        if latest_weather is None:
            warnings.append("no offline weather snapshot recorded")
        risk_rank = {
            RiskLevel.LOW: 1,
            RiskLevel.MODERATE: 2,
            RiskLevel.HIGH: 3,
            RiskLevel.CRITICAL: 4,
        }
        highest = (
            max((RiskLevel(item.risk_level) for item in incidents), key=risk_rank.get)
            if incidents
            else None
        )
        return SafetySummary(
            expedition_id=expedition_id,
            generated_at=current,
            scheduled_check_ins=len(check_ins),
            completed_check_ins=sum(item.checked_in_at is not None for item in check_ins),
            overdue_check_ins=overdue_count,
            unsafe_check_ins=unsafe_count,
            open_incidents=len(open_incidents),
            highest_incident_risk=highest,
            assessment_count=len(assessments),
            highest_assessment_score=max((item.score for item in assessments), default=None),
            latest_weather_snapshot=(
                WeatherSnapshotResponse.model_validate(latest_weather) if latest_weather else None
            ),
            warnings=warnings,
        )

    @staticmethod
    def _overdue_risk(minutes: int) -> RiskLevel:
        if minutes < 30:
            return RiskLevel.LOW
        if minutes < 120:
            return RiskLevel.MODERATE
        if minutes < 360:
            return RiskLevel.HIGH
        return RiskLevel.CRITICAL

    @staticmethod
    def _score_level(score: int) -> RiskLevel:
        if score <= 4:
            return RiskLevel.LOW
        if score <= 9:
            return RiskLevel.MODERATE
        if score <= 16:
            return RiskLevel.HIGH
        return RiskLevel.CRITICAL
