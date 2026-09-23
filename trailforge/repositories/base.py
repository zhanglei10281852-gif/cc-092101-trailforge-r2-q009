from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from trailforge.database.base import Base
from trailforge.errors import NotFoundError, ValidationError

ModelT = TypeVar("ModelT", bound=Base)


@dataclass(frozen=True)
class PageResult(Generic[ModelT]):
    items: list[ModelT]
    total: int
    page: int
    page_size: int


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]
    sortable: dict[str, object]

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        self.session.flush()
        return entity

    def get(self, entity_id: int, *, for_update: bool = False) -> ModelT | None:
        statement = select(self.model).where(self.model.id == entity_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def require(self, entity_id: int, *, for_update: bool = False) -> ModelT:
        entity = self.get(entity_id, for_update=for_update)
        if entity is None:
            raise NotFoundError(
                f"{self.model.__name__} {entity_id} was not found",
                context={"entity": self.model.__name__, "id": entity_id},
            )
        return entity

    def delete(self, entity: ModelT) -> None:
        self.session.delete(entity)
        self.session.flush()

    def count(self, statement: Select | None = None) -> int:
        if statement is None:
            return int(self.session.scalar(select(func.count()).select_from(self.model)) or 0)
        count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
        return int(self.session.scalar(count_statement) or 0)

    def paginate(
        self,
        statement: Select,
        *,
        page: int,
        page_size: int,
        sort: str,
        direction: str,
    ) -> PageResult[ModelT]:
        if sort not in self.sortable:
            raise ValidationError(
                f"unsupported sort field: {sort}",
                context={"allowed": sorted(self.sortable)},
            )
        if direction not in {"asc", "desc"}:
            raise ValidationError("direction must be asc or desc")
        total = self.count(statement)
        column = self.sortable[sort]
        order = column.asc() if direction == "asc" else column.desc()
        offset = (page - 1) * page_size
        items = list(
            self.session.scalars(
                statement.order_by(order, self.model.id.asc()).offset(offset).limit(page_size)
            )
        )
        return PageResult(items=items, total=total, page=page, page_size=page_size)


def apply_version(entity: object, expected_version: int | None) -> None:
    current = getattr(entity, "version", None)
    if expected_version is not None and current != expected_version:
        from trailforge.errors import ConflictError

        raise ConflictError(
            "resource was modified by another operation",
            context={"expected_version": expected_version, "current_version": current},
        )
    if current is not None:
        entity.version = current + 1
