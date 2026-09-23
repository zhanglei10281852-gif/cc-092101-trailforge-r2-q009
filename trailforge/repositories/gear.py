from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from trailforge.domain.enums import LoanStatus, RegistrationStatus
from trailforge.models.activities import ExpeditionRegistration
from trailforge.models.gear import (
    ActivityGearCheck,
    ActivityGearRequirement,
    GearCatalog,
    GearInventory,
    GearLoan,
    InventoryMovement,
)
from trailforge.repositories.base import BaseRepository


class GearRepository(BaseRepository[GearInventory]):
    model = GearInventory
    sortable = {
        "created_at": GearInventory.created_at,
        "updated_at": GearInventory.updated_at,
        "quantity_available": GearInventory.quantity_available,
        "condition": GearInventory.condition,
    }

    def get_catalog(self, catalog_id: int) -> GearCatalog | None:
        return self.session.get(GearCatalog, catalog_id)

    def get_catalog_by_sku(self, sku: str) -> GearCatalog | None:
        return self.session.scalar(select(GearCatalog).where(GearCatalog.sku == sku.upper()))

    def list_catalog(self, category: str | None = None) -> list[GearCatalog]:
        statement = select(GearCatalog)
        if category:
            statement = statement.where(GearCatalog.category == category)
        return list(
            self.session.scalars(statement.order_by(GearCatalog.category, GearCatalog.name))
        )

    def get_inventory(self, inventory_id: int, *, for_update: bool = False) -> GearInventory | None:
        statement = select(GearInventory).where(GearInventory.id == inventory_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def get_loan(self, loan_id: int, *, for_update: bool = False) -> GearLoan | None:
        statement = select(GearLoan).where(GearLoan.id == loan_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def active_loan(self, inventory_id: int, borrower_id: int) -> GearLoan | None:
        return self.session.scalar(
            select(GearLoan).where(
                GearLoan.inventory_id == inventory_id,
                GearLoan.borrower_id == borrower_id,
                GearLoan.status.in_({LoanStatus.ACTIVE, LoanStatus.OVERDUE}),
            )
        )

    def list_loans(
        self,
        *,
        borrower_id: int | None = None,
        status: LoanStatus | None = None,
    ) -> list[GearLoan]:
        statement = select(GearLoan)
        if borrower_id is not None:
            statement = statement.where(GearLoan.borrower_id == borrower_id)
        if status is not None:
            statement = statement.where(GearLoan.status == status)
        return list(self.session.scalars(statement.order_by(GearLoan.due_at)))

    def requirements(self, expedition_id: int) -> list[ActivityGearRequirement]:
        return list(
            self.session.scalars(
                select(ActivityGearRequirement)
                .where(ActivityGearRequirement.expedition_id == expedition_id)
                .order_by(ActivityGearRequirement.catalog_id)
            )
        )

    def get_requirement(
        self, expedition_id: int, catalog_id: int
    ) -> ActivityGearRequirement | None:
        return self.session.scalar(
            select(ActivityGearRequirement).where(
                ActivityGearRequirement.expedition_id == expedition_id,
                ActivityGearRequirement.catalog_id == catalog_id,
            )
        )

    def gear_checks(self, expedition_id: int) -> list[ActivityGearCheck]:
        return list(
            self.session.scalars(
                select(ActivityGearCheck).where(ActivityGearCheck.expedition_id == expedition_id)
            )
        )

    def get_gear_check(
        self, expedition_id: int, user_id: int, catalog_id: int
    ) -> ActivityGearCheck | None:
        return self.session.scalar(
            select(ActivityGearCheck).where(
                ActivityGearCheck.expedition_id == expedition_id,
                ActivityGearCheck.user_id == user_id,
                ActivityGearCheck.catalog_id == catalog_id,
            )
        )

    def participant_count(self, expedition_id: int) -> int:
        return int(
            self.session.scalar(
                select(func.count()).where(
                    ExpeditionRegistration.expedition_id == expedition_id,
                    ExpeditionRegistration.status == RegistrationStatus.CONFIRMED,
                )
            )
            or 0
        )

    def movements(
        self,
        inventory_id: int,
        *,
        since: datetime | None = None,
    ) -> list[InventoryMovement]:
        statement = select(InventoryMovement).where(InventoryMovement.inventory_id == inventory_id)
        if since is not None:
            statement = statement.where(InventoryMovement.created_at >= since)
        return list(self.session.scalars(statement.order_by(InventoryMovement.created_at)))
