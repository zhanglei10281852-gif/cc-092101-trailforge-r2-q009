from __future__ import annotations

from sqlalchemy.orm import Session

from trailforge.database.base import utc_now
from trailforge.domain.enums import (
    ACTIVITY_TRANSITIONS,
    ActivityStatus,
    AuditAction,
    RegistrationStatus,
    TeamRole,
)
from trailforge.errors import (
    CapacityError,
    ConflictError,
    InvalidStateError,
    NotFoundError,
    ValidationError,
)
from trailforge.models.activities import Expedition, ExpeditionRegistration
from trailforge.repositories.activities import ExpeditionRepository
from trailforge.repositories.base import apply_version
from trailforge.repositories.routes import RouteRepository
from trailforge.repositories.users import UserRepository
from trailforge.schemas.activities import (
    ActivityStateChange,
    ExpeditionCreate,
    ExpeditionFilter,
    ExpeditionResponse,
    ExpeditionRoster,
    ExpeditionRosterEntry,
    ExpeditionUpdate,
    RegistrationCreate,
    RegistrationResponse,
    WithdrawalRequest,
)
from trailforge.schemas.common import Page
from trailforge.services.base import ServiceBase


class ExpeditionService(ServiceBase):
    def __init__(self, session: Session) -> None:
        super().__init__(session)
        self.expeditions = ExpeditionRepository(session)
        self.users = UserRepository(session)
        self.routes = RouteRepository(session)

    def create(self, data: ExpeditionCreate) -> ExpeditionResponse:
        organizer = self.users.require(data.organizer_id)
        route = self.routes.get_detail(data.route_id)
        if route is None:
            raise NotFoundError(f"TrailRoute {data.route_id} was not found")
        if not route.is_published:
            raise ValidationError("an expedition requires a published route")
        expedition = Expedition(**data.model_dump())
        self.session.add(expedition)
        self.session.flush()
        organizer_registration = ExpeditionRegistration(
            expedition_id=expedition.id,
            user_id=organizer.id,
            role=TeamRole.LEADER,
            status=RegistrationStatus.CONFIRMED,
            registered_at=utc_now(),
            notes="Organizer",
        )
        self.session.add(organizer_registration)
        self.session.flush()
        self.audit(
            actor_id=organizer.id,
            entity_type="expedition",
            entity_id=expedition.id,
            action=AuditAction.CREATED,
            after=self.snapshot(expedition),
            context={"route_id": route.id, "organizer_registration_id": organizer_registration.id},
        )
        return ExpeditionResponse.model_validate(expedition)

    def get(self, expedition_id: int) -> ExpeditionResponse:
        expedition = self.expeditions.get_detail(expedition_id)
        if expedition is None:
            raise NotFoundError(f"Expedition {expedition_id} was not found")
        return ExpeditionResponse.model_validate(expedition)

    def list(self, filters: ExpeditionFilter) -> Page[ExpeditionResponse]:
        result = self.expeditions.list_expeditions(filters)
        return Page[ExpeditionResponse].build(
            [ExpeditionResponse.model_validate(item) for item in result.items],
            page=result.page,
            page_size=result.page_size,
            total=result.total,
        )

    def update(
        self, expedition_id: int, data: ExpeditionUpdate, *, actor_id: int
    ) -> ExpeditionResponse:
        expedition = self.expeditions.get_detail(expedition_id, for_update=True)
        if expedition is None:
            raise NotFoundError(f"Expedition {expedition_id} was not found")
        if ActivityStatus(expedition.status) not in {ActivityStatus.DRAFT, ActivityStatus.OPEN}:
            raise InvalidStateError("only draft or open expeditions can be edited")
        apply_version(expedition, data.expected_version)
        before = self.snapshot(expedition)
        changes = data.model_dump(exclude_unset=True, exclude={"expected_version"})
        start_at = changes.get("start_at", expedition.start_at)
        end_at = changes.get("end_at", expedition.end_at)
        meeting_at = changes.get("meeting_at", expedition.meeting_at)
        deadline = changes.get("registration_deadline", expedition.registration_deadline)
        capacity = changes.get("capacity", expedition.capacity)
        if end_at <= start_at:
            raise ValidationError("end_at must be later than start_at")
        if meeting_at > start_at or deadline > start_at:
            raise ValidationError("meeting and registration deadline cannot be after start")
        confirmed = self.expeditions.confirmed_count(expedition.id)
        if capacity < confirmed:
            raise CapacityError(
                "capacity cannot be lower than confirmed registrations",
                context={"confirmed": confirmed},
            )
        for field, value in changes.items():
            setattr(expedition, field, value)
        self.session.flush()
        self.audit(
            actor_id=actor_id,
            entity_type="expedition",
            entity_id=expedition.id,
            action=AuditAction.UPDATED,
            before=before,
            after=self.snapshot(expedition),
        )
        return ExpeditionResponse.model_validate(expedition)

    def change_status(self, expedition_id: int, data: ActivityStateChange) -> ExpeditionResponse:
        expedition = self.expeditions.get_detail(expedition_id, for_update=True)
        if expedition is None:
            raise NotFoundError(f"Expedition {expedition_id} was not found")
        self.users.require(data.actor_id)
        current = ActivityStatus(expedition.status)
        target = data.target_status
        if target not in ACTIVITY_TRANSITIONS[current]:
            raise InvalidStateError(
                f"cannot change expedition from {current} to {target}",
                context={"allowed": sorted(ACTIVITY_TRANSITIONS[current])},
            )
        if target == ActivityStatus.OPEN and expedition.registration_deadline <= utc_now():
            raise ValidationError("cannot open registration after its deadline")
        if (
            target == ActivityStatus.ASSEMBLING
            and self.expeditions.confirmed_count(expedition.id) == 0
        ):
            raise ValidationError("cannot assemble an expedition without confirmed members")
        if target == ActivityStatus.CANCELLED and not data.reason.strip():
            raise ValidationError("cancellation requires a reason")
        apply_version(expedition, data.expected_version)
        expedition.status = target
        if target == ActivityStatus.CANCELLED:
            expedition.cancellation_reason = data.reason.strip()
        self.session.flush()
        self.audit(
            actor_id=data.actor_id,
            entity_type="expedition",
            entity_id=expedition.id,
            action=AuditAction.STATUS_CHANGED,
            before={"status": current.value},
            after={"status": target.value},
            context={"reason": data.reason},
        )
        return ExpeditionResponse.model_validate(expedition)

    def register(self, expedition_id: int, data: RegistrationCreate) -> RegistrationResponse:
        scope = f"expedition:{expedition_id}:register"
        prior = self.find_idempotent(scope=scope, key=data.idempotency_key, payload=data)
        if prior is not None:
            registration = self.session.get(ExpeditionRegistration, prior.resource_id)
            if registration is None:
                raise ConflictError("idempotency record references a missing registration")
            return RegistrationResponse.model_validate(registration)
        expedition = self.expeditions.get_detail(expedition_id, for_update=True)
        if expedition is None:
            raise NotFoundError(f"Expedition {expedition_id} was not found")
        user = self.users.require(data.user_id)
        if ActivityStatus(expedition.status) != ActivityStatus.OPEN:
            raise InvalidStateError("registration is only available for open expeditions")
        if utc_now() > expedition.registration_deadline:
            raise ConflictError("registration deadline has passed")
        existing = self.expeditions.get_registration(expedition_id, data.user_id)
        if existing is not None and existing.status != RegistrationStatus.WITHDRAWN:
            raise ConflictError("user is already registered for this expedition")
        conflict = self.expeditions.conflicting_registration(
            data.user_id,
            expedition.start_at,
            expedition.end_at,
            exclude_expedition_id=expedition.id,
        )
        if conflict is not None:
            raise ConflictError(
                "user has another expedition during this time",
                context={"conflicting_expedition_id": conflict.expedition_id},
            )
        fitness_rank = self.users.fitness_rank(user.id)
        if fitness_rank is None:
            raise ValidationError("a sport profile is required before registration")
        if fitness_rank < expedition.minimum_fitness_level:
            raise ValidationError(
                "user fitness level does not meet expedition requirement",
                context={
                    "required": expedition.minimum_fitness_level,
                    "actual": fitness_rank,
                },
            )
        confirmed = self.expeditions.confirmed_count(expedition.id)
        status = (
            RegistrationStatus.CONFIRMED
            if confirmed < expedition.capacity
            else RegistrationStatus.WAITLISTED
        )
        if existing is None:
            registration = ExpeditionRegistration(
                expedition_id=expedition.id,
                user_id=user.id,
                role=data.role,
                status=status,
                registered_at=utc_now(),
                notes=data.notes,
            )
            self.session.add(registration)
        else:
            registration = existing
            registration.role = data.role
            registration.status = status
            registration.registered_at = utc_now()
            registration.withdrawn_at = None
            registration.notes = data.notes
            apply_version(registration, None)
        self.session.flush()
        response = RegistrationResponse.model_validate(registration)
        self.save_idempotent(
            scope=scope,
            key=data.idempotency_key,
            payload=data,
            resource_type="expedition_registration",
            resource_id=registration.id,
            response=response.model_dump(mode="json"),
        )
        self.audit(
            actor_id=user.id,
            entity_type="expedition_registration",
            entity_id=registration.id,
            action=AuditAction.REGISTERED,
            after=self.snapshot(registration),
            context={"expedition_id": expedition.id, "capacity_status": status.value},
            correlation_id=data.idempotency_key,
        )
        return response

    def withdraw(self, expedition_id: int, data: WithdrawalRequest) -> RegistrationResponse:
        scope = f"expedition:{expedition_id}:withdraw"
        prior = self.find_idempotent(scope=scope, key=data.idempotency_key, payload=data)
        if prior is not None:
            registration = self.session.get(ExpeditionRegistration, prior.resource_id)
            if registration is None:
                raise ConflictError("idempotency record references a missing registration")
            return RegistrationResponse.model_validate(registration)
        expedition = self.expeditions.get_detail(expedition_id, for_update=True)
        if expedition is None:
            raise NotFoundError(f"Expedition {expedition_id} was not found")
        if ActivityStatus(expedition.status) in {
            ActivityStatus.DEPARTED,
            ActivityStatus.IN_PROGRESS,
            ActivityStatus.COMPLETED,
        }:
            raise InvalidStateError("registration cannot be withdrawn after departure")
        registration = self.expeditions.get_registration(
            expedition_id, data.user_id, for_update=True
        )
        if registration is None:
            raise NotFoundError("registration was not found")
        if registration.role == TeamRole.LEADER and data.user_id == expedition.organizer_id:
            raise ConflictError("organizer cannot withdraw; cancel or transfer the expedition")
        if registration.status == RegistrationStatus.WITHDRAWN:
            raise ConflictError("registration is already withdrawn")
        registration.status = RegistrationStatus.WITHDRAWN
        registration.withdrawn_at = utc_now()
        registration.notes = f"{registration.notes}\nWithdrawal: {data.reason}".strip()
        apply_version(registration, None)
        self.session.flush()
        self._promote_waitlist(expedition.id)
        response = RegistrationResponse.model_validate(registration)
        self.save_idempotent(
            scope=scope,
            key=data.idempotency_key,
            payload=data,
            resource_type="expedition_registration",
            resource_id=registration.id,
            response=response.model_dump(mode="json"),
        )
        self.audit(
            actor_id=data.user_id,
            entity_type="expedition_registration",
            entity_id=registration.id,
            action=AuditAction.WITHDRAWN,
            before={"status": RegistrationStatus.CONFIRMED.value},
            after={"status": RegistrationStatus.WITHDRAWN.value},
            context={"reason": data.reason},
            correlation_id=data.idempotency_key,
        )
        return response

    def roster(self, expedition_id: int) -> ExpeditionRoster:
        expedition = self.expeditions.get_detail(expedition_id)
        if expedition is None:
            raise NotFoundError(f"Expedition {expedition_id} was not found")
        active = [
            item
            for item in expedition.registrations
            if item.status in {RegistrationStatus.CONFIRMED, RegistrationStatus.WAITLISTED}
        ]
        entries: list[ExpeditionRosterEntry] = []
        for registration in sorted(active, key=lambda item: (item.registered_at, item.id)):
            user = self.users.require(registration.user_id)
            profile = self.users.get_sport_profile(user.id)
            entries.append(
                ExpeditionRosterEntry(
                    registration_id=registration.id,
                    user_id=user.id,
                    display_name=user.display_name,
                    role=registration.role,
                    status=registration.status,
                    registered_at=registration.registered_at,
                    fitness_level=profile.fitness_level if profile else None,
                    has_emergency_contact=self.users.contact_count(user.id) > 0,
                    active_health_restrictions=self.users.active_restriction_count(user.id),
                )
            )
        confirmed = sum(item.status == RegistrationStatus.CONFIRMED for item in active)
        waitlisted = sum(item.status == RegistrationStatus.WAITLISTED for item in active)
        return ExpeditionRoster(
            expedition_id=expedition.id,
            capacity=expedition.capacity,
            confirmed_count=confirmed,
            waitlisted_count=waitlisted,
            available_places=max(expedition.capacity - confirmed, 0),
            members=entries,
        )

    def _promote_waitlist(self, expedition_id: int) -> None:
        expedition = self.expeditions.get_detail(expedition_id, for_update=True)
        if expedition is None:
            return
        confirmed = self.expeditions.confirmed_count(expedition_id)
        available = expedition.capacity - confirmed
        if available <= 0:
            return
        waiting = sorted(
            (
                item
                for item in expedition.registrations
                if item.status == RegistrationStatus.WAITLISTED
            ),
            key=lambda item: (item.registered_at, item.id),
        )
        for registration in waiting[:available]:
            registration.status = RegistrationStatus.CONFIRMED
            apply_version(registration, None)
