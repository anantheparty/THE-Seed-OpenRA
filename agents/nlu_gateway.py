from __future__ import annotations

import hashlib
import json
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
    risk_level: str = "low"
    latency_ms: float = 0.0
    rollout_allowed: bool = True
    rollout_reason: str = "rollout_not_checked"
    execution_success: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "reason": self.reason,
            "intent": self.intent,
            "confidence": self.confidence,
            "route_intent": self.route_intent,
            "matched": self.matched,
            "risk_level": self.risk_level,
            "latency_ms": self.latency_ms,
            "rollout_allowed": self.rollout_allowed,
            "rollout_reason": self.rollout_reason,
            "execution_success": self.execution_success,
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
        self._decision_log_path = self._resolve_decision_log_path()

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
        return bool(
            re.search(
                r"(攻击|进攻|突袭|集火|全军出击|打|压上|推过去|推平|冲上去|围剿|歼灭|灭掉|干掉|火力压制)",
                text,
            )
        )

    @staticmethod
    def _rewrite_router_text(text: str) -> str:
        t = str(text or "").strip()
        if not t:
            return t
        if re.fullmatch(r"开([一二三四五六七八九十两\d]+)?矿", t):
            return "建造矿场"
        if re.fullmatch(r"(下电|补电|下个电|补个电|下电厂|补电厂)", t):
            return "建造电厂"
        return t

    @staticmethod
    def _is_stop_attack_command(text: str) -> bool:
        return bool(
            re.search(
                r"(停火|停止(?:攻击|进攻|开火|作战|行动)|取消(?:攻击|进攻)|别攻击|不要攻击|先停手|停一停)",
                text,
            )
        )

    @staticmethod
    def _looks_like_query_command(text: str) -> bool:
        return bool(
            re.search(
                r"(查询|查看|列出|查下|看下|看看|查兵|查单位|有多少|多少|几辆|几只|几架|兵力|状态|战况|局势|概况|情况)",
                text,
            )
        )

    @staticmethod
    def _looks_like_produce_command(text: str) -> bool:
        return bool(
            re.search(
                r"(建造|生产|训练|制造|造|补(?!给)|爆兵|出兵|起兵|来一个|来一辆|搞一个|整一个|下电|补电|下兵营|下车间|开车间|拍兵)",
                text,
            )
        )

    @staticmethod
    def _looks_like_expand_mine_command(text: str) -> bool:
        return bool(
            re.search(
                r"(开(?:[一二三四五六七八九十两\d]+)?矿|开分矿|双矿|三矿|起矿|拉矿场|补矿)",
                text,
            )
        )

    @staticmethod
    def _looks_like_implicit_produce_command(text: str) -> bool:
        if re.search(r"(展开|部署|下基地|开基地|基地车|建造车|mcv)", text):
            return False
        if re.search(r"(采矿|挖矿|采集|矿车干活|矿车采矿|去矿区|拉钱|采钱)", text):
            return False
        if re.search(r"(侦察|侦查|探索|探路|探图|开图)", text):
            return False
        if re.search(r"(查询|查看|列出|查下|看下|看看|有多少|多少|几辆|几只|几架|兵力)", text):
            return False
        if re.search(r"(攻击|进攻|突袭|集火|停火|停止攻击|停止进攻|取消攻击)", text):
            return False
        if re.search(r"^([0-9一二三四五六七八九十两]+)(个|辆|座|架|名|只|台)?[\u4e00-\u9fffA-Za-z0-9]+$", text):
            return True
        return bool(re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9]{1,8}", text))

    @staticmethod
    def _has_sequence_connector(text: str) -> bool:
        return bool(re.search(r"(然后|再|接着|随后|之后|并且|，|,|；|;)", text))

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
            # For explicit attack wording with very strong router confidence,
            # allow a lower confidence floor to avoid blocking common shorthand.
            override_min_conf = float(cfg.get("min_confidence_router_override", 0.55))
            override_router_score = float(cfg.get("router_override_score", 0.995))
            if pred_conf < override_min_conf or float(route_result.score or 0.0) < override_router_score:
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

    def _composite_gate_check(
        self,
        *,
        text: str,
        pred_conf: float,
        route_result: Any,
        route_intent: str,
    ) -> tuple[bool, str]:
        cfg = self.config.get("composite_gated", {})
        if not bool(cfg.get("enabled", False)):
            return False, "composite_gated_disabled"
        if not route_result.matched or not route_result.code:
            return False, f"composite_router_unmatched:{route_result.reason}"
        if route_intent != "composite_sequence":
            return False, "composite_router_intent_not_composite"

        min_conf = float(cfg.get("min_confidence", 0.90))
        if pred_conf < min_conf:
            return False, "composite_low_confidence"

        min_router_score = float(cfg.get("min_router_score", 0.90))
        if float(route_result.score or 0.0) < min_router_score:
            return False, "composite_low_router_score"

        entities = route_result.entities or {}
        steps = int(entities.get("step_count") or 0)
        min_steps = int(cfg.get("min_steps", 2))
        max_steps = int(cfg.get("max_steps", self.config.get("max_steps_for_composite", 3)))
        if steps < min_steps:
            return False, "composite_step_count_too_small"
        if steps > max_steps:
            return False, "composite_steps_exceed_limit"

        if bool(cfg.get("require_connector", True)) and not self._has_sequence_connector(text):
            return False, "composite_connector_missing"

        step_intents = [str(x) for x in (entities.get("step_intents") or []) if str(x).strip()]
        if bool(cfg.get("require_step_intents", True)) and not step_intents:
            return False, "composite_step_intents_missing"

        if bool(cfg.get("forbid_attack_step", True)) and "attack" in step_intents:
            return False, "composite_attack_step_forbidden"

        allowed_step_intents = set(str(x) for x in cfg.get("allowed_step_intents", []) if str(x).strip())
        if allowed_step_intents and step_intents:
            blocked = [x for x in step_intents if x not in allowed_step_intents]
            if blocked:
                return False, f"composite_step_intent_not_allowed:{','.join(blocked)}"

        return True, "composite_gated_pass"

    def reload(self) -> None:
        self.config = self._load_config()
        self._compile_patterns()
        self._load_model()
        self._decision_log_path = self._resolve_decision_log_path()

    def _resolve_decision_log_path(self) -> Optional[Path]:
        cfg = self.config.get("online_collection", {})
        if not bool(cfg.get("enabled", False)):
            return None
        raw = str(cfg.get("decision_log_path", "")).strip()
        if not raw:
            return None
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    @staticmethod
    def _clamp_percentage(value: Any, default: float) -> float:
        try:
            out = float(value)
        except (TypeError, ValueError):
            out = default
        return max(0.0, min(100.0, out))

    def _build_rollout_key(self, text: str, cfg: Dict[str, Any], rollout_key: Optional[str]) -> str:
        mode = str(cfg.get("bucket_key", "agent_command")).strip() or "agent_command"
        salt = str(cfg.get("hash_salt", "nlu_phase4"))
        identity = str(rollout_key or "")
        if mode == "agent":
            base = self.name
        elif mode == "identity":
            base = identity or self.name
        elif mode == "identity_command":
            base = f"{identity}|{text}" if identity else f"{self.name}|{text}"
        elif mode == "command":
            base = text
        else:
            base = f"{self.name}|{text}"
        return f"{salt}|{base}"

    def _stable_bucket_percent(self, key: str) -> float:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return (int(digest[:8], 16) % 10000) / 100.0

    def _rollout_percentage(self, cfg: Dict[str, Any]) -> float:
        by_agent = cfg.get("percentages_by_agent", {})
        if isinstance(by_agent, dict) and self.name in by_agent:
            return self._clamp_percentage(by_agent.get(self.name), 0.0)
        return self._clamp_percentage(cfg.get("default_percentage", 100.0), 100.0)

    def _rollout_check(self, text: str, rollout_key: Optional[str]) -> tuple[bool, str]:
        cfg = self.config.get("rollout", {})
        if not bool(cfg.get("enabled", False)):
            return True, "rollout_feature_disabled"

        allow_agents = set(str(x) for x in cfg.get("allow_agents", []) if str(x).strip())
        if allow_agents and self.name not in allow_agents:
            return False, "rollout_agent_not_allowed"

        deny_agents = set(str(x) for x in cfg.get("deny_agents", []) if str(x).strip())
        if self.name in deny_agents:
            return False, "rollout_agent_denied"

        percentage = self._rollout_percentage(cfg)
        if percentage <= 0.0:
            return False, "rollout_zero_percentage"
        if percentage >= 100.0:
            return True, "rollout_full_percentage"

        bucket = self._stable_bucket_percent(self._build_rollout_key(text, cfg, rollout_key))
        if bucket < percentage:
            return True, f"rollout_hit:{bucket:.2f}<{percentage:.2f}"
        return False, f"rollout_holdback:{bucket:.2f}>={percentage:.2f}"

    def _risk_level_for_intent(self, intent: Optional[str]) -> str:
        if not intent:
            return "low"
        high_risk = set(self.config.get("high_risk", {}).get("intents", []))
        return "high" if intent in high_risk else "low"

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
            "composite_gated_enabled": bool(self.config.get("composite_gated", {}).get("enabled", False)),
            "online_collection_enabled": self._decision_log_path is not None,
            "decision_log_path": str(self._decision_log_path) if self._decision_log_path else "",
            "rollout_enabled": bool(self.config.get("rollout", {}).get("enabled", False)),
            "rollout_percentage": self._rollout_percentage(self.config.get("rollout", {})),
        }

    def run(
        self,
        executor: SimpleExecutor,
        command: str,
        *,
        rollout_key: Optional[str] = None,
    ) -> Tuple[ExecutionResult, Dict[str, Any]]:
        started = time.perf_counter()
        text = (command or "").strip()

        def finalize(result: ExecutionResult, decision: NLUDecision) -> Tuple[ExecutionResult, Dict[str, Any]]:
            decision.latency_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
            decision.execution_success = bool(result.success)
            self._emit(decision, text)
            return result, decision.to_dict()

        if not text:
            result = executor.run(command)
            decision = NLUDecision(source="llm_fallback", reason="empty_command")
            return finalize(result, decision)

        if not self.is_enabled():
            result = executor.run(command)
            decision = NLUDecision(source="llm_fallback", reason="gateway_disabled_or_model_missing")
            return finalize(result, decision)

        if len(text) > int(self.config.get("max_command_len", 80)):
            result = executor.run(command)
            decision = NLUDecision(source="llm_fallback", reason="command_too_long")
            return finalize(result, decision)

        for pat in self._blocked_patterns:
            if pat.search(text):
                if self._is_stop_attack_command(text):
                    break
                result = executor.run(command)
                decision = NLUDecision(source="llm_fallback", reason="blocked_by_safety_pattern")
                return finalize(result, decision)

        rollout_allowed, rollout_reason = self._rollout_check(text, rollout_key)
        if not rollout_allowed:
            result = executor.run(command)
            decision = NLUDecision(
                source="llm_fallback",
                reason=rollout_reason,
                rollout_allowed=False,
                rollout_reason=rollout_reason,
            )
            return finalize(result, decision)

        if self._is_stop_attack_command(text):
            stop_route = self.router.route("停止攻击")
            if stop_route.matched and stop_route.code:
                exec_result = executor._execute_code(stop_route.code)
                executor._record_history(text, stop_route.code, exec_result)
                decision = NLUDecision(
                    source="nlu_route",
                    reason="stop_attack_direct_route",
                    intent="stop_attack",
                    confidence=1.0,
                    route_intent="stop_attack",
                    matched=True,
                    risk_level="low",
                    rollout_allowed=rollout_allowed,
                    rollout_reason=rollout_reason,
                )
                return finalize(exec_result, decision)

        assert self.model is not None
        pred = self.model.predict_one(text)
        pred_intent = pred.intent
        pred_conf = float(pred.confidence)
        risk_level = self._risk_level_for_intent(pred_intent)
        safe_intents = set(self.config.get("safe_intents", []))
        route_text = self._rewrite_router_text(text)
        route_result = self.router.route(route_text)
        route_intent = route_result.intent or ""
        min_router_score = float(self.config.get("min_router_score", 0.8))
        router_safe_candidate = bool(
            route_result.matched
            and route_result.code
            and route_intent in safe_intents
            and float(route_result.score) >= min_router_score
        )
        router_override_safe_intents = set(
            str(x) for x in self.config.get("router_override_safe_intents", ["stop_attack"]) if str(x).strip()
        )
        query_router_override = bool(
            self.config.get("allow_query_router_override", True)
            and router_safe_candidate
            and route_intent == "query_actor"
            and pred_intent in {
                "produce",
                "fallback_other",
                "composite_sequence",
                "explore",
                "mine",
                "attack",
                "deploy_mcv",
            }
            and self._looks_like_query_command(text)
        )
        produce_router_override = bool(
            self.config.get("allow_produce_router_override", True)
            and router_safe_candidate
            and route_intent == "produce"
            and pred_intent in {
                "produce",
                "query_actor",
                "fallback_other",
                "composite_sequence",
                "attack",
                "deploy_mcv",
                "mine",
            }
            and (
                self._looks_like_produce_command(text)
                or self._looks_like_expand_mine_command(text)
                or self._looks_like_implicit_produce_command(text)
            )
        )
        pre_override_by_router = bool(
            bool(self.config.get("allow_safe_router_override", True))
            and router_safe_candidate
            and (
                route_intent in router_override_safe_intents
                or query_router_override
                or produce_router_override
            )
        )
        override_by_router = pre_override_by_router
        attack_route_allowed = False
        composite_route_allowed = False
        high_risk_route_reason = ""

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
                    risk_level=risk_level,
                    rollout_allowed=rollout_allowed,
                    rollout_reason=rollout_reason,
                )
                return finalize(result, decision)
        elif pred_intent in high_risk and pred_intent == "attack":
            if pre_override_by_router:
                override_by_router = True
            else:
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
                        risk_level=risk_level,
                        rollout_allowed=rollout_allowed,
                        rollout_reason=rollout_reason,
                    )
                    return finalize(result, decision)
                attack_route_allowed = True
                high_risk_route_reason = "attack_gated_routed"
        elif pred_intent in high_risk and pred_intent == "composite_sequence":
            if pre_override_by_router:
                override_by_router = True
            elif (
                bool(self.config.get("allow_attack_router_fallback_from_composite", True))
                and route_intent == "attack"
            ):
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
                        reason=f"composite_to_attack_gate_failed:{reason}",
                        intent=pred_intent,
                        confidence=pred_conf,
                        route_intent=route_intent,
                        matched=bool(route_result.matched),
                        risk_level=risk_level,
                        rollout_allowed=rollout_allowed,
                        rollout_reason=rollout_reason,
                    )
                    return finalize(result, decision)
                attack_route_allowed = True
                high_risk_route_reason = "attack_gated_routed_from_composite"
            else:
                ok, reason = self._composite_gate_check(
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
                        risk_level=risk_level,
                        rollout_allowed=rollout_allowed,
                        rollout_reason=rollout_reason,
                    )
                    return finalize(result, decision)
                composite_route_allowed = True
                high_risk_route_reason = "composite_gated_routed"
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
                    risk_level=risk_level,
                    rollout_allowed=rollout_allowed,
                    rollout_reason=rollout_reason,
                )
                return finalize(result, decision)

        if bool(self.config.get("shadow_mode", False)):
            result = executor.run(command)
            decision = NLUDecision(
                source="llm_fallback",
                reason="shadow_mode",
                intent=pred_intent,
                confidence=pred_conf,
                risk_level=risk_level,
                rollout_allowed=rollout_allowed,
                rollout_reason=rollout_reason,
            )
            return finalize(result, decision)

        high_risk_route_allowed = attack_route_allowed or composite_route_allowed
        if not override_by_router and not high_risk_route_allowed and pred_intent not in safe_intents:
            result = executor.run(command)
            decision = NLUDecision(
                source="llm_fallback",
                reason="predicted_intent_not_safe",
                intent=pred_intent,
                confidence=pred_conf,
                risk_level=risk_level,
                rollout_allowed=rollout_allowed,
                rollout_reason=rollout_reason,
            )
            return finalize(result, decision)

        min_conf_map = self.config.get("min_confidence_by_intent", {})
        min_conf = float(min_conf_map.get(pred_intent, 0.75))
        if (
            not override_by_router
            and not high_risk_route_allowed
            and router_safe_candidate
            and route_intent == pred_intent
            and float(route_result.score or 0.0) >= 0.95
        ):
            override_by_router = True
        if not override_by_router and not high_risk_route_allowed and pred_conf < min_conf:
            result = executor.run(command)
            decision = NLUDecision(
                source="llm_fallback",
                reason="low_confidence",
                intent=pred_intent,
                confidence=pred_conf,
                risk_level=risk_level,
                rollout_allowed=rollout_allowed,
                rollout_reason=rollout_reason,
            )
            return finalize(result, decision)

        if not route_result.matched or not route_result.code:
            result = executor.run(command)
            decision = NLUDecision(
                source="llm_fallback",
                reason=f"router_unmatched:{route_result.reason}",
                intent=pred_intent,
                confidence=pred_conf,
                route_intent=route_result.intent,
                matched=bool(route_result.matched),
                risk_level=risk_level,
                rollout_allowed=rollout_allowed,
                rollout_reason=rollout_reason,
            )
            return finalize(result, decision)

        if (
            bool(self.config.get("require_router_match", True))
            and route_intent not in safe_intents
            and not high_risk_route_allowed
        ):
            result = executor.run(command)
            decision = NLUDecision(
                source="llm_fallback",
                reason="router_intent_not_safe",
                intent=pred_intent,
                confidence=pred_conf,
                route_intent=route_intent,
                matched=True,
                risk_level=risk_level,
                rollout_allowed=rollout_allowed,
                rollout_reason=rollout_reason,
            )
            return finalize(result, decision)

        if (
            not override_by_router
            and not high_risk_route_allowed
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
                risk_level=risk_level,
                rollout_allowed=rollout_allowed,
                rollout_reason=rollout_reason,
            )
            return finalize(result, decision)

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
                    risk_level=risk_level,
                    rollout_allowed=rollout_allowed,
                    rollout_reason=rollout_reason,
                )
                return finalize(result, decision)

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
                else (high_risk_route_reason or "safe_intent_routed")
            ),
            intent=pred_intent,
            confidence=pred_conf,
            route_intent=route_intent,
            matched=True,
            risk_level=risk_level,
            rollout_allowed=rollout_allowed,
            rollout_reason=rollout_reason,
        )
        return finalize(exec_result, decision)

    def _emit(self, decision: NLUDecision, command: str) -> None:
        payload = decision.to_dict()
        self._append_decision_log(payload, command)
        if not bool(self.config.get("emit_dashboard_event", True)):
            return
        try:
            DashboardBridge().broadcast(
                "nlu_decision",
                {
                    "agent": self.name,
                    "command": command,
                    **payload,
                },
            )
        except Exception:
            # Dashboard channel should never block execution
            pass

    def _append_decision_log(self, decision_payload: Dict[str, Any], command: str) -> None:
        path = self._decision_log_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "agent": self.name,
                "command": command,
                **decision_payload,
            }
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            # never block command execution on logging failures
            pass
