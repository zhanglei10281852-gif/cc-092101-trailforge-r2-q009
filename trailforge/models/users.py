from __future__ import annotations

from datetime import date

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trailforge.database.base import Base
from trailforge.domain.enums import FitnessLevel
from trailforge.models.mixins import IntegerPrimaryKeyMixin, TimestampMixin


class User(IntegerPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("length(display_name) BETWEEN 1 AND 100", name="display_name_length"),
    )

    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    phone: Mapped[str | None] = mapped_column(String(40))
    birth_date: Mapped[date | None] = mapped_column(Date)
    locale: Mapped[str] = mapped_column(String(16), default="zh-CN", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    profile: Mapped[SportProfile | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    emergency_contacts: Mapped[list[EmergencyContact]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="EmergencyContact.priority",
    )
    health_restrictions: Mapped[list[HealthRestriction]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class SportProfile(IntegerPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sport_profiles"
    __table_args__ = (
        CheckConstraint("height_cm BETWEEN 80 AND 250", name="height_range"),
        CheckConstraint("weight_kg BETWEEN 20 AND 400", name="weight_range"),
        CheckConstraint("weekly_training_minutes >= 0", name="weekly_minutes_nonnegative"),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    height_cm: Mapped[float] = mapped_column(Float, nullable=False)
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    fitness_level: Mapped[FitnessLevel] = mapped_column(String(24), nullable=False)
    outdoor_experience: Mapped[str] = mapped_column(Text, default="", nullable=False)
    weekly_training_minutes: Mapped[int] = mapped_column(default=0, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    user: Mapped[User] = relationship(back_populates="profile")


class EmergencyContact(IntegerPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "emergency_contacts"
    __table_args__ = (
        UniqueConstraint("user_id", "phone", name="uq_contact_user_phone"),
        UniqueConstraint("user_id", "priority", name="uq_contact_user_priority"),
        CheckConstraint("priority BETWEEN 1 AND 10", name="priority_range"),
        CheckConstraint("length(name) BETWEEN 1 AND 100", name="name_length"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    relationship_label: Mapped[str] = mapped_column(String(60), nullable=False)
    phone: Mapped[str] = mapped_column(String(40), nullable=False)
    priority: Mapped[int] = mapped_column(default=1, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    user: Mapped[User] = relationship(back_populates="emergency_contacts")


class HealthRestriction(IntegerPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "health_restrictions"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_restriction_user_name"),
        CheckConstraint("severity BETWEEN 1 AND 5", name="severity_range"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    severity: Mapped[int] = mapped_column(default=1, nullable=False)
    activity_guidance: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped[User] = relationship(back_populates="health_restrictions")
