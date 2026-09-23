from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from trailforge.domain.enums import Difficulty, PointType, RiskLevel
from trailforge.schemas.common import TimestampedResponse, VersionedResponse, clean_text


class RouteSegmentCreate(BaseModel):
    sequence: int = Field(ge=1, le=10000)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    distance_km: float = Field(gt=0, le=1000)
    elevation_gain_m: int = Field(default=0, ge=0, le=20000)
    estimated_duration_minutes: int = Field(gt=0, le=10080)
    difficulty: Difficulty
    start_latitude: float = Field(ge=-90, le=90)
    start_longitude: float = Field(ge=-180, le=180)
    end_latitude: float = Field(ge=-90, le=90)
    end_longitude: float = Field(ge=-180, le=180)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return clean_text(value)


class RouteSegmentResponse(TimestampedResponse):
    route_id: int
    sequence: int
    name: str
    description: str
    distance_km: float
    elevation_gain_m: int
    estimated_duration_minutes: int
    difficulty: Difficulty
    start_latitude: float
    start_longitude: float
    end_latitude: float
    end_longitude: float


class RoutePointCreate(BaseModel):
    sequence: int = Field(ge=1, le=100000)
    name: str = Field(min_length=1, max_length=160)
    point_type: PointType
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude_m: int | None = Field(default=None, ge=-500, le=9000)
    distance_from_start_km: float = Field(ge=0, le=1000)
    description: str = Field(default="", max_length=4000)
    supply_details: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return clean_text(value)

    @model_validator(mode="after")
    def validate_supply_details(self) -> RoutePointCreate:
        supply_types = {PointType.WATER, PointType.FOOD, PointType.SHELTER}
        if self.point_type in supply_types and not self.supply_details.strip():
            raise ValueError("supply points require supply_details")
        return self


class RoutePointResponse(TimestampedResponse):
    route_id: int
    sequence: int
    name: str
    point_type: PointType
    latitude: float
    longitude: float
    altitude_m: int | None
    distance_from_start_km: float
    description: str
    supply_details: str


class RiskTagCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern="^[a-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)
    level: RiskLevel
    description: str = Field(default="", max_length=2000)
    mitigation: str = Field(default="", max_length=4000)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return clean_text(value)


class RiskTagResponse(TimestampedResponse):
    code: str
    name: str
    level: RiskLevel
    description: str
    mitigation: str


class TrailRouteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    region: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=10000)
    distance_km: float = Field(gt=0, le=1000)
    elevation_gain_m: int = Field(ge=0, le=20000)
    elevation_loss_m: int = Field(default=0, ge=0, le=20000)
    min_altitude_m: int = Field(default=0, ge=-500, le=9000)
    max_altitude_m: int = Field(default=0, ge=-500, le=9000)
    estimated_duration_minutes: int = Field(gt=0, le=10080)
    difficulty: Difficulty
    is_loop: bool = False
    is_published: bool = False
    segments: list[RouteSegmentCreate] = Field(default_factory=list, max_length=1000)
    points: list[RoutePointCreate] = Field(default_factory=list, max_length=5000)
    risk_tag_ids: list[int] = Field(default_factory=list, max_length=100)

    @field_validator("name", "region")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return clean_text(value)

    @model_validator(mode="after")
    def validate_route_geometry(self) -> TrailRouteCreate:
        if self.max_altitude_m < self.min_altitude_m:
            raise ValueError("max_altitude_m must be at least min_altitude_m")
        segment_sequences = [item.sequence for item in self.segments]
        point_sequences = [item.sequence for item in self.points]
        if len(segment_sequences) != len(set(segment_sequences)):
            raise ValueError("segment sequences must be unique")
        if len(point_sequences) != len(set(point_sequences)):
            raise ValueError("point sequences must be unique")
        if len(self.risk_tag_ids) != len(set(self.risk_tag_ids)):
            raise ValueError("risk_tag_ids must be unique")
        if self.segments:
            segment_distance = sum(item.distance_km for item in self.segments)
            tolerance = max(0.5, self.distance_km * 0.05)
            if abs(segment_distance - self.distance_km) > tolerance:
                raise ValueError("segment distances must approximately match route distance")
        if any(point.distance_from_start_km > self.distance_km for point in self.points):
            raise ValueError("route point cannot be beyond route distance")
        return self


class TrailRouteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=180)
    region: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=10000)
    distance_km: float | None = Field(default=None, gt=0, le=1000)
    elevation_gain_m: int | None = Field(default=None, ge=0, le=20000)
    elevation_loss_m: int | None = Field(default=None, ge=0, le=20000)
    estimated_duration_minutes: int | None = Field(default=None, gt=0, le=10080)
    difficulty: Difficulty | None = None
    is_loop: bool | None = None
    is_published: bool | None = None
    risk_tag_ids: list[int] | None = Field(default=None, max_length=100)
    expected_version: int | None = Field(default=None, ge=1)


class TrailRouteResponse(VersionedResponse):
    name: str
    region: str
    description: str
    distance_km: float
    elevation_gain_m: int
    elevation_loss_m: int
    min_altitude_m: int
    max_altitude_m: int
    estimated_duration_minutes: int
    difficulty: Difficulty
    is_loop: bool
    is_published: bool
    segments: list[RouteSegmentResponse] = Field(default_factory=list)
    points: list[RoutePointResponse] = Field(default_factory=list)
    risk_tags: list[RiskTagResponse] = Field(default_factory=list)


class RouteFilter(BaseModel):
    search: str | None = Field(default=None, max_length=160)
    region: str | None = Field(default=None, max_length=120)
    difficulty: Difficulty | None = None
    risk_level: RiskLevel | None = None
    point_type: PointType | None = None
    min_distance_km: float | None = Field(default=None, ge=0)
    max_distance_km: float | None = Field(default=None, ge=0)
    min_elevation_gain_m: int | None = Field(default=None, ge=0)
    max_elevation_gain_m: int | None = Field(default=None, ge=0)
    max_duration_minutes: int | None = Field(default=None, ge=1)
    is_loop: bool | None = None
    is_published: bool | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    sort: str = Field(default="created_at")
    direction: str = Field(default="desc", pattern="^(asc|desc)$")

    @model_validator(mode="after")
    def validate_ranges(self) -> RouteFilter:
        if (
            self.min_distance_km is not None
            and self.max_distance_km is not None
            and self.min_distance_km > self.max_distance_km
        ):
            raise ValueError("minimum distance cannot exceed maximum distance")
        if (
            self.min_elevation_gain_m is not None
            and self.max_elevation_gain_m is not None
            and self.min_elevation_gain_m > self.max_elevation_gain_m
        ):
            raise ValueError("minimum elevation cannot exceed maximum elevation")
        return self


class RouteSummary(BaseModel):
    id: int
    name: str
    region: str
    distance_km: float
    elevation_gain_m: int
    estimated_duration_minutes: int
    difficulty: Difficulty
    risk_levels: list[RiskLevel]
    supply_point_count: int
    updated_at: datetime
