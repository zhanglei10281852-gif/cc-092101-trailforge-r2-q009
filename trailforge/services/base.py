from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from trailforge.domain.enums import AuditAction
from trailforge.errors import IdempotencyConflictError
from trailforge.models.audit import AuditLog, IdempotencyRecord
from trailforge.repositories.audit import IdempotencyRepository

SENSITIVE_FIELDS = {
    "password",
    "password_hash",
    "secret",
    "token",
    "api_key",
    "authorization",
}


class ServiceBase:
    def __init__(self, session: Session) -> None:
        self.session = session

    def audit(
        self,
        *,
        actor_id: int | None,
        entity_type: str,
        entity_id: int,
        action: AuditAction,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> AuditLog:
        log = AuditLog(
            actor_id=actor_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            before_state=self._sanitize(before or {}),
            after_state=self._sanitize(after or {}),
            context=self._sanitize(context or {}),
            correlation_id=correlation_id,
        )
        self.session.add(log)
        self.session.flush()
        return log

    def snapshot(self, entity: object, *fields: str) -> dict[str, Any]:
        if not fields:
            mapper = inspect(entity).mapper
            fields = tuple(column.key for column in mapper.column_attrs)
        values: dict[str, Any] = {}
        for field in fields:
            if field.lower() in SENSITIVE_FIELDS:
                continue
            values[field] = self._json_value(getattr(entity, field, None))
        return values

    def request_hash(self, payload: BaseModel | dict[str, Any]) -> str:
        if isinstance(payload, BaseModel):
            raw = payload.model_dump(mode="json", exclude={"idempotency_key"})
        else:
            raw = payload
        encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def find_idempotent(
        self,
        *,
        scope: str,
        key: str,
        payload: BaseModel | dict[str, Any],
    ) -> IdempotencyRecord | None:
        existing = IdempotencyRepository(self.session).get_key(scope, key)
        if existing is None:
            return None
        request_hash = self.request_hash(payload)
        if existing.request_hash != request_hash:
            raise IdempotencyConflictError(
                "idempotency key was already used with a different request",
                context={"scope": scope, "key": key},
            )
        return existing

    def save_idempotent(
        self,
        *,
        scope: str,
        key: str,
        payload: BaseModel | dict[str, Any],
        resource_type: str,
        resource_id: int,
        response: dict[str, Any],
    ) -> IdempotencyRecord:
        record = IdempotencyRecord(
            scope=scope,
            idempotency_key=key,
            request_hash=self.request_hash(payload),
            resource_type=resource_type,
            resource_id=resource_id,
            response_json=self._sanitize(response),
        )
        self.session.add(record)
        self.session.flush()
        return record

    @classmethod
    def _sanitize(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): "[REDACTED]"
                if str(key).lower() in SENSITIVE_FIELDS
                else cls._sanitize(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [cls._sanitize(item) for item in value]
        return cls._json_value(value)

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if hasattr(value, "isoformat"):
            return value.isoformat()
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)
