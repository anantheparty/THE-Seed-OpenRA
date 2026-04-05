"""Enumerations for the data model layer."""

from enum import Enum


class TaskKind(str, Enum):
    INSTANT = "instant"
    MANAGED = "managed"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    ABORTED = "aborted"


class JobStatus(str, Enum):
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABORTED = "aborted"


class ResourceKind(str, Enum):
    ACTOR = "actor"
    PRODUCTION_QUEUE = "production_queue"


class ConstraintEnforcement(str, Enum):
    CLAMP = "clamp"
    ESCALATE = "escalate"


class SignalKind(str, Enum):
    PROGRESS = "progress"
    RISK_ALERT = "risk_alert"
    BLOCKED = "blocked"
    DECISION_REQUEST = "decision_request"
    RESOURCE_LOST = "resource_lost"
    TARGET_FOUND = "target_found"
    TASK_COMPLETE = "task_complete"
    CONSTRAINT_VIOLATED = "constraint_violated"


class EventType(str, Enum):
    GAME_RESET = "GAME_RESET"
    UNIT_DIED = "UNIT_DIED"
    UNIT_DAMAGED = "UNIT_DAMAGED"
    ENEMY_DISCOVERED = "ENEMY_DISCOVERED"
    STRUCTURE_LOST = "STRUCTURE_LOST"
    BASE_UNDER_ATTACK = "BASE_UNDER_ATTACK"
    PRODUCTION_COMPLETE = "PRODUCTION_COMPLETE"
    ENEMY_EXPANSION = "ENEMY_EXPANSION"
    FRONTLINE_WEAK = "FRONTLINE_WEAK"
    ECONOMY_SURPLUS = "ECONOMY_SURPLUS"
    UNIT_REQUEST_UNFULFILLED = "UNIT_REQUEST_UNFULFILLED"
    UNIT_ASSIGNED = "UNIT_ASSIGNED"
    PLAYER_MESSAGE = "PLAYER_MESSAGE"


class TaskMessageType(str, Enum):
    TASK_INFO = "task_info"
    TASK_WARNING = "task_warning"
    TASK_QUESTION = "task_question"
    TASK_COMPLETE_REPORT = "task_complete_report"


class EngagementMode(str, Enum):
    ASSAULT = "assault"
    HARASS = "harass"
    HOLD = "hold"
    SURROUND = "surround"


class MoveMode(str, Enum):
    MOVE = "move"
    ATTACK_MOVE = "attack_move"
    RETREAT = "retreat"


class ActorOwner(str, Enum):
    SELF = "self"
    ENEMY = "enemy"
    NEUTRAL = "neutral"


class ActorCategory(str, Enum):
    INFANTRY = "infantry"
    VEHICLE = "vehicle"
    BUILDING = "building"
    HARVESTER = "harvester"
    MCV = "mcv"


class Mobility(str, Enum):
    FAST = "fast"
    MEDIUM = "medium"
    SLOW = "slow"
    STATIC = "static"
