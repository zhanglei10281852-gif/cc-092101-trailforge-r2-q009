from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from trailforge.api.dependencies import get_session
from trailforge.domain.enums import Difficulty, PointType, RiskLevel
from trailforge.schemas.common import Page
from trailforge.schemas.routes import (
    RiskTagCreate,
    RiskTagResponse,
    RouteFilter,
    TrailRouteCreate,
    TrailRouteResponse,
    TrailRouteUpdate,
)
from trailforge.services.routes import RouteService

router = APIRouter(prefix="/routes", tags=["routes"])
SessionDep = Annotated[Session, Depends(get_session)]


@router.post("/risk-tags", response_model=RiskTagResponse, status_code=status.HTTP_201_CREATED)
def create_risk_tag(
    data: RiskTagCreate,
    session: SessionDep,
    actor_id: int = Query(gt=0),
) -> RiskTagResponse:
    return RouteService(session).create_risk_tag(data, actor_id=actor_id)


@router.get("/risk-tags", response_model=list[RiskTagResponse])
def list_risk_tags(session: SessionDep) -> list[RiskTagResponse]:
    return RouteService(session).list_risk_tags()


@router.post("", response_model=TrailRouteResponse, status_code=status.HTTP_201_CREATED)
def create_route(
    data: TrailRouteCreate,
    session: SessionDep,
    actor_id: int = Query(gt=0),
) -> TrailRouteResponse:
    return RouteService(session).create_route(data, actor_id=actor_id)


@router.get("", response_model=Page[TrailRouteResponse])
def list_routes(
    session: SessionDep,
    search: str | None = None,
    region: str | None = None,
    difficulty: Difficulty | None = None,
    risk_level: RiskLevel | None = None,
    point_type: PointType | None = None,
    min_distance_km: float | None = Query(default=None, ge=0),
    max_distance_km: float | None = Query(default=None, ge=0),
    min_elevation_gain_m: int | None = Query(default=None, ge=0),
    max_elevation_gain_m: int | None = Query(default=None, ge=0),
    max_duration_minutes: int | None = Query(default=None, ge=1),
    is_loop: bool | None = None,
    is_published: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    sort: str = "created_at",
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
) -> Page[TrailRouteResponse]:
    filters = RouteFilter(
        search=search,
        region=region,
        difficulty=difficulty,
        risk_level=risk_level,
        point_type=point_type,
        min_distance_km=min_distance_km,
        max_distance_km=max_distance_km,
        min_elevation_gain_m=min_elevation_gain_m,
        max_elevation_gain_m=max_elevation_gain_m,
        max_duration_minutes=max_duration_minutes,
        is_loop=is_loop,
        is_published=is_published,
        page=page,
        page_size=page_size,
        sort=sort,
        direction=direction,
    )
    return RouteService(session).list_routes(filters)


@router.get("/{route_id}", response_model=TrailRouteResponse)
def get_route(route_id: int, session: SessionDep) -> TrailRouteResponse:
    return RouteService(session).get_route(route_id)


@router.patch("/{route_id}", response_model=TrailRouteResponse)
def update_route(
    route_id: int,
    data: TrailRouteUpdate,
    session: SessionDep,
    actor_id: int = Query(gt=0),
) -> TrailRouteResponse:
    return RouteService(session).update_route(route_id, data, actor_id=actor_id)


@router.get("/{route_id}/readiness")
def route_readiness(route_id: int, session: SessionDep) -> dict[str, object]:
    return RouteService(session).route_readiness(route_id)
