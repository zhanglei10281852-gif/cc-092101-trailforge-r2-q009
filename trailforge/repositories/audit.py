from __future__ import annotations

from sqlalchemy import Select, select

from trailforge.models.audit import AuditLog, IdempotencyRecord
from trailforge.repositories.base import BaseRepository, PageResult
from trailforge.schemas.audit import AuditFilter


class AuditRepository(BaseRepository[AuditLog]):
    model = AuditLog
    sortable = {
        "occurred_at": AuditLog.occurred_at,
        "entity_type": AuditLog.entity_type,
        "action": AuditLog.action,
        "actor_id": AuditLog.actor_id,
    }

    def list_logs(self, filters: AuditFilter) -> PageResult[AuditLog]:
        statement: Select = select(AuditLog)
        if filters.actor_id is not None:
            statement = statement.where(AuditLog.actor_id == filters.actor_id)
        if filters.entity_type is not None:
            statement = statement.where(AuditLog.entity_type == filters.entity_type)
        if filters.entity_id is not None:
            statement = statement.where(AuditLog.entity_id == filters.entity_id)
        if filters.action is not None:
            statement = statement.where(AuditLog.action == filters.action)
        if filters.occurred_after is not None:
            statement = statement.where(AuditLog.occurred_at >= filters.occurred_after)
        if filters.occurred_before is not None:
            statement = statement.where(AuditLog.occurred_at <= filters.occurred_before)
        if filters.correlation_id is not None:
            statement = statement.where(AuditLog.correlation_id == filters.correlation_id)
        return self.paginate(
            statement,
            page=filters.page,
            page_size=filters.page_size,
            sort=filters.sort,
            direction=filters.direction,
        )


class IdempotencyRepository(BaseRepository[IdempotencyRecord]):
    model = IdempotencyRecord
    sortable = {"created_at": IdempotencyRecord.created_at}

    def get_key(self, scope: str, key: str) -> IdempotencyRecord | None:
        return self.session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.idempotency_key == key,
            )
        )
