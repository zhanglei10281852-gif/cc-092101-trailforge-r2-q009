from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trailforge.database.base import Base, UTCDateTime, utc_now
from trailforge.domain.enums import AuditAction
from trailforge.models.mixins import IntegerPrimaryKeyMixin


class AuditLog(IntegerPrimaryKeyMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_actor_time", "actor_id", "occurred_at"),
    )

    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[AuditAction] = mapped_column(String(40), nullable=False)
    before_state: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    after_state: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    context: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(120), index=True)


class IdempotencyRecord(IntegerPrimaryKeyMixin, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("scope", "idempotency_key", name="uq_idempotency_scope_key"),
    )

    scope: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[int] = mapped_column(Integer, nullable=False)
    response_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class SchemaMigration(IntegerPrimaryKeyMixin, Base):
    __tablename__ = "schema_migrations"

    version: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
