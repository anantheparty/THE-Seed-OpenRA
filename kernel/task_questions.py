"""Task question state helpers for Kernel player-interaction flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from models import PlayerResponse, TaskMessage, TaskMessageType


@dataclass(slots=True)
class PendingQuestion:
    message: TaskMessage
    deadline_at: float
    default_option: str


@dataclass(slots=True)
class QuestionSubmitResult:
    ok: bool
    status: str
    timestamp: float
    message: str = ""
    delivered_response: Optional[PlayerResponse] = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": self.ok,
            "status": self.status,
            "timestamp": self.timestamp,
        }
        if self.message:
            payload["message"] = self.message
        return payload


class PendingQuestionStore:
    """Tracks open/timed-out/closed task questions and response delivery."""

    def __init__(self) -> None:
        self._pending_questions: dict[str, PendingQuestion] = {}
        self._timed_out_questions: set[str] = set()
        self._closed_questions: set[str] = set()

    def list_pending_questions(self) -> list[dict[str, object]]:
        pending = sorted(
            self._pending_questions.values(),
            key=lambda item: (item.message.priority, item.message.timestamp),
            reverse=True,
        )
        return [
            {
                "message_id": item.message.message_id,
                "task_id": item.message.task_id,
                "question": item.message.content,
                "options": list(item.message.options or []),
                "default_option": item.message.default_option,
                "priority": item.message.priority,
                "asked_at": item.message.timestamp,
                "timeout_s": item.message.timeout_s,
                "deadline_at": item.deadline_at,
            }
            for item in pending
        ]

    def register(self, message: TaskMessage) -> None:
        if message.type != TaskMessageType.TASK_QUESTION:
            return
        if message.timeout_s is None or message.default_option is None:
            raise ValueError("task_question requires timeout_s and default_option")
        self._pending_questions[message.message_id] = PendingQuestion(
            message=message,
            deadline_at=message.timestamp + message.timeout_s,
            default_option=message.default_option,
        )
        self._timed_out_questions.discard(message.message_id)
        self._closed_questions.discard(message.message_id)

    def cancel(self, message_id: str) -> bool:
        pending = self._pending_questions.pop(message_id, None)
        if pending is None:
            return False
        self._closed_questions.add(message_id)
        return True

    def close_for_task(self, task_id: str) -> None:
        closed_ids = [
            message_id
            for message_id, pending in self._pending_questions.items()
            if pending.message.task_id == task_id
        ]
        for message_id in closed_ids:
            self._pending_questions.pop(message_id, None)
            self._closed_questions.add(message_id)

    def submit(self, response: PlayerResponse, timestamp: float) -> QuestionSubmitResult:
        pending = self._pending_questions.get(response.message_id)
        if pending is None:
            if response.message_id in self._timed_out_questions:
                return QuestionSubmitResult(
                    ok=False,
                    status="timed_out",
                    message="已按默认处理，如需更改请重新下令",
                    timestamp=timestamp,
                )
            if response.message_id in self._closed_questions:
                return QuestionSubmitResult(
                    ok=False,
                    status="closed",
                    message="任务已结束，请重新下令",
                    timestamp=timestamp,
                )
            return QuestionSubmitResult(
                ok=False,
                status="unknown_message",
                message="未找到对应问题",
                timestamp=timestamp,
            )
        if pending.deadline_at <= timestamp:
            delivered = self._expire(response.message_id, timestamp)
            return QuestionSubmitResult(
                ok=False,
                status="timed_out",
                message="已按默认处理，如需更改请重新下令",
                timestamp=timestamp,
                delivered_response=delivered,
            )
        if pending.message.task_id != response.task_id:
            return QuestionSubmitResult(
                ok=False,
                status="task_mismatch",
                message="回复与任务不匹配",
                timestamp=timestamp,
            )
        self._pending_questions.pop(response.message_id, None)
        return QuestionSubmitResult(
            ok=True,
            status="delivered",
            timestamp=timestamp,
            delivered_response=PlayerResponse(
                message_id=response.message_id,
                task_id=response.task_id,
                answer=response.answer,
                timestamp=timestamp,
            ),
        )

    def expire_due(self, timestamp: float) -> list[PlayerResponse]:
        expired_ids = [
            message_id
            for message_id, pending in self._pending_questions.items()
            if pending.deadline_at <= timestamp
        ]
        return [response for message_id in expired_ids if (response := self._expire(message_id, timestamp)) is not None]

    def reset(self) -> None:
        self._pending_questions.clear()
        self._timed_out_questions.clear()
        self._closed_questions.clear()

    def _expire(self, message_id: str, timestamp: float) -> Optional[PlayerResponse]:
        pending = self._pending_questions.pop(message_id, None)
        if pending is None:
            return None
        self._timed_out_questions.add(message_id)
        return PlayerResponse(
            message_id=message_id,
            task_id=pending.message.task_id,
            answer=pending.default_option,
            timestamp=timestamp,
        )
