from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from trailforge.domain.enums import ChecklistStatus, GearCondition, GearOwnership, LoanStatus
from trailforge.schemas.common import (
    TimestampedResponse,
    VersionedResponse,
    clean_text,
    require_aware,
)


class GearCatalogCreate(BaseModel):
    sku: str = Field(min_length=2, max_length=80, pattern="^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=160)
    category: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=4000)
    default_weight_grams: int = Field(default=0, ge=0, le=1000000)
    safety_critical: bool = False
    inspection_interval_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("sku")
    @classmethod
    def normalize_sku(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("name", "category")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return clean_text(value)


class GearCatalogResponse(TimestampedResponse):
    sku: str
    name: str
    category: str
    description: str
    default_weight_grams: int
    safety_critical: bool
    inspection_interval_days: int | None


class GearInventoryCreate(BaseModel):
    catalog_id: int = Field(gt=0)
    owner_id: int | None = Field(default=None, gt=0)
    ownership: GearOwnership
    quantity_total: int = Field(ge=1, le=100000)
    condition: GearCondition = GearCondition.GOOD
    weight_grams: int | None = Field(default=None, ge=0, le=1000000)
    storage_location: str = Field(default="", max_length=160)
    acquired_at: datetime | None = None
    last_inspected_at: datetime | None = None
    notes: str = Field(default="", max_length=4000)
    actor_id: int = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=160)

    @field_validator("acquired_at", "last_inspected_at")
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        return require_aware(value) if value is not None else None

    @model_validator(mode="after")
    def validate_owner(self) -> GearInventoryCreate:
        if self.ownership == GearOwnership.PERSONAL and self.owner_id is None:
            raise ValueError("personal inventory requires owner_id")
        if self.ownership == GearOwnership.CLUB and self.owner_id is not None:
            raise ValueError("club inventory cannot have owner_id")
        return self


class GearInventoryResponse(VersionedResponse):
    catalog_id: int
    owner_id: int | None
    ownership: GearOwnership
    quantity_total: int
    quantity_available: int
    condition: GearCondition
    weight_grams: int
    storage_location: str
    acquired_at: datetime | None
    last_inspected_at: datetime | None
    notes: str


class InventoryAdjustment(BaseModel):
    quantity_delta: int = Field(ge=-100000, le=100000)
    reason: str = Field(min_length=1, max_length=2000)
    actor_id: int = Field(gt=0)
    expected_version: int | None = Field(default=None, ge=1)
    idempotency_key: str = Field(min_length=8, max_length=160)

    @field_validator("quantity_delta")
    @classmethod
    def nonzero_delta(cls, value: int) -> int:
        if value == 0:
            raise ValueError("quantity_delta cannot be zero")
        return value


class GearLoanCreate(BaseModel):
    inventory_id: int = Field(gt=0)
    borrower_id: int = Field(gt=0)
    expedition_id: int | None = Field(default=None, gt=0)
    quantity: int = Field(gt=0, le=100000)
    loaned_at: datetime
    due_at: datetime
    notes: str = Field(default="", max_length=4000)
    actor_id: int = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=160)

    @field_validator("loaned_at", "due_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def validate_due_date(self) -> GearLoanCreate:
        if self.due_at <= self.loaned_at:
            raise ValueError("due_at must be later than loaned_at")
        return self


class GearLoanReturn(BaseModel):
    quantity: int = Field(gt=0, le=100000)
    returned_at: datetime
    condition_in: GearCondition
    notes: str = Field(default="", max_length=4000)
    actor_id: int = Field(gt=0)
    expected_version: int | None = Field(default=None, ge=1)
    idempotency_key: str = Field(min_length=8, max_length=160)

    @field_validator("returned_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return require_aware(value)


class GearLoanResponse(VersionedResponse):
    inventory_id: int
    borrower_id: int
    expedition_id: int | None
    quantity: int
    returned_quantity: int
    loaned_at: datetime
    due_at: datetime
    returned_at: datetime | None
    status: LoanStatus
    condition_out: GearCondition
    condition_in: GearCondition | None
    notes: str


class GearRequirementCreate(BaseModel):
    catalog_id: int = Field(gt=0)
    quantity_per_person: int = Field(default=0, ge=0, le=1000)
    quantity_for_group: int = Field(default=0, ge=0, le=100000)
    mandatory: bool = True
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def require_quantity(self) -> GearRequirementCreate:
        if self.quantity_per_person == 0 and self.quantity_for_group == 0:
            raise ValueError("at least one required quantity must be positive")
        return self


class GearRequirementResponse(TimestampedResponse):
    expedition_id: int
    catalog_id: int
    quantity_per_person: int
    quantity_for_group: int
    mandatory: bool
    notes: str


class GearCheckUpsert(BaseModel):
    user_id: int = Field(gt=0)
    catalog_id: int = Field(gt=0)
    quantity: int = Field(ge=0, le=100000)
    status: ChecklistStatus
    verified_by: int | None = Field(default=None, gt=0)
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def verifier_required(self) -> GearCheckUpsert:
        if self.status == ChecklistStatus.VERIFIED and self.verified_by is None:
            raise ValueError("verified status requires verified_by")
        return self


class GearCheckResponse(TimestampedResponse):
    expedition_id: int
    user_id: int
    catalog_id: int
    quantity: int
    status: ChecklistStatus
    verified_by: int | None
    verified_at: datetime | None
    notes: str


class MissingGearItem(BaseModel):
    catalog_id: int
    sku: str
    name: str
    mandatory: bool
    required_quantity: int
    packed_quantity: int
    missing_quantity: int


class MissingGearReport(BaseModel):
    expedition_id: int
    participant_count: int
    checked_at: datetime
    is_ready: bool
    missing_items: list[MissingGearItem]


class GearStatistics(BaseModel):
    total_catalog_items: int
    total_inventory_units: int
    available_inventory_units: int
    active_loans: int
    overdue_loans: int
    loaned_units: int
    utilization_rate: float
    items_by_condition: dict[str, int]
    loans_by_catalog: dict[str, int]
