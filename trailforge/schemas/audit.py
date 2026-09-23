from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from trailforge.domain.enums import AuditAction
from trailforge.schemas.common import ORMModel


class AuditLogResponse(ORMModel):
    id: int
    actor_id: int | None
    occurred_at: datetime
    entity_type: str
    entity_id: int
    action: AuditAction
    before_state: dict[str, Any]
    after_state: dict[str, Any]
    context: dict[str, Any]
    correlation_id: str | None


class AuditFilter(BaseModel):
    actor_id: int | None = Field(default=None, gt=0)
    entity_type: str | None = Field(default=None, max_length=80)
    entity_id: int | None = Field(default=None, gt=0)
    action: AuditAction | None = None
    occurred_after: datetime | None = None
    occurred_before: datetime | None = None
    correlation_id: str | None = Field(default=None, max_length=120)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    sort: str = Field(default="occurred_at")
    direction: str = Field(default="desc", pattern="^(asc|desc)$")


class DashboardStatistics(BaseModel):
    generated_at: datetime
    active_users: int
    published_routes: int
    active_training_plans: int
    upcoming_expeditions: int
    completed_expeditions: int
    total_hiking_distance_km: float
    total_elevation_gain_m: int
    overdue_check_ins: int
    open_emergencies: int
    active_gear_loans: int
