from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from trailforge.domain.enums import PlanStatus, SessionStatus, TrainingType
from trailforge.schemas.common import (
    TimestampedResponse,
    VersionedResponse,
    clean_text,
    require_aware,
)


class TrainingExerciseCreate(BaseModel):
    sequence: int = Field(ge=1, le=500)
    name: str = Field(min_length=1, max_length=160)
    training_type: TrainingType
    target_duration_minutes: int = Field(default=0, ge=0, le=1440)
    target_distance_km: float = Field(default=0, ge=0, le=1000)
    target_repetitions: int = Field(default=0, ge=0, le=10000)
    target_sets: int = Field(default=0, ge=0, le=1000)
    target_load_kg: float = Field(default=0, ge=0, le=1000)
    planned_rpe: int = Field(default=5, ge=1, le=10)
    instructions: str = Field(default="", max_length=4000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return clean_text(value)

    @model_validator(mode="after")
    def require_measurable_target(self) -> TrainingExerciseCreate:
        targets = (
            self.target_duration_minutes,
            self.target_distance_km,
            self.target_repetitions,
            self.target_sets,
        )
        if not any(value > 0 for value in targets):
            raise ValueError("exercise requires at least one measurable target")
        if self.training_type == TrainingType.LOADED_WALK and self.target_load_kg <= 0:
            raise ValueError("loaded walking requires a positive target_load_kg")
        return self


class TrainingExerciseResponse(TimestampedResponse):
    plan_id: int
    sequence: int
    name: str
    training_type: TrainingType
    target_duration_minutes: int
    target_distance_km: float
    target_repetitions: int
    target_sets: int
    target_load_kg: float
    planned_rpe: int
    instructions: str


class TrainingPlanCreate(BaseModel):
    user_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    goal: str = Field(default="", max_length=2000)
    start_at: datetime
    end_at: datetime
    target_sessions_per_week: int = Field(ge=1, le=14)
    exercises: list[TrainingExerciseCreate] = Field(default_factory=list, max_length=100)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return clean_text(value)

    @field_validator("start_at", "end_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def validate_dates_and_sequences(self) -> TrainingPlanCreate:
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be later than start_at")
        sequences = [item.sequence for item in self.exercises]
        if len(sequences) != len(set(sequences)):
            raise ValueError("exercise sequences must be unique")
        return self


class TrainingPlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    goal: str | None = Field(default=None, max_length=2000)
    start_at: datetime | None = None
    end_at: datetime | None = None
    target_sessions_per_week: int | None = Field(default=None, ge=1, le=14)
    expected_version: int | None = Field(default=None, ge=1)

    @field_validator("start_at", "end_at")
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        return require_aware(value) if value is not None else None


class TrainingPlanResponse(VersionedResponse):
    user_id: int
    name: str
    description: str
    goal: str
    start_at: datetime
    end_at: datetime
    target_sessions_per_week: int
    status: PlanStatus
    exercises: list[TrainingExerciseResponse] = Field(default_factory=list)


class TrainingSessionCreate(BaseModel):
    plan_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=160)
    planned_start_at: datetime
    planned_end_at: datetime
    notes: str = Field(default="", max_length=4000)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        return clean_text(value)

    @field_validator("planned_start_at", "planned_end_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def validate_time_order(self) -> TrainingSessionCreate:
        if self.planned_end_at <= self.planned_start_at:
            raise ValueError("planned_end_at must be later than planned_start_at")
        return self


class TrainingSessionResponse(VersionedResponse):
    plan_id: int
    user_id: int
    title: str
    planned_start_at: datetime
    planned_end_at: datetime
    actual_start_at: datetime | None
    actual_end_at: datetime | None
    status: SessionStatus
    notes: str


class TrainingRecordCreate(BaseModel):
    exercise_id: int = Field(gt=0)
    duration_minutes: int = Field(default=0, ge=0, le=1440)
    distance_km: float = Field(default=0, ge=0, le=1000)
    repetitions: int = Field(default=0, ge=0, le=100000)
    sets: int = Field(default=0, ge=0, le=1000)
    load_kg: float = Field(default=0, ge=0, le=1000)
    perceived_exertion: int = Field(ge=1, le=10)
    completion_percent: float = Field(ge=0, le=100)
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def require_recorded_work(self) -> TrainingRecordCreate:
        if not any(
            value > 0
            for value in (
                self.duration_minutes,
                self.distance_km,
                self.repetitions,
                self.sets,
            )
        ):
            raise ValueError("record requires at least one positive work metric")
        return self


class TrainingRecordResponse(TimestampedResponse):
    session_id: int
    exercise_id: int
    duration_minutes: int
    distance_km: float
    repetitions: int
    sets: int
    load_kg: float
    perceived_exertion: int
    completion_percent: float
    training_load: float
    notes: str


class SessionCompleteRequest(BaseModel):
    completed_at: datetime
    records: list[TrainingRecordCreate] = Field(min_length=1, max_length=100)
    expected_version: int | None = Field(default=None, ge=1)

    @field_validator("completed_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def exercise_ids_unique(self) -> SessionCompleteRequest:
        ids = [record.exercise_id for record in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("each exercise may be recorded once per session")
        return self


class TrainingPlanFilter(BaseModel):
    user_id: int | None = Field(default=None, gt=0)
    status: PlanStatus | None = None
    training_type: TrainingType | None = None
    starts_after: datetime | None = None
    ends_before: datetime | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    sort: str = Field(default="start_at")
    direction: str = Field(default="desc", pattern="^(asc|desc)$")


class TrainingStatistics(BaseModel):
    user_id: int
    period_start: datetime | None
    period_end: datetime | None
    planned_sessions: int
    completed_sessions: int
    skipped_sessions: int
    completion_rate: float
    total_duration_minutes: int
    total_distance_km: float
    total_training_load: float
    average_rpe: float
    load_by_type: dict[str, float]
