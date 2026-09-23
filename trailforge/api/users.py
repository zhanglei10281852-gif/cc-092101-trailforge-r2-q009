from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from trailforge.api.dependencies import get_session
from trailforge.domain.enums import FitnessLevel
from trailforge.schemas.common import Page
from trailforge.schemas.users import (
    EmergencyContactCreate,
    EmergencyContactResponse,
    EmergencyContactUpdate,
    HealthRestrictionCreate,
    HealthRestrictionResponse,
    HealthRestrictionUpdate,
    SportProfileResponse,
    SportProfileUpsert,
    UserCreate,
    UserFilter,
    UserProfileResponse,
    UserResponse,
    UserUpdate,
)
from trailforge.services.users import UserService

router = APIRouter(prefix="/users", tags=["users"])
SessionDep = Annotated[Session, Depends(get_session)]


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(data: UserCreate, session: SessionDep) -> UserResponse:
    return UserService(session).create(data)


@router.get("", response_model=Page[UserResponse])
def list_users(
    session: SessionDep,
    search: str | None = None,
    is_active: bool | None = None,
    fitness_level: FitnessLevel | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    sort: str = "created_at",
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
) -> Page[UserResponse]:
    filters = UserFilter(
        search=search,
        is_active=is_active,
        fitness_level=fitness_level,
        page=page,
        page_size=page_size,
        sort=sort,
        direction=direction,
    )
    return UserService(session).list(filters)


@router.get("/{user_id}", response_model=UserProfileResponse)
def get_user(user_id: int, session: SessionDep) -> UserProfileResponse:
    return UserService(session).get(user_id)


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    data: UserUpdate,
    session: SessionDep,
    actor_id: int = Query(gt=0),
) -> UserResponse:
    return UserService(session).update(user_id, data, actor_id=actor_id)


@router.put("/{user_id}/sport-profile", response_model=SportProfileResponse)
def upsert_sport_profile(
    user_id: int,
    data: SportProfileUpsert,
    session: SessionDep,
    actor_id: int = Query(gt=0),
) -> SportProfileResponse:
    return UserService(session).upsert_profile(user_id, data, actor_id=actor_id)


@router.post(
    "/{user_id}/emergency-contacts",
    response_model=EmergencyContactResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_emergency_contact(
    user_id: int,
    data: EmergencyContactCreate,
    session: SessionDep,
    actor_id: int = Query(gt=0),
) -> EmergencyContactResponse:
    return UserService(session).add_contact(user_id, data, actor_id=actor_id)


@router.patch(
    "/{user_id}/emergency-contacts/{contact_id}",
    response_model=EmergencyContactResponse,
)
def update_emergency_contact(
    user_id: int,
    contact_id: int,
    data: EmergencyContactUpdate,
    session: SessionDep,
    actor_id: int = Query(gt=0),
) -> EmergencyContactResponse:
    return UserService(session).update_contact(user_id, contact_id, data, actor_id=actor_id)


@router.post(
    "/{user_id}/health-restrictions",
    response_model=HealthRestrictionResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_health_restriction(
    user_id: int,
    data: HealthRestrictionCreate,
    session: SessionDep,
    actor_id: int = Query(gt=0),
) -> HealthRestrictionResponse:
    return UserService(session).add_restriction(user_id, data, actor_id=actor_id)


@router.patch(
    "/{user_id}/health-restrictions/{restriction_id}",
    response_model=HealthRestrictionResponse,
)
def update_health_restriction(
    user_id: int,
    restriction_id: int,
    data: HealthRestrictionUpdate,
    session: SessionDep,
    actor_id: int = Query(gt=0),
) -> HealthRestrictionResponse:
    return UserService(session).update_restriction(user_id, restriction_id, data, actor_id=actor_id)
