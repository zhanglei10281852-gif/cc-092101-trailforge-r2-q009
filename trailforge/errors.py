from __future__ import annotations

from typing import Any


class TrailForgeError(Exception):
    status_code = 400
    code = "trailforge_error"

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context = context or {}

    def as_detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "context": self.context}


class NotFoundError(TrailForgeError):
    status_code = 404
    code = "not_found"


class ConflictError(TrailForgeError):
    status_code = 409
    code = "conflict"


class InvalidStateError(ConflictError):
    code = "invalid_state_transition"


class CapacityError(ConflictError):
    code = "capacity_exceeded"


class InventoryError(ConflictError):
    code = "inventory_error"


class ValidationError(TrailForgeError):
    status_code = 422
    code = "domain_validation_error"


class IdempotencyConflictError(ConflictError):
    code = "idempotency_conflict"


class DatabaseBusyError(TrailForgeError):
    status_code = 503
    code = "database_busy"


class UnauthorizedOperationError(TrailForgeError):
    status_code = 403
    code = "operation_not_allowed"
