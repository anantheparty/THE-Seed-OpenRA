from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from common import PROJECT_ROOT


class WeakLabeler:
    def __init__(self) -> None:
        the_seed_path = PROJECT_ROOT / "the-seed"
        import sys

        if str(the_seed_path) not in sys.path:
            sys.path.insert(0, str(the_seed_path))

        from the_seed.demos.openra.rules.command_router import CommandRouter  # type: ignore

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
