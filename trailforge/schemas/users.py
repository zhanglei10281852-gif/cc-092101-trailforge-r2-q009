from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from trailforge.domain.enums import FitnessLevel
from trailforge.schemas.common import ORMModel, PositiveId, TimestampedResponse, clean_text


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=40)
    birth_date: date | None = None
    locale: str = Field(default="zh-CN", min_length=2, max_length=16)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized.count("@") != 1:
            raise ValueError("email must contain one @ character")
        local, domain = normalized.split("@")
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("email format is invalid")
        return normalized

    @field_validator("display_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return clean_text(value)

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if len(cleaned) < 5:
            raise ValueError("phone number is too short")
        return cleaned

    @field_validator("birth_date")
    @classmethod
    def validate_birth_date(cls, value: date | None) -> date | None:
        if value is not None and value >= date.today():
            raise ValueError("birth_date must be in the past")
        return value


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=40)
    birth_date: date | None = None
    locale: str | None = Field(default=None, min_length=2, max_length=16)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    is_active: bool | None = None

    @field_validator("display_name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        return clean_text(value) if value is not None else None


class UserResponse(TimestampedResponse):
    email: str
    display_name: str
    phone: str | None
    birth_date: date | None
    locale: str
    timezone: str
    is_active: bool


class SportProfileUpsert(BaseModel):
    height_cm: float = Field(ge=80, le=250)
    weight_kg: float = Field(ge=20, le=400)
    fitness_level: FitnessLevel
    outdoor_experience: str = Field(default="", max_length=4000)
    weekly_training_minutes: int = Field(default=0, ge=0, le=10080)
    notes: str = Field(default="", max_length=4000)


class SportProfileResponse(TimestampedResponse):
    user_id: int
    height_cm: float
    weight_kg: float
    fitness_level: FitnessLevel
    outdoor_experience: str
    weekly_training_minutes: int
    notes: str


class EmergencyContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    relationship_label: str = Field(min_length=1, max_length=60)
    phone: str = Field(min_length=5, max_length=40)
    priority: int = Field(default=1, ge=1, le=10)
    notes: str = Field(default="", max_length=1000)

    @field_validator("name", "relationship_label", "phone")
    @classmethod
    def normalize_required(cls, value: str) -> str:
        return clean_text(value)


class EmergencyContactUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    relationship_label: str | None = Field(default=None, min_length=1, max_length=60)
    phone: str | None = Field(default=None, min_length=5, max_length=40)
    priority: int | None = Field(default=None, ge=1, le=10)
    notes: str | None = Field(default=None, max_length=1000)


class EmergencyContactResponse(TimestampedResponse):
    user_id: int
    name: str
    relationship_label: str
    phone: str
    priority: int
    notes: str


class HealthRestrictionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    severity: int = Field(ge=1, le=5)
    activity_guidance: str = Field(default="", max_length=4000)
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return clean_text(value)


class HealthRestrictionUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=4000)
    severity: int | None = Field(default=None, ge=1, le=5)
    activity_guidance: str | None = Field(default=None, max_length=4000)
    is_active: bool | None = None


class HealthRestrictionResponse(TimestampedResponse):
    user_id: int
    name: str
    description: str
    severity: int
    activity_guidance: str
    is_active: bool


class UserProfileResponse(ORMModel):
    user: UserResponse
    sport_profile: SportProfileResponse | None
    emergency_contacts: list[EmergencyContactResponse]
    health_restrictions: list[HealthRestrictionResponse]


class UserFilter(BaseModel):
    search: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None
    fitness_level: FitnessLevel | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    sort: str = Field(default="created_at")
    direction: str = Field(default="desc", pattern="^(asc|desc)$")


class UserSummary(BaseModel):
    id: PositiveId
    display_name: str
    email: str
    fitness_level: FitnessLevel | None
    active_restriction_count: int
    emergency_contact_count: int
    joined_at: datetime
