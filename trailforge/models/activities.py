from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trailforge.database.base import Base, UTCDateTime
from trailforge.domain.enums import ActivityStatus, RegistrationStatus, RiskLevel, TeamRole
from trailforge.models.mixins import IntegerPrimaryKeyMixin, TimestampMixin, VersionMixin


class Expedition(IntegerPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "expeditions"
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="time_order"),
        CheckConstraint("registration_deadline <= start_at", name="deadline_before_start"),
        CheckConstraint("capacity BETWEEN 1 AND 500", name="capacity_range"),
        CheckConstraint("minimum_fitness_level BETWEEN 1 AND 4", name="fitness_level_range"),
    )

    organizer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    route_id: Mapped[int] = mapped_column(ForeignKey("trail_routes.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    meeting_location: Mapped[str] = mapped_column(String(240), nullable=False)
    meeting_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    start_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    registration_deadline: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_fitness_level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[ActivityStatus] = mapped_column(
        String(24), default=ActivityStatus.DRAFT, nullable=False, index=True
    )
    risk_level: Mapped[RiskLevel] = mapped_column(
        String(24), default=RiskLevel.MODERATE, nullable=False
    )
    cancellation_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)

    registrations: Mapped[list[ExpeditionRegistration]] = relationship(
        back_populates="expedition",
        cascade="all, delete-orphan",
    )


class ExpeditionRegistration(IntegerPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "expedition_registrations"
    __table_args__ = (
        UniqueConstraint("expedition_id", "user_id", name="uq_registration_expedition_user"),
    )

    expedition_id: Mapped[int] = mapped_column(
        ForeignKey("expeditions.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[TeamRole] = mapped_column(String(24), default=TeamRole.MEMBER, nullable=False)
    status: Mapped[RegistrationStatus] = mapped_column(
        String(24), default=RegistrationStatus.CONFIRMED, nullable=False, index=True
    )
    registered_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    expedition: Mapped[Expedition] = relationship(back_populates="registrations")
