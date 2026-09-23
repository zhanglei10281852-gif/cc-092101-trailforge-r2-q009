from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trailforge.database.base import Base
from trailforge.domain.enums import Difficulty, PointType, RiskLevel
from trailforge.models.mixins import IntegerPrimaryKeyMixin, TimestampMixin, VersionMixin

route_risk_tags = Table(
    "route_risk_tags",
    Base.metadata,
    Column("route_id", ForeignKey("trail_routes.id", ondelete="CASCADE"), primary_key=True),
    Column("risk_tag_id", ForeignKey("risk_tags.id", ondelete="CASCADE"), primary_key=True),
)


class TrailRoute(IntegerPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "trail_routes"
    __table_args__ = (
        UniqueConstraint("name", "region", name="uq_route_name_region"),
        CheckConstraint("distance_km > 0", name="distance_positive"),
        CheckConstraint("elevation_gain_m >= 0", name="gain_nonnegative"),
        CheckConstraint("elevation_loss_m >= 0", name="loss_nonnegative"),
        CheckConstraint("estimated_duration_minutes > 0", name="duration_positive"),
        CheckConstraint("min_altitude_m <= max_altitude_m", name="altitude_order"),
    )

    name: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    region: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    distance_km: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    elevation_gain_m: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    elevation_loss_m: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    min_altitude_m: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_altitude_m: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    difficulty: Mapped[Difficulty] = mapped_column(String(24), nullable=False, index=True)
    is_loop: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    segments: Mapped[list[RouteSegment]] = relationship(
        back_populates="route",
        cascade="all, delete-orphan",
        order_by="RouteSegment.sequence",
    )
    points: Mapped[list[RoutePoint]] = relationship(
        back_populates="route",
        cascade="all, delete-orphan",
        order_by="RoutePoint.sequence",
    )
    risk_tags: Mapped[list[RiskTag]] = relationship(
        secondary=route_risk_tags, back_populates="routes"
    )


class RouteSegment(IntegerPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "route_segments"
    __table_args__ = (
        UniqueConstraint("route_id", "sequence", name="uq_segment_route_sequence"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("distance_km > 0", name="distance_positive"),
        CheckConstraint("elevation_gain_m >= 0", name="gain_nonnegative"),
        CheckConstraint("estimated_duration_minutes > 0", name="duration_positive"),
        CheckConstraint("start_latitude BETWEEN -90 AND 90", name="start_latitude_range"),
        CheckConstraint("end_latitude BETWEEN -90 AND 90", name="end_latitude_range"),
        CheckConstraint("start_longitude BETWEEN -180 AND 180", name="start_longitude_range"),
        CheckConstraint("end_longitude BETWEEN -180 AND 180", name="end_longitude_range"),
    )

    route_id: Mapped[int] = mapped_column(ForeignKey("trail_routes.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    distance_km: Mapped[float] = mapped_column(Float, nullable=False)
    elevation_gain_m: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    difficulty: Mapped[Difficulty] = mapped_column(String(24), nullable=False)
    start_latitude: Mapped[float] = mapped_column(Float, nullable=False)
    start_longitude: Mapped[float] = mapped_column(Float, nullable=False)
    end_latitude: Mapped[float] = mapped_column(Float, nullable=False)
    end_longitude: Mapped[float] = mapped_column(Float, nullable=False)

    route: Mapped[TrailRoute] = relationship(back_populates="segments")


class RoutePoint(IntegerPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "route_points"
    __table_args__ = (
        UniqueConstraint("route_id", "sequence", name="uq_point_route_sequence"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        CheckConstraint("distance_from_start_km >= 0", name="distance_nonnegative"),
    )

    route_id: Mapped[int] = mapped_column(ForeignKey("trail_routes.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    point_type: Mapped[PointType] = mapped_column(String(24), nullable=False, index=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    altitude_m: Mapped[int | None] = mapped_column(Integer)
    distance_from_start_km: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    supply_details: Mapped[str] = mapped_column(Text, default="", nullable=False)

    route: Mapped[TrailRoute] = relationship(back_populates="points")


class RiskTag(IntegerPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "risk_tags"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    level: Mapped[RiskLevel] = mapped_column(String(24), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    mitigation: Mapped[str] = mapped_column(Text, default="", nullable=False)

    routes: Mapped[list[TrailRoute]] = relationship(
        secondary=route_risk_tags, back_populates="risk_tags"
    )
