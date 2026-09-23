from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from trailforge.domain.enums import AuditAction
from trailforge.errors import ConflictError, NotFoundError
from trailforge.models.users import EmergencyContact, HealthRestriction, SportProfile, User
from trailforge.repositories.users import UserRepository
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
from trailforge.services.base import ServiceBase


class UserService(ServiceBase):
    def __init__(self, session: Session) -> None:
        super().__init__(session)
        self.users = UserRepository(session)

    def create(self, data: UserCreate) -> UserResponse:
        if self.users.get_by_email(data.email) is not None:
            raise ConflictError("email is already registered", context={"email": data.email})
        user = User(**data.model_dump())
        try:
            with self.session.begin_nested():
                self.session.add(user)
                self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("user could not be created because a unique value exists") from exc
        self.audit(
            actor_id=user.id,
            entity_type="user",
            entity_id=user.id,
            action=AuditAction.CREATED,
            after=self.snapshot(user, "email", "display_name", "is_active"),
        )
        return UserResponse.model_validate(user)

    def get(self, user_id: int) -> UserProfileResponse:
        user = self.users.get_profile_bundle(user_id)
        if user is None:
            raise NotFoundError(f"User {user_id} was not found")
        return UserProfileResponse(
            user=UserResponse.model_validate(user),
            sport_profile=(
                SportProfileResponse.model_validate(user.profile)
                if user.profile is not None
                else None
            ),
            emergency_contacts=[
                EmergencyContactResponse.model_validate(item) for item in user.emergency_contacts
            ],
            health_restrictions=[
                HealthRestrictionResponse.model_validate(item) for item in user.health_restrictions
            ],
        )

    def list(self, filters: UserFilter) -> Page[UserResponse]:
        result = self.users.list(filters)
        return Page[UserResponse].build(
            [UserResponse.model_validate(item) for item in result.items],
            page=result.page,
            page_size=result.page_size,
            total=result.total,
        )

    def update(self, user_id: int, data: UserUpdate, *, actor_id: int) -> UserResponse:
        user = self.users.require(user_id, for_update=True)
        before = self.snapshot(user, "display_name", "phone", "birth_date", "is_active")
        changes = data.model_dump(exclude_unset=True)
        for field, value in changes.items():
            setattr(user, field, value)
        self.session.flush()
        self.audit(
            actor_id=actor_id,
            entity_type="user",
            entity_id=user.id,
            action=AuditAction.UPDATED,
            before=before,
            after=self.snapshot(user, *changes.keys()),
        )
        return UserResponse.model_validate(user)

    def upsert_profile(
        self, user_id: int, data: SportProfileUpsert, *, actor_id: int
    ) -> SportProfileResponse:
        self.users.require(user_id)
        profile = self.users.get_sport_profile(user_id)
        before: dict = {}
        action = AuditAction.CREATED
        if profile is None:
            profile = SportProfile(user_id=user_id, **data.model_dump())
            self.session.add(profile)
        else:
            before = self.snapshot(profile)
            action = AuditAction.UPDATED
            for field, value in data.model_dump().items():
                setattr(profile, field, value)
        self.session.flush()
        self.audit(
            actor_id=actor_id,
            entity_type="sport_profile",
            entity_id=profile.id,
            action=action,
            before=before,
            after=self.snapshot(profile),
        )
        return SportProfileResponse.model_validate(profile)

    def add_contact(
        self, user_id: int, data: EmergencyContactCreate, *, actor_id: int
    ) -> EmergencyContactResponse:
        self.users.require(user_id)
        contact = EmergencyContact(user_id=user_id, **data.model_dump())
        try:
            with self.session.begin_nested():
                self.session.add(contact)
                self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("contact phone and priority must be unique for a user") from exc
        self.audit(
            actor_id=actor_id,
            entity_type="emergency_contact",
            entity_id=contact.id,
            action=AuditAction.CREATED,
            after=self.snapshot(contact),
        )
        return EmergencyContactResponse.model_validate(contact)

    def update_contact(
        self,
        user_id: int,
        contact_id: int,
        data: EmergencyContactUpdate,
        *,
        actor_id: int,
    ) -> EmergencyContactResponse:
        contact = self.users.get_contact(user_id, contact_id)
        if contact is None:
            raise NotFoundError(f"EmergencyContact {contact_id} was not found")
        before = self.snapshot(contact)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(contact, field, value)
        try:
            with self.session.begin_nested():
                self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("contact phone and priority must be unique for a user") from exc
        self.audit(
            actor_id=actor_id,
            entity_type="emergency_contact",
            entity_id=contact.id,
            action=AuditAction.UPDATED,
            before=before,
            after=self.snapshot(contact),
        )
        return EmergencyContactResponse.model_validate(contact)

    def add_restriction(
        self, user_id: int, data: HealthRestrictionCreate, *, actor_id: int
    ) -> HealthRestrictionResponse:
        self.users.require(user_id)
        restriction = HealthRestriction(user_id=user_id, **data.model_dump())
        try:
            with self.session.begin_nested():
                self.session.add(restriction)
                self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("health restriction name must be unique for a user") from exc
        self.audit(
            actor_id=actor_id,
            entity_type="health_restriction",
            entity_id=restriction.id,
            action=AuditAction.CREATED,
            after=self.snapshot(restriction),
        )
        return HealthRestrictionResponse.model_validate(restriction)

    def update_restriction(
        self,
        user_id: int,
        restriction_id: int,
        data: HealthRestrictionUpdate,
        *,
        actor_id: int,
    ) -> HealthRestrictionResponse:
        restriction = self.users.get_restriction(user_id, restriction_id)
        if restriction is None:
            raise NotFoundError(f"HealthRestriction {restriction_id} was not found")
        before = self.snapshot(restriction)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(restriction, field, value)
        self.session.flush()
        self.audit(
            actor_id=actor_id,
            entity_type="health_restriction",
            entity_id=restriction.id,
            action=AuditAction.UPDATED,
            before=before,
            after=self.snapshot(restriction),
        )
        return HealthRestrictionResponse.model_validate(restriction)
