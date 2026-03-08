from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from common import PROJECT_ROOT
from nlu_pipeline.rules import CommandRouter


class WeakLabeler:
    def __init__(self) -> None:
        self.router = CommandRouter()

    def infer(self, text: str) -> Dict[str, Any]:
        routed = self.router.route(text)
        intent = routed.intent if routed.matched else "fallback_other"
        confidence = float(routed.score or 0.0)
        slots = routed.entities or {}
        risk_level = "high" if intent == "attack" else "low"
        return {
            "intent": intent,
            "slots": slots,
            "confidence": confidence,
            "risk_level": risk_level,
            "matched": bool(routed.matched),
            "reason": routed.reason,
        }
