from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from trailforge import __version__
from trailforge.api import api_router
from trailforge.config import Settings, get_settings
from trailforge.database.migrations import initialize_database
from trailforge.database.session import Database
from trailforge.errors import TrailForgeError
from trailforge.schemas.common import HealthResponse


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    database = Database(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        initialize_database(database)
        yield
        database.engine.dispose()

    app = FastAPI(
        title=resolved_settings.api_title,
        version=resolved_settings.api_version,
        description=(
            "Offline-first training, hiking route, expedition, gear, and safety management. "
            "Weather records are explicit offline snapshots and do not imply live rescue support."
        ),
        lifespan=lifespan,
    )
    app.state.database = database
    app.state.settings = resolved_settings
    app.include_router(api_router)
    register_error_handlers(app)

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        details = database.verify_connection()
        return HealthResponse(
            status="ok",
            database="sqlite",
            foreign_keys=int(details["foreign_keys"]),
            journal_mode=str(details["journal_mode"]),
            version=__version__,
        )

    return app


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(TrailForgeError)
    async def handle_domain_error(request: Request, exc: TrailForgeError) -> JSONResponse:
        del request
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.as_detail()})

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del request
        errors: list[dict[str, Any]] = []
        for item in exc.errors():
            errors.append(
                {
                    "location": [str(part) for part in item.get("loc", ())],
                    "message": item.get("msg", "invalid value"),
                    "type": item.get("type", "validation_error"),
                }
            )
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "request_validation_error",
                    "message": "request validation failed",
                    "errors": errors,
                }
            },
        )

    @app.exception_handler(IntegrityError)
    async def handle_integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        del request, exc
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "code": "database_constraint_conflict",
                    "message": "operation conflicts with a database constraint",
                    "context": {},
                }
            },
        )


app = create_app()
