from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from trailforge.database.base import utc_now
from trailforge.domain.enums import (
    AuditAction,
    ChecklistStatus,
    GearCondition,
    InventoryMovementType,
    LoanStatus,
)
from trailforge.errors import ConflictError, InventoryError, NotFoundError, ValidationError
from trailforge.models.gear import (
    ActivityGearCheck,
    ActivityGearRequirement,
    GearCatalog,
    GearInventory,
    GearLoan,
    InventoryMovement,
)
from trailforge.repositories.activities import ExpeditionRepository
from trailforge.repositories.base import apply_version
from trailforge.repositories.gear import GearRepository
from trailforge.repositories.users import UserRepository
from trailforge.schemas.gear import (
    GearCatalogCreate,
    GearCatalogResponse,
    GearCheckResponse,
    GearCheckUpsert,
    GearInventoryCreate,
    GearInventoryResponse,
    GearLoanCreate,
    GearLoanResponse,
    GearLoanReturn,
    GearRequirementCreate,
    GearRequirementResponse,
    InventoryAdjustment,
    MissingGearItem,
    MissingGearReport,
)
from trailforge.services.base import ServiceBase


class GearService(ServiceBase):
    def __init__(self, session: Session) -> None:
        super().__init__(session)
        self.gear = GearRepository(session)
        self.users = UserRepository(session)
        self.expeditions = ExpeditionRepository(session)

    def create_catalog(self, data: GearCatalogCreate, *, actor_id: int) -> GearCatalogResponse:
        if self.gear.get_catalog_by_sku(data.sku) is not None:
            raise ConflictError("gear SKU already exists", context={"sku": data.sku})
        catalog = GearCatalog(**data.model_dump())
        self.session.add(catalog)
        self.session.flush()
        self.audit(
            actor_id=actor_id,
            entity_type="gear_catalog",
            entity_id=catalog.id,
            action=AuditAction.CREATED,
            after=self.snapshot(catalog),
        )
        return GearCatalogResponse.model_validate(catalog)

    def list_catalog(self, category: str | None = None) -> list[GearCatalogResponse]:
        return [
            GearCatalogResponse.model_validate(item) for item in self.gear.list_catalog(category)
        ]

    def create_inventory(self, data: GearInventoryCreate) -> GearInventoryResponse:
        scope = "gear:inventory:create"
        prior = self.find_idempotent(scope=scope, key=data.idempotency_key, payload=data)
        if prior is not None:
            inventory = self.gear.get_inventory(prior.resource_id)
            if inventory is None:
                raise ConflictError("idempotency record references missing inventory")
            return GearInventoryResponse.model_validate(inventory)
        catalog = self.gear.get_catalog(data.catalog_id)
        if catalog is None:
            raise NotFoundError(f"GearCatalog {data.catalog_id} was not found")
        self.users.require(data.actor_id)
        if data.owner_id is not None:
            self.users.require(data.owner_id)
        payload = data.model_dump(exclude={"actor_id", "idempotency_key"})
        weight = payload.pop("weight_grams")
        inventory = GearInventory(
            **payload,
            weight_grams=catalog.default_weight_grams if weight is None else weight,
            quantity_available=data.quantity_total,
        )
        try:
            with self.session.begin_nested():
                self.session.add(inventory)
                self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("inventory already exists for this owner and catalog item") from exc
        movement = InventoryMovement(
            inventory_id=inventory.id,
            movement_type=InventoryMovementType.INITIAL,
            quantity_delta=inventory.quantity_total,
            quantity_after=inventory.quantity_available,
            reference_type="inventory",
            reference_id=inventory.id,
            reason="Initial inventory",
            actor_id=data.actor_id,
        )
        self.session.add(movement)
        self.session.flush()
        response = GearInventoryResponse.model_validate(inventory)
        self.save_idempotent(
            scope=scope,
            key=data.idempotency_key,
            payload=data,
            resource_type="gear_inventory",
            resource_id=inventory.id,
            response=response.model_dump(mode="json"),
        )
        self.audit(
            actor_id=data.actor_id,
            entity_type="gear_inventory",
            entity_id=inventory.id,
            action=AuditAction.CREATED,
            after=self.snapshot(inventory),
            correlation_id=data.idempotency_key,
        )
        return response

    def adjust_inventory(
        self, inventory_id: int, data: InventoryAdjustment
    ) -> GearInventoryResponse:
        scope = f"gear:inventory:{inventory_id}:adjust"
        prior = self.find_idempotent(scope=scope, key=data.idempotency_key, payload=data)
        if prior is not None:
            inventory = self.gear.get_inventory(inventory_id)
            if inventory is None:
                raise NotFoundError(f"GearInventory {inventory_id} was not found")
            return GearInventoryResponse.model_validate(inventory)
        inventory = self.gear.get_inventory(inventory_id, for_update=True)
        if inventory is None:
            raise NotFoundError(f"GearInventory {inventory_id} was not found")
        apply_version(inventory, data.expected_version)
        new_total = inventory.quantity_total + data.quantity_delta
        new_available = inventory.quantity_available + data.quantity_delta
        if new_total < 0 or new_available < 0:
            raise InventoryError(
                "adjustment would make inventory negative",
                context={
                    "quantity_total": inventory.quantity_total,
                    "quantity_available": inventory.quantity_available,
                    "delta": data.quantity_delta,
                },
            )
        inventory.quantity_total = new_total
        inventory.quantity_available = new_available
        movement = InventoryMovement(
            inventory_id=inventory.id,
            movement_type=InventoryMovementType.ADJUSTMENT,
            quantity_delta=data.quantity_delta,
            quantity_after=new_available,
            reference_type="manual_adjustment",
            reference_id=None,
            reason=data.reason,
            actor_id=data.actor_id,
        )
        self.session.add(movement)
        self.session.flush()
        response = GearInventoryResponse.model_validate(inventory)
        self.save_idempotent(
            scope=scope,
            key=data.idempotency_key,
            payload=data,
            resource_type="gear_inventory",
            resource_id=inventory.id,
            response=response.model_dump(mode="json"),
        )
        self.audit(
            actor_id=data.actor_id,
            entity_type="gear_inventory",
            entity_id=inventory.id,
            action=AuditAction.INVENTORY_CHANGED,
            before={"quantity_total": new_total - data.quantity_delta},
            after={"quantity_total": new_total, "quantity_available": new_available},
            context={"reason": data.reason, "delta": data.quantity_delta},
            correlation_id=data.idempotency_key,
        )
        return response

    def loan(self, data: GearLoanCreate) -> GearLoanResponse:
        scope = f"gear:inventory:{data.inventory_id}:loan"
        prior = self.find_idempotent(scope=scope, key=data.idempotency_key, payload=data)
        if prior is not None:
            loan = self.gear.get_loan(prior.resource_id)
            if loan is None:
                raise ConflictError("idempotency record references missing loan")
            return GearLoanResponse.model_validate(loan)
        inventory = self.gear.get_inventory(data.inventory_id, for_update=True)
        if inventory is None:
            raise NotFoundError(f"GearInventory {data.inventory_id} was not found")
        self.users.require(data.borrower_id)
        self.users.require(data.actor_id)
        if data.expedition_id is not None and self.expeditions.get(data.expedition_id) is None:
            raise NotFoundError(f"Expedition {data.expedition_id} was not found")
        if inventory.condition in {GearCondition.DAMAGED, GearCondition.RETIRED}:
            raise InventoryError("damaged or retired gear cannot be loaned")
        if inventory.quantity_available < data.quantity:
            raise InventoryError(
                "insufficient available inventory",
                context={"available": inventory.quantity_available, "requested": data.quantity},
            )
        if self.gear.active_loan(inventory.id, data.borrower_id) is not None:
            raise ConflictError("borrower already has an active loan for this inventory")
        loan = GearLoan(
            inventory_id=inventory.id,
            borrower_id=data.borrower_id,
            expedition_id=data.expedition_id,
            quantity=data.quantity,
            loaned_at=data.loaned_at,
            due_at=data.due_at,
            condition_out=inventory.condition,
            notes=data.notes,
        )
        self.session.add(loan)
        inventory.quantity_available -= data.quantity
        apply_version(inventory, None)
        self.session.flush()
        movement = InventoryMovement(
            inventory_id=inventory.id,
            movement_type=InventoryMovementType.LOAN_OUT,
            quantity_delta=-data.quantity,
            quantity_after=inventory.quantity_available,
            reference_type="gear_loan",
            reference_id=loan.id,
            reason="Gear loaned",
            actor_id=data.actor_id,
        )
        self.session.add(movement)
        response = GearLoanResponse.model_validate(loan)
        self.save_idempotent(
            scope=scope,
            key=data.idempotency_key,
            payload=data,
            resource_type="gear_loan",
            resource_id=loan.id,
            response=response.model_dump(mode="json"),
        )
        self.audit(
            actor_id=data.actor_id,
            entity_type="gear_loan",
            entity_id=loan.id,
            action=AuditAction.LOANED,
            after=self.snapshot(loan),
            context={"inventory_available": inventory.quantity_available},
            correlation_id=data.idempotency_key,
        )
        return response

    def return_loan(self, loan_id: int, data: GearLoanReturn) -> GearLoanResponse:
        scope = f"gear:loan:{loan_id}:return"
        prior = self.find_idempotent(scope=scope, key=data.idempotency_key, payload=data)
        if prior is not None:
            loan = self.gear.get_loan(loan_id)
            if loan is None:
                raise NotFoundError(f"GearLoan {loan_id} was not found")
            return GearLoanResponse.model_validate(loan)
        loan = self.gear.get_loan(loan_id, for_update=True)
        if loan is None:
            raise NotFoundError(f"GearLoan {loan_id} was not found")
        inventory = self.gear.get_inventory(loan.inventory_id, for_update=True)
        if inventory is None:
            raise NotFoundError("loan inventory was not found")
        if loan.status not in {LoanStatus.ACTIVE, LoanStatus.OVERDUE}:
            raise ConflictError("loan is not active")
        remaining = loan.quantity - loan.returned_quantity
        if data.quantity > remaining:
            raise InventoryError(
                "return quantity exceeds outstanding loan quantity",
                context={"outstanding": remaining, "returned": data.quantity},
            )
        if data.returned_at < loan.loaned_at:
            raise ValidationError("returned_at cannot be before loaned_at")
        apply_version(loan, data.expected_version)
        apply_version(inventory, None)
        loan.returned_quantity += data.quantity
        loan.condition_in = data.condition_in
        loan.notes = f"{loan.notes}\nReturn: {data.notes}".strip()
        inventory.quantity_available += data.quantity
        if data.condition_in == GearCondition.DAMAGED:
            inventory.condition = GearCondition.DAMAGED
        if loan.returned_quantity == loan.quantity:
            loan.status = LoanStatus.RETURNED
            loan.returned_at = data.returned_at
        self.session.flush()
        movement = InventoryMovement(
            inventory_id=inventory.id,
            movement_type=InventoryMovementType.RETURN_IN,
            quantity_delta=data.quantity,
            quantity_after=inventory.quantity_available,
            reference_type="gear_loan",
            reference_id=loan.id,
            reason=data.notes or "Gear returned",
            actor_id=data.actor_id,
        )
        self.session.add(movement)
        response = GearLoanResponse.model_validate(loan)
        self.save_idempotent(
            scope=scope,
            key=data.idempotency_key,
            payload=data,
            resource_type="gear_loan",
            resource_id=loan.id,
            response=response.model_dump(mode="json"),
        )
        self.audit(
            actor_id=data.actor_id,
            entity_type="gear_loan",
            entity_id=loan.id,
            action=AuditAction.RETURNED,
            after=self.snapshot(loan),
            context={"returned_quantity": data.quantity},
            correlation_id=data.idempotency_key,
        )
        return response

    def add_requirement(
        self, expedition_id: int, data: GearRequirementCreate, *, actor_id: int
    ) -> GearRequirementResponse:
        if self.expeditions.get(expedition_id) is None:
            raise NotFoundError(f"Expedition {expedition_id} was not found")
        if self.gear.get_catalog(data.catalog_id) is None:
            raise NotFoundError(f"GearCatalog {data.catalog_id} was not found")
        requirement = self.gear.get_requirement(expedition_id, data.catalog_id)
        action = AuditAction.CREATED
        if requirement is None:
            requirement = ActivityGearRequirement(expedition_id=expedition_id, **data.model_dump())
            self.session.add(requirement)
        else:
            action = AuditAction.UPDATED
            for field, value in data.model_dump().items():
                setattr(requirement, field, value)
        self.session.flush()
        self.audit(
            actor_id=actor_id,
            entity_type="activity_gear_requirement",
            entity_id=requirement.id,
            action=action,
            after=self.snapshot(requirement),
        )
        return GearRequirementResponse.model_validate(requirement)

    def upsert_check(self, expedition_id: int, data: GearCheckUpsert) -> GearCheckResponse:
        if self.expeditions.get(expedition_id) is None:
            raise NotFoundError(f"Expedition {expedition_id} was not found")
        self.users.require(data.user_id)
        if data.verified_by is not None:
            self.users.require(data.verified_by)
        if self.gear.get_catalog(data.catalog_id) is None:
            raise NotFoundError(f"GearCatalog {data.catalog_id} was not found")
        check = self.gear.get_gear_check(expedition_id, data.user_id, data.catalog_id)
        payload = data.model_dump()
        if check is None:
            check = ActivityGearCheck(expedition_id=expedition_id, **payload)
            self.session.add(check)
        else:
            for field, value in payload.items():
                setattr(check, field, value)
        check.verified_at = utc_now() if data.status == ChecklistStatus.VERIFIED else None
        self.session.flush()
        return GearCheckResponse.model_validate(check)

    def missing_report(self, expedition_id: int) -> MissingGearReport:
        if self.expeditions.get(expedition_id) is None:
            raise NotFoundError(f"Expedition {expedition_id} was not found")
        participant_count = self.gear.participant_count(expedition_id)
        checks = self.gear.gear_checks(expedition_id)
        packed: dict[int, int] = {}
        accepted = {ChecklistStatus.PACKED, ChecklistStatus.VERIFIED}
        for check in checks:
            if check.status in accepted:
                packed[check.catalog_id] = packed.get(check.catalog_id, 0) + check.quantity
        missing_items: list[MissingGearItem] = []
        for requirement in self.gear.requirements(expedition_id):
            required = (
                requirement.quantity_for_group + requirement.quantity_per_person * participant_count
            )
            packed_quantity = packed.get(requirement.catalog_id, 0)
            missing = max(required - packed_quantity, 0)
            if missing > 0 and requirement.mandatory:
                catalog = self.gear.get_catalog(requirement.catalog_id)
                if catalog is None:
                    continue
                missing_items.append(
                    MissingGearItem(
                        catalog_id=catalog.id,
                        sku=catalog.sku,
                        name=catalog.name,
                        mandatory=requirement.mandatory,
                        required_quantity=required,
                        packed_quantity=packed_quantity,
                        missing_quantity=missing,
                    )
                )
        return MissingGearReport(
            expedition_id=expedition_id,
            participant_count=participant_count,
            checked_at=utc_now(),
            is_ready=not missing_items,
            missing_items=missing_items,
        )

    def mark_overdue_loans(self, now: datetime | None = None) -> int:
        current = now or utc_now()
        changed = 0
        for loan in self.gear.list_loans(status=LoanStatus.ACTIVE):
            if loan.due_at < current:
                loan.status = LoanStatus.OVERDUE
                apply_version(loan, None)
                changed += 1
        self.session.flush()
        return changed
