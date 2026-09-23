from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from trailforge.domain.enums import ActivityStatus, RegistrationStatus, RiskLevel, TeamRole
from trailforge.schemas.common import (
    VersionedResponse,
    clean_text,
    require_aware,
)


class ExpeditionCreate(BaseModel):
    organizer_id: int = Field(gt=0)
    route_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=180)
    description: str = Field(default="", max_length=10000)
    meeting_location: str = Field(min_length=1, max_length=240)
    meeting_at: datetime
    start_at: datetime
    end_at: datetime
    registration_deadline: datetime
    capacity: int = Field(ge=1, le=500)
    minimum_fitness_level: int = Field(default=1, ge=1, le=4)
    risk_level: RiskLevel = RiskLevel.MODERATE

    @field_validator("name", "meeting_location")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return clean_text(value)

    @field_validator("meeting_at", "start_at", "end_at", "registration_deadline")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def validate_schedule(self) -> ExpeditionCreate:
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be later than start_at")
        if self.meeting_at > self.start_at:
            raise ValueError("meeting_at cannot be later than start_at")
        if self.registration_deadline > self.start_at:
            raise ValueError("registration_deadline cannot be later than start_at")
        return self


class ExpeditionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=180)
    description: str | None = Field(default=None, max_length=10000)
    meeting_location: str | None = Field(default=None, min_length=1, max_length=240)
    meeting_at: datetime | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    registration_deadline: datetime | None = None
    capacity: int | None = Field(default=None, ge=1, le=500)
    minimum_fitness_level: int | None = Field(default=None, ge=1, le=4)
    risk_level: RiskLevel | None = None
    expected_version: int | None = Field(default=None, ge=1)

    @field_validator("meeting_at", "start_at", "end_at", "registration_deadline")
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        return require_aware(value) if value is not None else None


class ExpeditionResponse(VersionedResponse):
    organizer_id: int
    route_id: int
    name: str
    description: str
    meeting_location: str
    meeting_at: datetime
    start_at: datetime
    end_at: datetime
    registration_deadline: datetime
    capacity: int
    minimum_fitness_level: int
    status: ActivityStatus
    risk_level: RiskLevel
    cancellation_reason: str


class RegistrationCreate(BaseModel):
    user_id: int = Field(gt=0)
    role: TeamRole = TeamRole.MEMBER
    notes: str = Field(default="", max_length=2000)
    idempotency_key: str = Field(min_length=8, max_length=160)


class RegistrationUpdate(BaseModel):
    role: TeamRole | None = None
    status: RegistrationStatus | None = None
    notes: str | None = Field(default=None, max_length=2000)
    expected_version: int | None = Field(default=None, ge=1)


class RegistrationResponse(VersionedResponse):
    expedition_id: int
    user_id: int
    role: TeamRole
    status: RegistrationStatus
    registered_at: datetime
    withdrawn_at: datetime | None
    notes: str


class WithdrawalRequest(BaseModel):
    user_id: int = Field(gt=0)
    reason: str = Field(default="", max_length=1000)
    idempotency_key: str = Field(min_length=8, max_length=160)


class ActivityStateChange(BaseModel):
    target_status: ActivityStatus
    actor_id: int = Field(gt=0)
    reason: str = Field(default="", max_length=2000)
    expected_version: int | None = Field(default=None, ge=1)


class ExpeditionFilter(BaseModel):
    organizer_id: int | None = Field(default=None, gt=0)
    route_id: int | None = Field(default=None, gt=0)
    participant_id: int | None = Field(default=None, gt=0)
    status: ActivityStatus | None = None
    risk_level: RiskLevel | None = None
    starts_after: datetime | None = None
    starts_before: datetime | None = None
    has_capacity: bool | None = None
    search: str | None = Field(default=None, max_length=180)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    sort: str = Field(default="start_at")
    direction: str = Field(default="asc", pattern="^(asc|desc)$")


class ExpeditionRosterEntry(BaseModel):
    registration_id: int
    user_id: int
    display_name: str
    role: TeamRole
    status: RegistrationStatus
    registered_at: datetime
    fitness_level: str | None
    has_emergency_contact: bool
    active_health_restrictions: int


class ExpeditionRoster(BaseModel):
    expedition_id: int
    capacity: int
    confirmed_count: int
    waitlisted_count: int
    available_places: int
    members: list[ExpeditionRosterEntry]


class ActivityStatistics(BaseModel):
    organizer_id: int | None
    total_activities: int
    completed_activities: int
    cancelled_activities: int
    completion_rate: float
    total_registrations: int
    confirmed_registrations: int
    average_participants: float
    activities_by_status: dict[str, int]
    activities_by_risk_level: dict[str, int]
