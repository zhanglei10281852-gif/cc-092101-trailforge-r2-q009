from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from trailforge.config import Settings
from trailforge.database.migrations import initialize_database
from trailforge.database.session import Database
from trailforge.main import create_app
from trailforge.schemas.activities import ExpeditionCreate
from trailforge.schemas.routes import RouteSegmentCreate, TrailRouteCreate
from trailforge.schemas.users import SportProfileUpsert, UserCreate
from trailforge.services.activities import ExpeditionService
from trailforge.services.routes import RouteService
from trailforge.services.users import UserService

UTC = UTC


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        sqlite_timeout_seconds=5,
        sqlite_busy_retries=4,
        sqlite_busy_backoff_seconds=0.01,
    )


@pytest.fixture
def database(settings: Settings) -> Database:
    db = Database(settings)
    initialize_database(db)
    yield db
    db.engine.dispose()


@pytest.fixture
def session(database: Database) -> Session:
    with database.session() as db_session:
        yield db_session


@pytest.fixture
def client(settings: Settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def create_user(
    session: Session,
    *,
    email: str = "hiker@example.com",
    name: str = "Test Hiker",
    fitness: str = "intermediate",
) -> int:
    service = UserService(session)
    user = service.create(UserCreate(email=email, display_name=name))
    service.upsert_profile(
        user.id,
        SportProfileUpsert(
            height_cm=172,
            weight_kg=68,
            fitness_level=fitness,
            outdoor_experience="Weekend hiking and local trails",
            weekly_training_minutes=240,
        ),
        actor_id=user.id,
    )
    return user.id


def create_route(
    session: Session,
    *,
    actor_id: int,
    name: str = "Test Ridge Loop",
    published: bool = True,
) -> int:
    route = RouteService(session).create_route(
        TrailRouteCreate(
            name=name,
            region="Test Mountains",
            description="A local offline route",
            distance_km=10,
            elevation_gain_m=500,
            elevation_loss_m=500,
            min_altitude_m=100,
            max_altitude_m=600,
            estimated_duration_minutes=240,
            difficulty="moderate",
            is_loop=True,
            is_published=published,
            segments=[
                RouteSegmentCreate(
                    sequence=1,
                    name="Main ridge",
                    distance_km=10,
                    elevation_gain_m=500,
                    estimated_duration_minutes=240,
                    difficulty="moderate",
                    start_latitude=30,
                    start_longitude=120,
                    end_latitude=30,
                    end_longitude=120,
                )
            ],
        ),
        actor_id=actor_id,
    )
    return route.id


def create_expedition(
    session: Session,
    *,
    organizer_id: int,
    route_id: int,
    capacity: int = 3,
    offset_days: int = 10,
) -> int:
    start = datetime.now(UTC) + timedelta(days=offset_days)
    expedition = ExpeditionService(session).create(
        ExpeditionCreate(
            organizer_id=organizer_id,
            route_id=route_id,
            name=f"Expedition {offset_days}",
            meeting_location="North trailhead",
            meeting_at=start - timedelta(hours=1),
            start_at=start,
            end_at=start + timedelta(hours=8),
            registration_deadline=start - timedelta(days=1),
            capacity=capacity,
            minimum_fitness_level=1,
            risk_level="moderate",
        )
    )
    return expedition.id
