from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from trailforge.api.dependencies import get_session
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
    MissingGearReport,
)
from trailforge.services.gear import GearService

router = APIRouter(prefix="/gear", tags=["gear"])
SessionDep = Annotated[Session, Depends(get_session)]


@router.post("/catalog", response_model=GearCatalogResponse, status_code=status.HTTP_201_CREATED)
def create_catalog_item(
    data: GearCatalogCreate,
    session: SessionDep,
    actor_id: int = Query(gt=0),
) -> GearCatalogResponse:
    return GearService(session).create_catalog(data, actor_id=actor_id)


@router.get("/catalog", response_model=list[GearCatalogResponse])
def list_catalog(
    session: SessionDep,
    category: str | None = None,
) -> list[GearCatalogResponse]:
    return GearService(session).list_catalog(category)


@router.post(
    "/inventory", response_model=GearInventoryResponse, status_code=status.HTTP_201_CREATED
)
def create_inventory(data: GearInventoryCreate, session: SessionDep) -> GearInventoryResponse:
    return GearService(session).create_inventory(data)


@router.post("/inventory/{inventory_id}/adjust", response_model=GearInventoryResponse)
def adjust_inventory(
    inventory_id: int,
    data: InventoryAdjustment,
    session: SessionDep,
) -> GearInventoryResponse:
    return GearService(session).adjust_inventory(inventory_id, data)


@router.post("/loans", response_model=GearLoanResponse, status_code=status.HTTP_201_CREATED)
def create_loan(data: GearLoanCreate, session: SessionDep) -> GearLoanResponse:
    return GearService(session).loan(data)


@router.post("/loans/{loan_id}/returns", response_model=GearLoanResponse)
def return_loan(loan_id: int, data: GearLoanReturn, session: SessionDep) -> GearLoanResponse:
    return GearService(session).return_loan(loan_id, data)


@router.put(
    "/expeditions/{expedition_id}/requirements",
    response_model=GearRequirementResponse,
)
def upsert_requirement(
    expedition_id: int,
    data: GearRequirementCreate,
    session: SessionDep,
    actor_id: int = Query(gt=0),
) -> GearRequirementResponse:
    return GearService(session).add_requirement(expedition_id, data, actor_id=actor_id)


@router.put(
    "/expeditions/{expedition_id}/checks",
    response_model=GearCheckResponse,
)
def upsert_gear_check(
    expedition_id: int,
    data: GearCheckUpsert,
    session: SessionDep,
) -> GearCheckResponse:
    return GearService(session).upsert_check(expedition_id, data)


@router.get(
    "/expeditions/{expedition_id}/missing",
    response_model=MissingGearReport,
)
def missing_gear(expedition_id: int, session: SessionDep) -> MissingGearReport:
    return GearService(session).missing_report(expedition_id)
