from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from trailforge.domain.enums import AuditAction, PointType
from trailforge.errors import ConflictError, NotFoundError, ValidationError
from trailforge.models.routes import RiskTag, RoutePoint, RouteSegment, TrailRoute
from trailforge.repositories.base import apply_version
from trailforge.repositories.routes import RouteRepository
from trailforge.schemas.common import Page
from trailforge.schemas.routes import (
    RiskTagCreate,
    RiskTagResponse,
    RouteFilter,
    TrailRouteCreate,
    TrailRouteResponse,
    TrailRouteUpdate,
)
from trailforge.services.base import ServiceBase


class RouteService(ServiceBase):
    def __init__(self, session: Session) -> None:
        super().__init__(session)
        self.routes = RouteRepository(session)

    def create_risk_tag(self, data: RiskTagCreate, *, actor_id: int) -> RiskTagResponse:
        if self.routes.get_risk_tag_by_code(data.code) is not None:
            raise ConflictError("risk tag code already exists", context={"code": data.code})
        tag = RiskTag(**data.model_dump())
        self.session.add(tag)
        self.session.flush()
        self.audit(
            actor_id=actor_id,
            entity_type="risk_tag",
            entity_id=tag.id,
            action=AuditAction.CREATED,
            after=self.snapshot(tag),
        )
        return RiskTagResponse.model_validate(tag)

    def list_risk_tags(self) -> list[RiskTagResponse]:
        return [RiskTagResponse.model_validate(item) for item in self.routes.list_risk_tags()]

    def create_route(self, data: TrailRouteCreate, *, actor_id: int) -> TrailRouteResponse:
        tags = self.routes.get_risk_tags(data.risk_tag_ids)
        if len(tags) != len(data.risk_tag_ids):
            found = {item.id for item in tags}
            missing = sorted(set(data.risk_tag_ids) - found)
            raise ValidationError(
                "one or more risk tags do not exist", context={"missing": missing}
            )
        route_data = data.model_dump(exclude={"segments", "points", "risk_tag_ids"})
        route = TrailRoute(**route_data)
        route.segments = [RouteSegment(**item.model_dump()) for item in data.segments]
        route.points = [RoutePoint(**item.model_dump()) for item in data.points]
        route.risk_tags = tags
        try:
            with self.session.begin_nested():
                self.session.add(route)
                self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("route name must be unique within a region") from exc
        self.audit(
            actor_id=actor_id,
            entity_type="trail_route",
            entity_id=route.id,
            action=AuditAction.CREATED,
            after=self.snapshot(route),
            context={
                "segment_count": len(route.segments),
                "point_count": len(route.points),
                "risk_tags": [tag.code for tag in tags],
            },
        )
        return TrailRouteResponse.model_validate(route)

    def get_route(self, route_id: int) -> TrailRouteResponse:
        route = self.routes.get_detail(route_id)
        if route is None:
            raise NotFoundError(f"TrailRoute {route_id} was not found")
        return TrailRouteResponse.model_validate(route)

    def list_routes(self, filters: RouteFilter) -> Page[TrailRouteResponse]:
        result = self.routes.list_routes(filters)
        return Page[TrailRouteResponse].build(
            [TrailRouteResponse.model_validate(item) for item in result.items],
            page=result.page,
            page_size=result.page_size,
            total=result.total,
        )

    def update_route(
        self, route_id: int, data: TrailRouteUpdate, *, actor_id: int
    ) -> TrailRouteResponse:
        route = self.routes.get_detail(route_id, for_update=True)
        if route is None:
            raise NotFoundError(f"TrailRoute {route_id} was not found")
        apply_version(route, data.expected_version)
        before = self.snapshot(route)
        changes = data.model_dump(
            exclude_unset=True,
            exclude={"risk_tag_ids", "expected_version"},
        )
        for field, value in changes.items():
            setattr(route, field, value)
        if data.risk_tag_ids is not None:
            tags = self.routes.get_risk_tags(data.risk_tag_ids)
            if len(tags) != len(data.risk_tag_ids):
                raise ValidationError("one or more risk tags do not exist")
            route.risk_tags = tags
        self._validate_existing_geometry(route)
        self.session.flush()
        self.audit(
            actor_id=actor_id,
            entity_type="trail_route",
            entity_id=route.id,
            action=AuditAction.UPDATED,
            before=before,
            after=self.snapshot(route),
        )
        return TrailRouteResponse.model_validate(route)

    @staticmethod
    def _validate_existing_geometry(route: TrailRoute) -> None:
        if route.max_altitude_m < route.min_altitude_m:
            raise ValidationError("maximum altitude cannot be below minimum altitude")
        if route.segments:
            segment_distance = sum(item.distance_km for item in route.segments)
            tolerance = max(0.5, route.distance_km * 0.05)
            if abs(segment_distance - route.distance_km) > tolerance:
                raise ValidationError("updated distance no longer matches route segments")
        if any(item.distance_from_start_km > route.distance_km for item in route.points):
            raise ValidationError("updated distance ends before an existing route point")

    def route_readiness(self, route_id: int) -> dict[str, object]:
        route = self.routes.get_detail(route_id)
        if route is None:
            raise NotFoundError(f"TrailRoute {route_id} was not found")
        warnings: list[str] = []
        supply_types = {PointType.WATER, PointType.FOOD, PointType.SHELTER}
        supplies = [point for point in route.points if point.point_type in supply_types]
        exits = [point for point in route.points if point.point_type == PointType.EXIT]
        if not route.segments:
            warnings.append("route has no segments")
        if not route.points:
            warnings.append("route has no key points")
        if route.distance_km >= 15 and not supplies:
            warnings.append("long route has no documented supply point")
        if route.difficulty in {"hard", "extreme"} and not route.risk_tags:
            warnings.append("difficult route has no risk tags")
        if route.distance_km >= 20 and not exits:
            warnings.append("long route has no documented emergency exit")
        return {
            "route_id": route.id,
            "is_publishable": len(warnings) == 0,
            "segment_count": len(route.segments),
            "point_count": len(route.points),
            "supply_point_count": len(supplies),
            "risk_tag_count": len(route.risk_tags),
            "warnings": warnings,
        }
