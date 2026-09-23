from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar("T")

PositiveId = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
NonNegativeFloat = Annotated[float, Field(ge=0)]
Percentage = Annotated[float, Field(ge=0, le=100)]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class TimestampedResponse(ORMModel):
    id: int
    created_at: datetime
    updated_at: datetime


class VersionedResponse(TimestampedResponse):
    version: int


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
    pages: int


class Page(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMeta

    @classmethod
    def build(cls, items: list[T], *, page: int, page_size: int, total: int) -> Page[T]:
        pages = (total + page_size - 1) // page_size if total else 0
        return cls(
            items=items,
            meta=PageMeta(page=page, page_size=page_size, total=total, pages=pages),
        )


class MessageResponse(BaseModel):
    message: str


class HealthResponse(BaseModel):
    status: str
    database: str
    foreign_keys: int
    journal_mode: str
    version: str


class StateChangeRequest(BaseModel):
    target_status: str = Field(min_length=1, max_length=40)
    reason: str = Field(default="", max_length=1000)
    expected_version: int | None = Field(default=None, ge=1)


class IdempotentRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=160)


class DateRangeQuery(BaseModel):
    start_at: datetime | None = None
    end_at: datetime | None = None

    @field_validator("start_at", "end_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("datetime must include timezone information")
        return value.astimezone(UTC) if value is not None else None


def require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    return value.astimezone(UTC)


def clean_text(value: str) -> str:
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        raise ValueError("value must not be blank")
    return cleaned
