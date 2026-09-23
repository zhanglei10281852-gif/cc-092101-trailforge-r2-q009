from __future__ import annotations

from enum import StrEnum


class FitnessLevel(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class TrainingType(StrEnum):
    STRENGTH = "strength"
    ENDURANCE = "endurance"
    LOADED_WALK = "loaded_walk"
    MOBILITY = "mobility"
    RECOVERY = "recovery"
    SKILLS = "skills"


class PlanStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class SessionStatus(StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class Difficulty(StrEnum):
    EASY = "easy"
    MODERATE = "moderate"
    HARD = "hard"
    EXTREME = "extreme"


class PointType(StrEnum):
    WAYPOINT = "waypoint"
    SUMMIT = "summit"
    TRAILHEAD = "trailhead"
    SHELTER = "shelter"
    WATER = "water"
    FOOD = "food"
    EXIT = "exit"
    HAZARD = "hazard"


class RiskLevel(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class ActivityStatus(StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    ASSEMBLING = "assembling"
    DEPARTED = "departed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class RegistrationStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    WAITLISTED = "waitlisted"
    WITHDRAWN = "withdrawn"
    REJECTED = "rejected"


class TeamRole(StrEnum):
    LEADER = "leader"
    NAVIGATOR = "navigator"
    SAFETY = "safety"
    MEDIC = "medic"
    MEMBER = "member"


class GearOwnership(StrEnum):
    PERSONAL = "personal"
    CLUB = "club"


class GearCondition(StrEnum):
    NEW = "new"
    GOOD = "good"
    FAIR = "fair"
    DAMAGED = "damaged"
    RETIRED = "retired"


class LoanStatus(StrEnum):
    ACTIVE = "active"
    RETURNED = "returned"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


class ChecklistStatus(StrEnum):
    REQUIRED = "required"
    PACKED = "packed"
    VERIFIED = "verified"
    MISSING = "missing"
    WAIVED = "waived"


class CheckInType(StrEnum):
    ASSEMBLY = "assembly"
    DEPARTURE = "departure"
    ROUTINE = "routine"
    WAYPOINT = "waypoint"
    SAFE_RETURN = "safe_return"


class EmergencyStatus(StrEnum):
    OPEN = "open"
    MONITORING = "monitoring"
    RESOLVED = "resolved"
    FALSE_ALARM = "false_alarm"


class EmergencyType(StrEnum):
    INJURY = "injury"
    ILLNESS = "illness"
    LOST_PERSON = "lost_person"
    WEATHER = "weather"
    EQUIPMENT = "equipment"
    DELAY = "delay"
    OTHER = "other"


class InventoryMovementType(StrEnum):
    INITIAL = "initial"
    ACQUIRE = "acquire"
    LOAN_OUT = "loan_out"
    RETURN_IN = "return_in"
    ADJUSTMENT = "adjustment"
    RETIRE = "retire"


class AuditAction(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    STATUS_CHANGED = "status_changed"
    REGISTERED = "registered"
    WITHDRAWN = "withdrawn"
    CHECKED_IN = "checked_in"
    INVENTORY_CHANGED = "inventory_changed"
    LOANED = "loaned"
    RETURNED = "returned"
    RISK_RECORDED = "risk_recorded"
    EMERGENCY_RECORDED = "emergency_recorded"


PLAN_TRANSITIONS: dict[PlanStatus, set[PlanStatus]] = {
    PlanStatus.DRAFT: {PlanStatus.ACTIVE, PlanStatus.CANCELLED},
    PlanStatus.ACTIVE: {PlanStatus.PAUSED, PlanStatus.COMPLETED, PlanStatus.CANCELLED},
    PlanStatus.PAUSED: {PlanStatus.ACTIVE, PlanStatus.CANCELLED},
    PlanStatus.COMPLETED: set(),
    PlanStatus.CANCELLED: set(),
}


SESSION_TRANSITIONS: dict[SessionStatus, set[SessionStatus]] = {
    SessionStatus.PLANNED: {
        SessionStatus.IN_PROGRESS,
        SessionStatus.SKIPPED,
        SessionStatus.CANCELLED,
    },
    SessionStatus.IN_PROGRESS: {SessionStatus.COMPLETED, SessionStatus.CANCELLED},
    SessionStatus.COMPLETED: set(),
    SessionStatus.SKIPPED: set(),
    SessionStatus.CANCELLED: set(),
}


ACTIVITY_TRANSITIONS: dict[ActivityStatus, set[ActivityStatus]] = {
    ActivityStatus.DRAFT: {ActivityStatus.OPEN, ActivityStatus.CANCELLED},
    ActivityStatus.OPEN: {ActivityStatus.ASSEMBLING, ActivityStatus.CANCELLED},
    ActivityStatus.ASSEMBLING: {ActivityStatus.DEPARTED, ActivityStatus.CANCELLED},
    ActivityStatus.DEPARTED: {ActivityStatus.IN_PROGRESS, ActivityStatus.CANCELLED},
    ActivityStatus.IN_PROGRESS: {ActivityStatus.COMPLETED, ActivityStatus.CANCELLED},
    ActivityStatus.COMPLETED: set(),
    ActivityStatus.CANCELLED: set(),
}
