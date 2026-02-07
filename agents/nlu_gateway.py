from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
THE_SEED_PATH = PROJECT_ROOT / "the-seed"
if str(THE_SEED_PATH) not in sys.path:
    sys.path.insert(0, str(THE_SEED_PATH))

from nlu_pipeline.runtime import PortableIntentModel
from the_seed.core import ExecutionResult, SimpleExecutor
from the_seed.demos.openra.rules.command_router import CommandRouter
from the_seed.utils import DashboardBridge, LogManager

logger = LogManager.get_logger()


@dataclass
class NLUDecision:
    source: str
    reason: str
    intent: Optional[str] = None
    confidence: float = 0.0
    route_intent: Optional[str] = None
    matched: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "reason": self.reason,
            "intent": self.intent,
            "confidence": self.confidence,
            "route_intent": self.route_intent,
            "matched": self.matched,
            "timestamp": int(time.time() * 1000),
        }


class Phase2NLUGateway:
    """Phase 2 gateway: Safe intents direct route + fallback to LLM executor."""

    def __init__(
        self,
        *,
        name: str,
        config_path: str = "nlu_pipeline/configs/runtime_gateway.yaml",
        runtime_model_path: str = "nlu_pipeline/artifacts/intent_model_runtime.json",
    ) -> None:
        self.name = name
        self.config_path = Path(config_path)
        self.runtime_model_path = Path(runtime_model_path)

        self.config = self._load_config()
        self.router = CommandRouter()
        self.model: Optional[PortableIntentModel] = None
        self.model_loaded = False
        self._blocked_patterns: list[re.Pattern[str]] = []

        self._compile_patterns()
        self._load_model()

    def _load_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            logger.warning("NLUGateway[%s] config not found: %s", self.name, self.config_path)
            return {"enabled": False}
        with self.config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data

    def _compile_patterns(self) -> None:
        self._blocked_patterns = []
        for pat in self.config.get("blocked_regex", []):
            try:
                self._blocked_patterns.append(re.compile(str(pat)))
            except re.error:
                logger.warning("NLUGateway[%s] invalid blocked regex: %s", self.name, pat)

    def _load_model(self) -> None:
        if not self.runtime_model_path.exists():
            logger.warning(
                "NLUGateway[%s] runtime model not found: %s",
                self.name,
                self.runtime_model_path,
            )
            self.model_loaded = False
            return
        try:
            self.model = PortableIntentModel.load(self.runtime_model_path)
            self.model_loaded = True
            logger.info(
                "NLUGateway[%s] runtime model loaded: %s labels=%d",
                self.name,
                self.runtime_model_path,
                len(self.model.labels),
            )
        except Exception as e:
            self.model_loaded = False
            logger.warning("NLUGateway[%s] failed to load model: %s", self.name, e)

    @staticmethod
    def _has_attack_verb(text: str) -> bool:
        return bool(re.search(r"(攻击|进攻|突袭|集火|全军出击|打|压上|推过去)", text))

    def _attack_gate_check(
        self,
        *,
        text: str,
        pred_conf: float,
        route_result: Any,
        route_intent: str,
    ) -> tuple[bool, str]:
        cfg = self.config.get("attack_gated", {})
        if not bool(cfg.get("enabled", False)):
            return False, "attack_gated_disabled"
        if not route_result.matched or not route_result.code:
            return False, f"attack_router_unmatched:{route_result.reason}"
        if route_intent != "attack":
            return False, "attack_router_intent_not_attack"

        min_conf = float(cfg.get("min_confidence", 0.93))
        if pred_conf < min_conf:
            return False, "attack_low_confidence"

        min_router_score = float(cfg.get("min_router_score", 0.95))
        if float(route_result.score or 0.0) < min_router_score:
            return False, "attack_low_router_score"

        if bool(cfg.get("require_explicit_attack_verb", True)) and not self._has_attack_verb(text):
            return False, "attack_verb_missing"

        entities = route_result.entities or {}
        if bool(cfg.get("require_target_entity", True)):
            if not (entities.get("target_type") or entities.get("unit")):
                return False, "attack_target_missing"
        if bool(cfg.get("require_attacker_entity", False)):
            if not entities.get("attacker_type"):
                return False, "attack_attacker_missing"

        return True, "attack_gated_pass"

    def reload(self) -> None:
        self.config = self._load_config()
        self._compile_patterns()
        self._load_model()

    def is_enabled(self) -> bool:
        return bool(self.config.get("enabled", False)) and self.model_loaded

    def status(self) -> Dict[str, Any]:
        return {
            "agent": self.name,
            "phase": self.config.get("phase", "phase2"),
            "enabled": bool(self.config.get("enabled", False)),
            "active": self.is_enabled(),
            "shadow_mode": bool(self.config.get("shadow_mode", False)),
            "model_loaded": self.model_loaded,
            "runtime_model_path": str(self.runtime_model_path),
            "safe_intents": list(self.config.get("safe_intents", [])),
            "attack_gated_enabled": bool(self.config.get("attack_gated", {}).get("enabled", False)),
        }

    def run(self, executor: SimpleExecutor, command: str) -> Tuple[ExecutionResult, Dict[str, Any]]:
        text = (command or "").strip()
        if not text:
            result = executor.run(command)
            decision = NLUDecision(source="llm_fallback", reason="empty_command")
            self._emit(decision, text)
            return result, decision.to_dict()

        if not self.is_enabled():
            result = executor.run(command)
            decision = NLUDecision(source="llm_fallback", reason="gateway_disabled_or_model_missing")
            self._emit(decision, text)
            return result, decision.to_dict()

        if len(text) > int(self.config.get("max_command_len", 80)):
            result = executor.run(command)
            decision = NLUDecision(source="llm_fallback", reason="command_too_long")
            self._emit(decision, text)
            return result, decision.to_dict()

        for pat in self._blocked_patterns:
            if pat.search(text):
                result = executor.run(command)
                decision = NLUDecision(source="llm_fallback", reason="blocked_by_safety_pattern")
                self._emit(decision, text)
                return result, decision.to_dict()

        assert self.model is not None
        pred = self.model.predict_one(text)
        pred_intent = pred.intent
        pred_conf = float(pred.confidence)
        safe_intents = set(self.config.get("safe_intents", []))
        route_result = self.router.route(text)
        route_intent = route_result.intent or ""
        min_router_score = float(self.config.get("min_router_score", 0.8))
        router_safe_candidate = bool(
            route_result.matched
            and route_result.code
            and route_intent in safe_intents
            and float(route_result.score) >= min_router_score
        )
        override_by_router = False
        attack_route_allowed = False

        high_risk = set(self.config.get("high_risk", {}).get("intents", []))
        if pred_intent in high_risk and bool(self.config.get("high_risk", {}).get("force_fallback", True)):
            if bool(self.config.get("allow_safe_router_override", True)) and router_safe_candidate:
                override_by_router = True
            else:
                result = executor.run(command)
                decision = NLUDecision(
                    source="llm_fallback",
                    reason="high_risk_intent_blocked",
                    intent=pred_intent,
                    confidence=pred_conf,
                    route_intent=route_intent,
                    matched=bool(route_result.matched),
                )
                self._emit(decision, text)
                return result, decision.to_dict()
        elif pred_intent in high_risk and pred_intent == "attack":
            ok, reason = self._attack_gate_check(
                text=text,
                pred_conf=pred_conf,
                route_result=route_result,
                route_intent=route_intent,
            )
            if not ok:
                result = executor.run(command)
                decision = NLUDecision(
                    source="llm_fallback",
                    reason=reason,
                    intent=pred_intent,
                    confidence=pred_conf,
                    route_intent=route_intent,
                    matched=bool(route_result.matched),
                )
                self._emit(decision, text)
                return result, decision.to_dict()
            attack_route_allowed = True
        elif pred_intent in high_risk:
            if bool(self.config.get("allow_safe_router_override", True)) and router_safe_candidate:
                override_by_router = True
            else:
                result = executor.run(command)
                decision = NLUDecision(
                    source="llm_fallback",
                    reason="high_risk_intent_blocked",
                    intent=pred_intent,
                    confidence=pred_conf,
                    route_intent=route_intent,
                    matched=bool(route_result.matched),
                )
                self._emit(decision, text)
                return result, decision.to_dict()

        if bool(self.config.get("shadow_mode", False)):
            result = executor.run(command)
            decision = NLUDecision(
                source="llm_fallback",
                reason="shadow_mode",
                intent=pred_intent,
                confidence=pred_conf,
            )
            self._emit(decision, text)
            return result, decision.to_dict()

        if not override_by_router and not attack_route_allowed and pred_intent not in safe_intents:
            result = executor.run(command)
            decision = NLUDecision(
                source="llm_fallback",
                reason="predicted_intent_not_safe",
                intent=pred_intent,
                confidence=pred_conf,
            )
            self._emit(decision, text)
            return result, decision.to_dict()

        min_conf_map = self.config.get("min_confidence_by_intent", {})
        min_conf = float(min_conf_map.get(pred_intent, 0.75))
        if not override_by_router and not attack_route_allowed and pred_conf < min_conf:
            result = executor.run(command)
            decision = NLUDecision(
                source="llm_fallback",
                reason="low_confidence",
                intent=pred_intent,
                confidence=pred_conf,
            )
            self._emit(decision, text)
            return result, decision.to_dict()

        if not route_result.matched or not route_result.code:
            result = executor.run(command)
            decision = NLUDecision(
                source="llm_fallback",
                reason=f"router_unmatched:{route_result.reason}",
                intent=pred_intent,
                confidence=pred_conf,
                route_intent=route_result.intent,
                matched=bool(route_result.matched),
            )
            self._emit(decision, text)
            return result, decision.to_dict()

        if (
            bool(self.config.get("require_router_match", True))
            and route_intent not in safe_intents
            and not attack_route_allowed
        ):
            result = executor.run(command)
            decision = NLUDecision(
                source="llm_fallback",
                reason="router_intent_not_safe",
                intent=pred_intent,
                confidence=pred_conf,
                route_intent=route_intent,
                matched=True,
            )
            self._emit(decision, text)
            return result, decision.to_dict()

        if (
            not override_by_router
            and not attack_route_allowed
            and bool(self.config.get("require_intent_match", True))
            and route_intent != pred_intent
        ):
            result = executor.run(command)
            decision = NLUDecision(
                source="llm_fallback",
                reason="intent_router_mismatch",
                intent=pred_intent,
                confidence=pred_conf,
                route_intent=route_intent,
                matched=True,
            )
            self._emit(decision, text)
            return result, decision.to_dict()

        if route_intent == "composite_sequence":
            entities = route_result.entities or {}
            steps = int(entities.get("step_count") or 0)
            max_steps = int(self.config.get("max_steps_for_composite", 3))
            if steps > max_steps:
                result = executor.run(command)
                decision = NLUDecision(
                    source="llm_fallback",
                    reason="composite_steps_exceed_limit",
                    intent=pred_intent,
                    confidence=pred_conf,
                    route_intent=route_intent,
                    matched=True,
                )
                self._emit(decision, text)
                return result, decision.to_dict()

        # Route execution
        logger.info(
            "NLUGateway[%s] route hit intent=%s conf=%.3f",
            self.name,
            route_intent,
            pred_conf,
        )
        exec_result = executor._execute_code(route_result.code)
        executor._record_history(text, route_result.code, exec_result)

        decision = NLUDecision(
            source="nlu_route",
            reason=(
                "safe_router_override"
                if override_by_router
                else ("attack_gated_routed" if attack_route_allowed else "safe_intent_routed")
            ),
            intent=pred_intent,
            confidence=pred_conf,
            route_intent=route_intent,
            matched=True,
        )
        self._emit(decision, text)
        return exec_result, decision.to_dict()

    def _emit(self, decision: NLUDecision, command: str) -> None:
        if not bool(self.config.get("emit_dashboard_event", True)):
            return
        try:
            DashboardBridge().broadcast(
                "nlu_decision",
                {
                    "agent": self.name,
                    "command": command,
                    **decision.to_dict(),
                },
            )
        except Exception:
            # Dashboard channel should never block execution
            pass
