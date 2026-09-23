from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from enum import Enum
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from trailforge.config import Settings
from trailforge.database.session import begin_immediate, is_busy_error
from trailforge.domain.enums import AuditAction
from trailforge.errors import DatabaseBusyError, IdempotencyConflictError
from trailforge.models.audit import AuditLog, IdempotencyRecord
from trailforge.repositories.audit import IdempotencyRepository

T = TypeVar("T")

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

    def run_serialized(self, operation: Callable[[], T]) -> T:
        """Run *operation* inside a ``BEGIN IMMEDIATE`` transaction.

        All checks and writes of a mutating operation run while the connection
        holds SQLite's writer lock, so concurrent connections observe each
        other's committed state instead of interleaving check-then-write.
        Only lock-contention errors trigger a bounded retry; every other error
        propagates untouched and the surrounding session rolls the attempt
        back, leaving no partial rows.
        """
        settings = self.session.info.get("settings") or Settings()
        attempts = settings.sqlite_busy_retries + 1
        last_error: OperationalError | None = None
        for attempt in range(attempts):
            try:
                begin_immediate(self.session)
                return operation()
            except OperationalError as exc:
                last_error = exc
                if not is_busy_error(exc):
                    raise
                self.session.rollback()
                if attempt < attempts - 1:
                    time.sleep(settings.sqlite_busy_backoff_seconds * (2**attempt))
        raise DatabaseBusyError(
            "SQLite remained busy after configured retries",
            context={"attempts": attempts},
        ) from last_error

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
