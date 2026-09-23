from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session


def get_session(request: Request) -> Generator[Session, None, None]:
    database = request.app.state.database
    with database.session() as session:
        yield session
