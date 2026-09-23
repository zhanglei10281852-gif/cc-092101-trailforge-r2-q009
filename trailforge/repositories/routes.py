from __future__ import annotations

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import selectinload

from trailforge.models.routes import RiskTag, RoutePoint, TrailRoute, route_risk_tags
from trailforge.repositories.base import BaseRepository, PageResult
from trailforge.schemas.routes import RouteFilter


class RouteRepository(BaseRepository[TrailRoute]):
    model = TrailRoute
    sortable = {
        "created_at": TrailRoute.created_at,
        "updated_at": TrailRoute.updated_at,
        "name": TrailRoute.name,
        "distance_km": TrailRoute.distance_km,
        "elevation_gain_m": TrailRoute.elevation_gain_m,
        "estimated_duration_minutes": TrailRoute.estimated_duration_minutes,
        "difficulty": TrailRoute.difficulty,
    }

    def get_detail(self, route_id: int, *, for_update: bool = False) -> TrailRoute | None:
        statement = (
            select(TrailRoute)
            .options(
                selectinload(TrailRoute.segments),
                selectinload(TrailRoute.points),
                selectinload(TrailRoute.risk_tags),
            )
            .where(TrailRoute.id == route_id)
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def list_routes(self, filters: RouteFilter) -> PageResult[TrailRoute]:
        statement: Select = select(TrailRoute).options(
            selectinload(TrailRoute.risk_tags),
            selectinload(TrailRoute.points),
        )
        if filters.search:
            pattern = f"%{filters.search.strip()}%"
            statement = statement.where(
                or_(TrailRoute.name.ilike(pattern), TrailRoute.description.ilike(pattern))
            )
        if filters.region:
            statement = statement.where(TrailRoute.region.ilike(f"%{filters.region.strip()}%"))
        if filters.difficulty is not None:
            statement = statement.where(TrailRoute.difficulty == filters.difficulty)
        if filters.risk_level is not None:
            statement = (
                statement.join(route_risk_tags)
                .join(RiskTag)
                .where(RiskTag.level == filters.risk_level)
            )
        if filters.point_type is not None:
            statement = statement.join(RoutePoint).where(
                RoutePoint.point_type == filters.point_type
            )
        if filters.min_distance_km is not None:
            statement = statement.where(TrailRoute.distance_km >= filters.min_distance_km)
        if filters.max_distance_km is not None:
            statement = statement.where(TrailRoute.distance_km <= filters.max_distance_km)
        if filters.min_elevation_gain_m is not None:
            statement = statement.where(TrailRoute.elevation_gain_m >= filters.min_elevation_gain_m)
        if filters.max_elevation_gain_m is not None:
            statement = statement.where(TrailRoute.elevation_gain_m <= filters.max_elevation_gain_m)
        if filters.max_duration_minutes is not None:
            statement = statement.where(
                TrailRoute.estimated_duration_minutes <= filters.max_duration_minutes
            )
        if filters.is_loop is not None:
            statement = statement.where(TrailRoute.is_loop == filters.is_loop)
        if filters.is_published is not None:
            statement = statement.where(TrailRoute.is_published == filters.is_published)
        return self.paginate(
            statement.distinct(),
            page=filters.page,
            page_size=filters.page_size,
            sort=filters.sort,
            direction=filters.direction,
        )

    def get_risk_tags(self, tag_ids: list[int]) -> list[RiskTag]:
        if not tag_ids:
            return []
        return list(self.session.scalars(select(RiskTag).where(RiskTag.id.in_(tag_ids))))

    def get_risk_tag_by_code(self, code: str) -> RiskTag | None:
        return self.session.scalar(select(RiskTag).where(RiskTag.code == code))

    def list_risk_tags(self) -> list[RiskTag]:
        return list(self.session.scalars(select(RiskTag).order_by(RiskTag.level, RiskTag.code)))
