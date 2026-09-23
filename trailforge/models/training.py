from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trailforge.database.base import Base, UTCDateTime
from trailforge.domain.enums import PlanStatus, SessionStatus, TrainingType
from trailforge.models.mixins import IntegerPrimaryKeyMixin, TimestampMixin, VersionMixin


class TrainingPlan(IntegerPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "training_plans"
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="date_order"),
        CheckConstraint("target_sessions_per_week BETWEEN 1 AND 14", name="frequency_range"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    goal: Mapped[str] = mapped_column(Text, default="", nullable=False)
    start_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    target_sessions_per_week: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PlanStatus] = mapped_column(String(24), default=PlanStatus.DRAFT, index=True)

    exercises: Mapped[list[TrainingExercise]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="TrainingExercise.sequence",
    )
    sessions: Mapped[list[TrainingSession]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
    )


class TrainingExercise(IntegerPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "training_exercises"
    __table_args__ = (
        UniqueConstraint("plan_id", "sequence", name="uq_exercise_plan_sequence"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("target_duration_minutes >= 0", name="duration_nonnegative"),
        CheckConstraint("target_distance_km >= 0", name="distance_nonnegative"),
        CheckConstraint("target_repetitions >= 0", name="repetitions_nonnegative"),
        CheckConstraint("target_sets >= 0", name="sets_nonnegative"),
        CheckConstraint("target_load_kg >= 0", name="load_nonnegative"),
        CheckConstraint("planned_rpe BETWEEN 1 AND 10", name="rpe_range"),
    )

    plan_id: Mapped[int] = mapped_column(ForeignKey("training_plans.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    training_type: Mapped[TrainingType] = mapped_column(String(32), nullable=False, index=True)
    target_duration_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    target_distance_km: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    target_repetitions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    target_sets: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    target_load_kg: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    planned_rpe: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    instructions: Mapped[str] = mapped_column(Text, default="", nullable=False)

    plan: Mapped[TrainingPlan] = relationship(back_populates="exercises")


class TrainingSession(IntegerPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "training_sessions"
    __table_args__ = (
        CheckConstraint("planned_end_at > planned_start_at", name="planned_time_order"),
        CheckConstraint(
            "actual_end_at IS NULL OR actual_start_at IS NULL OR actual_end_at >= actual_start_at",
            name="actual_time_order",
        ),
    )

    plan_id: Mapped[int] = mapped_column(ForeignKey("training_plans.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    planned_start_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    planned_end_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    actual_start_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    actual_end_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[SessionStatus] = mapped_column(
        String(24), default=SessionStatus.PLANNED, nullable=False, index=True
    )
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    plan: Mapped[TrainingPlan] = relationship(back_populates="sessions")
    records: Mapped[list[TrainingRecord]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )


class TrainingRecord(IntegerPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "training_records"
    __table_args__ = (
        UniqueConstraint("session_id", "exercise_id", name="uq_record_session_exercise"),
        CheckConstraint("duration_minutes >= 0", name="duration_nonnegative"),
        CheckConstraint("distance_km >= 0", name="distance_nonnegative"),
        CheckConstraint("repetitions >= 0", name="repetitions_nonnegative"),
        CheckConstraint("sets >= 0", name="sets_nonnegative"),
        CheckConstraint("load_kg >= 0", name="load_nonnegative"),
        CheckConstraint("perceived_exertion BETWEEN 1 AND 10", name="rpe_range"),
        CheckConstraint("completion_percent BETWEEN 0 AND 100", name="completion_range"),
        CheckConstraint("training_load >= 0", name="training_load_nonnegative"),
    )

    session_id: Mapped[int] = mapped_column(ForeignKey("training_sessions.id", ondelete="CASCADE"))
    exercise_id: Mapped[int] = mapped_column(
        ForeignKey("training_exercises.id", ondelete="RESTRICT")
    )
    duration_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    distance_km: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    repetitions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sets: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    load_kg: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    perceived_exertion: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_percent: Mapped[float] = mapped_column(Float, nullable=False)
    training_load: Mapped[float] = mapped_column(Float, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    session: Mapped[TrainingSession] = relationship(back_populates="records")
    exercise: Mapped[TrainingExercise] = relationship()
