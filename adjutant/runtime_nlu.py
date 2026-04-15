"""Runtime NLU adapter that reuses the old Phase-2 NLU front-half only.

This module intentionally does NOT execute the old code templates / SimpleExecutor.
It only consumes:
  - PortableIntentModel
  - CommandRouter
  - runtime_gateway.yaml thresholds / guardrails

Then converts accepted routes into current-runtime direct actions that Adjutant can
translate into Kernel task/job creation.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import time
from typing import Any, Optional

import yaml

from models import CombatJobConfig, DeployJobConfig, EconomyJobConfig, EngagementMode, ReconJobConfig
from nlu_pipeline.rules import CommandRouter, RouteResult
from nlu_pipeline.runtime import PortableIntentModel
from openra_api.production_names import normalize_production_name
from unit_registry import UnitRegistry, normalize_registry_name


_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CONFIG_PATH = _ROOT / "nlu_pipeline" / "configs" / "runtime_gateway.yaml"
_DEFAULT_MODEL_PATH = _ROOT / "nlu_pipeline" / "artifacts" / "intent_model_runtime.json"


@dataclass(frozen=True)
class DirectNLUStep:
    intent: str
    expert_type: str
    config: Any
    reason: str
    source_text: str


@dataclass(frozen=True)
class RuntimeNLUDecision:
    source: str
    reason: str
    intent: Optional[str]
    confidence: float
    route_intent: Optional[str]
    matched: bool
    risk_level: str
    rollout_allowed: bool
    rollout_reason: str
    steps: list[DirectNLUStep]


class RuntimeNLURouter:
    """Adapter over the old tested NLU front-half for current runtime actions."""

    SUPPORTED_DIRECT_INTENTS = {"deploy_mcv", "produce", "explore", "mine", "stop_attack", "query_actor", "attack"}

    def __init__(
        self,
        *,
        unit_registry: UnitRegistry,
        config_path: Path | str = _DEFAULT_CONFIG_PATH,
        model_path: Path | str = _DEFAULT_MODEL_PATH,
    ) -> None:
        self.unit_registry = unit_registry
        self.config_path = Path(config_path)
        self.model_path = Path(model_path)
        self.config = self._load_config()
        self.router = CommandRouter()
        self.model = self._load_model()
        self.safe_intents = {str(x) for x in self.config.get("safe_intents", []) if str(x).strip()}
        self.high_risk_intents = {
            str(x) for x in self.config.get("high_risk", {}).get("intents", []) if str(x).strip()
        }
        self._decision_log_path = self._resolve_decision_log_path()
        self._blocked_patterns = self._compile_blocked_patterns()

    def is_enabled(self) -> bool:
        return bool(self.config.get("enabled", False)) and self.model is not None

    def route(self, text: str) -> Optional[RuntimeNLUDecision]:
        normalized = str(text or "").strip()
        if not normalized or not self.is_enabled():
            return None
        if self._is_blocked(normalized):
            return None
        rewritten = self._rewrite_router_text(normalized)

        pred = self.model.predict_one(normalized)
        route_result = self.router.route(rewritten)
        route_intent = route_result.intent
        if self._is_stop_attack_command(normalized):
            route_intent = "stop_attack"
        risk_level = "high" if (pred.intent in self.high_risk_intents or route_intent in self.high_risk_intents) else "low"
        rollout_allowed = bool(self.config.get("rollout", {}).get("enabled", True))
        rollout_reason = "rollout_enabled" if rollout_allowed else "rollout_disabled"

        # Hard confidence floor: by default, fall back to LLM below this.
        # But allow explicit safe router overrides (e.g. "步兵", "来点步兵")
        # to pass when the text clearly matches a supported direct intent.
        hard_min_conf = float(self.config.get("hard_min_confidence", 0.7))
        allow_safe_override = (
            bool(route_result.matched)
            and bool(route_intent)
            and route_intent in self.SUPPORTED_DIRECT_INTENTS
            and self._allow_safe_router_override(route_intent, normalized)
        )
        if pred.confidence < hard_min_conf and not allow_safe_override:
            return None

        if not route_result.matched or not route_intent:
            return None

        if route_intent not in self.safe_intents and route_intent != "composite_sequence":
            return None

        min_conf = float(self.config.get("min_confidence_by_intent", {}).get(pred.intent, 0.75))
        min_router_score = float(self.config.get("min_router_score", 0.8))

        if route_intent == "composite_sequence":
            return self._route_sequence(
                text=normalized,
                pred_intent=pred.intent,
                pred_conf=pred.confidence,
                route_result=route_result,
                min_router_score=min_router_score,
            )

        if route_intent not in self.SUPPORTED_DIRECT_INTENTS:
            return None
        if pred.intent != route_intent and not self._allow_safe_router_override(route_intent, normalized):
            return None
        if pred.confidence < min_conf and not self._allow_safe_router_override(route_intent, normalized):
            return None
        router_score_threshold = min_router_score
        if route_intent == "query_actor" and self._looks_like_query_command(normalized):
            router_score_threshold = min(router_score_threshold, 0.7)
        if float(route_result.score or 0.0) < router_score_threshold:
            return None

        steps = self._steps_from_intent(route_intent, route_result.entities or {}, normalized)
        if not steps:
            return None
        return RuntimeNLUDecision(
            source="nlu_route",
            reason="safe_intent_routed",
            intent=pred.intent,
            confidence=float(pred.confidence),
            route_intent=route_intent,
            matched=True,
            risk_level=risk_level,
            rollout_allowed=rollout_allowed,
            rollout_reason=rollout_reason,
            steps=steps,
        )

    def _route_sequence(
        self,
        *,
        text: str,
        pred_intent: str,
        pred_conf: float,
        route_result: RouteResult,
        min_router_score: float,
    ) -> Optional[RuntimeNLUDecision]:
        cfg = self.config.get("composite_gated", {})
        if not bool(cfg.get("enabled", False)):
            return None
        min_conf = float(cfg.get("min_confidence", 0.9))
        min_steps = int(cfg.get("min_steps", 2))
        max_steps = int(cfg.get("max_steps", self.config.get("max_steps_for_composite", 3)))
        allow_router_override = self._allow_safe_composite_router_override(route_result, text)
        router_score_threshold = max(min_router_score, float(cfg.get("min_router_score", 0.9)))
        if allow_router_override:
            router_score_threshold = min(
                router_score_threshold,
                float(cfg.get("router_override_score", 0.85)),
            )
        if pred_intent != "composite_sequence" and not allow_router_override:
            return None
        if pred_conf < min_conf and not allow_router_override:
            return None
        if float(route_result.score or 0.0) < router_score_threshold:
            return None

        entities = route_result.entities or {}
        step_intents = [str(x) for x in entities.get("step_intents") or [] if str(x).strip()]
        step_entities = list(entities.get("step_entities") or [])
        clauses = list(entities.get("clauses") or [])
        step_count = int(entities.get("step_count") or len(step_intents))
        if step_count < min_steps or step_count > max_steps:
            return None
        if len(step_intents) != len(step_entities) or len(step_intents) != len(clauses):
            return None
        if any(intent not in self.SUPPORTED_DIRECT_INTENTS for intent in step_intents):
            return None

        steps: list[DirectNLUStep] = []
        for step_intent, step_entity, clause in zip(step_intents, step_entities, clauses, strict=True):
            step_group = self._steps_from_intent(step_intent, dict(step_entity), str(clause))
            if not step_group:
                return None
            steps.extend(step_group)

        return RuntimeNLUDecision(
            source="nlu_route",
            reason="composite_gated_routed",
            intent=pred_intent,
            confidence=float(pred_conf),
            route_intent="composite_sequence",
            matched=True,
            risk_level="high",
            rollout_allowed=True,
            rollout_reason="rollout_enabled",
            steps=steps,
        )

    def _steps_from_intent(
        self,
        intent: str,
        entities: dict[str, Any],
        source_text: str,
    ) -> list[DirectNLUStep]:
        if intent == "deploy_mcv":
            return [
                DirectNLUStep(
                    intent=intent,
                    expert_type="DeployExpert",
                    config=DeployJobConfig(actor_id=-1, target_position=(0, 0)),
                    reason="nlu_deploy_mcv",
                    source_text=source_text,
                )
            ]
        if intent == "explore":
            # Extract scout_count from entities or "所有/全部" keywords
            scout_count = int(entities.get("count") or 1)
            if scout_count <= 1 and re.search(r"(所有|全部|全军|都去|都派|全体)", source_text):
                scout_count = 10  # "all" → request many, resource manager will cap
            return [
                DirectNLUStep(
                    intent=intent,
                    expert_type="ReconExpert",
                    config=ReconJobConfig(
                        search_region="enemy_half",
                        target_type="base",
                        target_owner="enemy",
                        retreat_hp_pct=0.3,
                        avoid_combat=True,
                        scout_count=scout_count,
                    ),
                    reason="nlu_explore",
                    source_text=source_text,
                )
            ]
        if intent == "produce":
            items = list(entities.get("production_items") or [])
            if not items and entities.get("unit"):
                items = [{"unit": entities.get("unit"), "count": entities.get("count") or 1}]
            if len(items) <= 1 and int(entities.get("count") or 1) <= 1:
                fallback_items = self._extract_multi_production_items(source_text)
                if len(fallback_items) > 1:
                    items = fallback_items
            steps: list[DirectNLUStep] = []
            multi_item = len(items) > 1
            for item in items:
                entry = self.unit_registry.resolve_name(item.get("unit"))
                if entry is None:
                    return []
                count = max(1, int(item.get("count") or 1))
                # When multiple items share one source_text, use per-item description
                step_text = f"造{count}个{item.get('unit', entry.unit_id)}" if multi_item else source_text
                steps.append(
                    DirectNLUStep(
                        intent=intent,
                        expert_type="EconomyExpert",
                        config=EconomyJobConfig(
                            unit_type=normalize_production_name(entry.unit_id),
                            count=count,
                            queue_type=entry.queue_type,
                            repeat=False,
                        ),
                        reason="nlu_produce",
                        source_text=step_text,
                    )
                )
            return steps
        if intent == "mine":
            return [
                DirectNLUStep(
                    intent=intent,
                    expert_type="__MINE__",
                    config={"unit": entities.get("unit") or "矿车", "count": max(1, int(entities.get("count") or 1))},
                    reason="nlu_mine",
                    source_text=source_text,
                )
            ]
        if intent == "stop_attack":
            return [
                DirectNLUStep(
                    intent=intent,
                    expert_type="__STOP_ATTACK__",
                    config=dict(entities),
                    reason="nlu_stop_attack",
                    source_text=source_text,
                )
            ]
        if intent == "query_actor":
            return [
                DirectNLUStep(
                    intent=intent,
                    expert_type="__QUERY_ACTOR__",
                    config=dict(entities),
                    reason="nlu_query_actor",
                    source_text=source_text,
                )
            ]
        if intent == "attack":
            unit_count = int(entities.get("count") or 0)
            if re.search(r"(所有|全部|全军|全体|都去|总攻)", source_text):
                unit_count = 0  # 0 = all available — override any default count
            # target_position (0,0) → CombatJob auto-targets nearest enemy
            return [
                DirectNLUStep(
                    intent=intent,
                    expert_type="CombatExpert",
                    config=CombatJobConfig(
                        target_position=(0, 0),
                        engagement_mode=EngagementMode.ASSAULT,
                        unit_count=unit_count,
                    ),
                    reason="nlu_attack",
                    source_text=source_text,
                )
            ]
        return []

    def _extract_multi_production_items(self, source_text: str) -> list[dict[str, Any]]:
        normalized_text = normalize_registry_name(source_text)
        if not normalized_text:
            return []
        matches: list[tuple[int, int, str, str]] = []
        seen_mentions: set[tuple[int, str]] = set()
        allowed_queues = {"building", "defense", "infantry", "vehicle", "aircraft"}
        for entry in self.unit_registry.entries():
            if entry.queue_type.lower() not in allowed_queues:
                continue
            best_pos: int | None = None
            best_len = 0
            best_alias = ""
            for alias in [entry.display_name, entry.unit_id, entry.unit_id.lower(), *entry.aliases]:
                normalized_alias = normalize_registry_name(alias)
                if not normalized_alias:
                    continue
                pos = normalized_text.find(normalized_alias)
                if pos < 0:
                    continue
                if best_pos is None or pos < best_pos or (pos == best_pos and len(normalized_alias) > best_len):
                    best_pos = pos
                    best_len = len(normalized_alias)
                    best_alias = str(alias)
            if best_pos is None:
                continue
            mention_key = (best_pos, normalize_registry_name(best_alias))
            if mention_key in seen_mentions:
                continue
            seen_mentions.add(mention_key)
            matches.append((best_pos, -best_len, entry.unit_id, best_alias))
        matches.sort()
        if len(matches) <= 1:
            return []
        return [{"unit": alias, "count": 1} for _, _, _, alias in matches]

    def _allow_safe_composite_router_override(self, route_result: RouteResult, text: str) -> bool:
        entities = route_result.entities or {}
        step_intents = [str(x) for x in entities.get("step_intents") or [] if str(x).strip()]
        if not step_intents:
            return False
        if any(intent not in self.SUPPORTED_DIRECT_INTENTS for intent in step_intents):
            return False
        clauses = list(entities.get("clauses") or [])
        if not clauses:
            return False
        if any(not clause.strip() for clause in clauses):
            return False
        return True

    def append_decision_log(self, command: str, payload: dict[str, Any]) -> None:
        if self._decision_log_path is None:
            return
        try:
            self._decision_log_path.parent.mkdir(parents=True, exist_ok=True)
            record = {"command": command, **payload, "timestamp": int(time.time() * 1000)}
            with self._decision_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            return

    def _allow_safe_router_override(self, route_intent: str, text: str) -> bool:
        if not bool(self.config.get("allow_safe_router_override", True)):
            return False
        if route_intent == "produce":
            return self._looks_like_produce(text) or self._looks_like_implicit_produce(text)
        if route_intent == "explore":
            return True
        if route_intent == "deploy_mcv":
            return True
        if route_intent == "stop_attack":
            return self._is_stop_attack_command(text)
        if route_intent == "attack":
            return self._looks_like_attack(text)
        return False

    def _is_blocked(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in self._blocked_patterns)

    def _compile_blocked_patterns(self) -> list[re.Pattern[str]]:
        compiled: list[re.Pattern[str]] = []
        for item in self.config.get("blocked_regex", []):
            try:
                compiled.append(re.compile(str(item)))
            except re.error:
                continue
        return compiled

    def _resolve_decision_log_path(self) -> Optional[Path]:
        online = self.config.get("online_collection", {})
        if not bool(online.get("enabled", False)):
            return None
        raw = str(online.get("decision_log_path", "")).strip()
        if not raw:
            return None
        path = Path(raw)
        if not path.is_absolute():
            path = _ROOT / path
        return path

    def _load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {"enabled": False}
        data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return {"enabled": False}
        return data

    def _load_model(self) -> Optional[PortableIntentModel]:
        if not self.model_path.exists():
            return None
        try:
            return PortableIntentModel.load(self.model_path)
        except Exception:
            return None

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
                r"(停火|停止(?:攻击|进攻|开火|作战|行动)|取消(?:攻击|进攻)|别攻击|不要攻击|先停手|停一停|撤退|全军撤退|后撤|回撤|撤回|退回去|退回来|撤军|退兵)",
                text,
            )
        )

    @staticmethod
    def _looks_like_attack(text: str) -> bool:
        return bool(
            re.search(
                r"(进攻|攻击|出击|突袭|冲锋|总攻|全军进攻|全军出击|打过去|打他|揍他|干他|集火)",
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
    def _looks_like_produce(command: str) -> bool:
        return bool(
            re.search(
                r"(建造|生产|训练|制造|造|补(?!给)|爆兵|出兵|起兵|来一个|来一辆|搞一个|整一个|下电|补电|下兵营|下车间|开车间|拍兵)",
                command,
            )
        )

    @staticmethod
    def _looks_like_implicit_produce(command: str) -> bool:
        if re.search(r"(展开|部署|下基地|开基地|基地车|建造车|mcv)", command):
            return False
        if re.search(r"(采矿|挖矿|采集|矿车干活|矿车采矿|去矿区|拉钱|采钱)", command):
            return False
        if re.search(r"(侦察|侦查|探索|探路|探图|开图)", command):
            return False
        if re.search(r"(查询|查看|列出|查下|看下|看看|有多少|多少|几辆|几只|几架|兵力)", command):
            return False
        if re.search(r"(攻击|进攻|突袭|集火|停火|停止攻击|停止进攻|取消攻击)", command):
            return False
        if re.search(r"(反击|还击|回击|反打|反推|防守|守住|顶住|拦截)", command):
            return False
        if re.search(
            r"(左边|右边|上面|下面|前面|后面|旁边|附近|周围|对面|那里|这里|那边|这边|北边|南边|东边|西边"
            r"|怎么样|什么情况|状况|好了吗|好没|完了吗|在哪|被打|被攻击|着火|损坏)",
            command,
        ):
            return False
        # Power/resource shortage phrases → implicit produce (e.g. "没电了", "缺电", "断电")
        if re.search(r"(没电|缺电|断电|电力不足|电不够|停电)", command):
            return True
        if re.search(r"(炸了|掉了|没了|丢了)", command):
            return False
        if re.search(r"^([0-9一二三四五六七八九十两]+)(个|辆|座|架|名|只|台)?", command):
            return True
        # Only match short strings that contain a known building/unit keyword.
        # Do NOT use a catch-all regex — it misclassifies informational phrases
        # like "找到敌方基地" as production commands.
        if re.search(
            r"(电厂|核电|大电|矿场|精炼|兵营|车间|战车|雷达|科技|维修|修理|狗屋|"
            r"磁暴|火焰|防空|机枪|碉堡|"
            r"步兵|火箭兵|工程师|军犬|掷弹|"
            r"坦克|重坦|猛犸|矿车|基地车|地雷|"
            r"powr|apwr|proc|barr|weap|dome|stek|fix|kenn|silo|"
            r"tsla|ftur|sam|pbox|"
            r"e1|e2|e3|e6|dog|3tnk|4tnk|ttnk|v2rl|harv|mcv|mnly|ftrk)",
            command,
        ):
            return True
        return False
