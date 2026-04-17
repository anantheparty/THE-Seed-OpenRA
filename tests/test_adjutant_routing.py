"""Adjutant routing edge-case tests (Task 4.4).

Tests cover:
  1. Reply routing — single/multi pending question, priority matching
  2. Timeout — late reply after default applied
  3. Mixed — command during pending question, concurrent questions
  4. Classification robustness — LLM failure, empty/malformed input
"""

from __future__ import annotations

import asyncio
import json
import sys
import os
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm import LLMResponse, MockProvider
from adjutant import Adjutant, AdjutantConfig, ClassificationResult, InputType
from adjutant.adjutant import RuleMatchResult
from adjutant.runtime_nlu import DirectNLUStep, RuntimeNLUDecision
from tests._adjutant_fixtures import MockKernel as BaseMockKernel, MockTask, MockWorldModel


# --- Shared mocks (same pattern as test_adjutant.py) ---

class MockKernel(BaseMockKernel):
    def __init__(self):
        super().__init__()
        self._timed_out: set[str] = set()

    def submit_player_response(self, response, *, now=None):
        if response.message_id in self._timed_out:
            return {"ok": False, "status": "timed_out", "message": "已按默认处理，如需更改请重新下令"}
        return super().submit_player_response(response, now=now)

    def expire_question(self, message_id):
        """Simulate question timeout."""
        self._pending_questions = [q for q in self._pending_questions if q["message_id"] != message_id]
        self._timed_out.add(message_id)


# --- Routing contract matrix ---

class ProbeWorldModel(MockWorldModel):
    def __init__(self, *, stale: bool = False):
        super().__init__()
        self.stale = stale

    def refresh_health(self):
        if not self.stale:
            return super().refresh_health()
        return {
            "status": "degraded",
            "stale": True,
            "consecutive_failures": 4,
            "total_failures": 4,
            "last_error": "actors:COMMAND_EXECUTION_ERROR",
            "failure_threshold": 3,
            "timestamp": time.time(),
        }


class RoutingProbeAdjutant(Adjutant):
    def __init__(self, *, scenario: str, **kwargs):
        super().__init__(**kwargs)
        self.scenario = scenario
        self.calls: list[str] = []

    def _is_economy_command(self, text: str) -> bool:
        self.calls.append("economy_check")
        return self.scenario == "capability_merge"

    def _try_merge_to_capability(self, text: str):
        self.calls.append("capability_merge")
        return {
            "type": "command",
            "ok": True,
            "routing": "capability_merge",
            "response_text": "merged",
        }

    def _try_runtime_nlu(self, text: str):
        self.calls.append("runtime_nlu")
        if self.scenario not in {"runtime_nlu", "stale_command"}:
            return None
        return RuntimeNLUDecision(
            source="test",
            reason="test_route",
            intent="explore",
            confidence=0.99,
            route_intent="explore",
            matched=True,
            risk_level="low",
            rollout_allowed=True,
            rollout_reason="enabled",
            steps=[
                DirectNLUStep(
                    intent="explore",
                    expert_type="ReconExpert",
                    config=None,
                    reason="test_step",
                    source_text=text,
                )
            ],
        )

    async def _handle_runtime_nlu(self, text: str, decision: RuntimeNLUDecision):
        self.calls.append("handle_runtime_nlu")
        return {
            "type": "command",
            "ok": True,
            "routing": "nlu",
            "response_text": "nlu",
        }

    def _try_rule_match(self, text: str):
        self.calls.append("rule_match")
        if self.scenario != "rule":
            return None
        return RuleMatchResult(expert_type="EconomyExpert", config=None, reason="test_rule")

    async def _handle_rule_command(self, text: str, match: RuleMatchResult):
        self.calls.append("handle_rule")
        return {
            "type": "command",
            "ok": True,
            "routing": "rule",
            "response_text": "rule",
        }

    async def _classify_input(self, context):
        self.calls.append("classify")
        return ClassificationResult(input_type=InputType.COMMAND, confidence=0.9, raw_text=context.player_input)

    async def _handle_command(self, text: str):
        self.calls.append("handle_command")
        return {
            "type": "command",
            "ok": True,
            "routing": "llm_command",
            "response_text": "command",
        }


def _assert_call_subsequence(actual_calls, expected_calls):
    cursor = 0
    for expected in expected_calls:
        try:
            cursor = actual_calls.index(expected, cursor) + 1
        except ValueError as exc:
            raise AssertionError(f"Missing expected call order {expected_calls} in {actual_calls}") from exc


@pytest.mark.parametrize(
    ("scenario", "text", "expected_calls", "forbidden_calls", "expected_routing"),
    [
        ("capability_merge", "发展经济", ["economy_check", "runtime_nlu", "capability_merge"], ["rule_match", "handle_rule", "classify", "handle_command"], "capability_merge"),
        ("runtime_nlu", "探索地图", ["economy_check", "runtime_nlu", "handle_runtime_nlu"], ["rule_match", "handle_rule", "classify", "handle_command", "capability_merge"], "nlu"),
        ("rule", "建造兵营", ["economy_check", "runtime_nlu", "rule_match", "handle_rule"], ["classify", "handle_command", "capability_merge"], "rule"),
        ("classification", "继续推进左路", ["economy_check", "runtime_nlu", "rule_match", "classify", "handle_command"], ["handle_rule", "capability_merge"], "llm_command"),
    ],
)
def test_routing_precedence_matrix(scenario, text, expected_calls, forbidden_calls, expected_routing):
    adj = RoutingProbeAdjutant(
        scenario=scenario,
        llm=MockProvider(),
        kernel=MockKernel(),
        world_model=ProbeWorldModel(),
    )

    async def run():
        result = await adj.handle_player_input(text)
        assert result["routing"] == expected_routing
        assert result["ok"] is True

    asyncio.run(run())
    _assert_call_subsequence(adj.calls, expected_calls)
    for forbidden in forbidden_calls:
        assert forbidden not in adj.calls
    print(f"  PASS: routing_precedence_matrix[{scenario}]")


def test_stale_query_short_circuits_before_nlu_rule_and_llm():
    adj = RoutingProbeAdjutant(
        scenario="classification",
        llm=MockProvider(),
        kernel=MockKernel(),
        world_model=ProbeWorldModel(stale=True),
    )

    async def run():
        result = await adj.handle_player_input("战况如何？")
        assert result["type"] == "query"
        assert result["ok"] is False
        assert result["routing"] == "stale_guard"
        assert result["reason"] == "world_sync_stale"

    asyncio.run(run())
    assert adj.calls == []
    print("  PASS: stale_query_short_circuits_before_nlu_rule_and_llm")


def test_stale_command_short_circuits_after_nlu_but_before_execution():
    adj = RoutingProbeAdjutant(
        scenario="stale_command",
        llm=MockProvider(),
        kernel=MockKernel(),
        world_model=ProbeWorldModel(stale=True),
    )

    async def run():
        result = await adj.handle_player_input("探索地图")
        assert result["type"] == "command"
        assert result["ok"] is False
        assert result["routing"] == "stale_guard"
        assert result["reason"] == "world_sync_stale"

    asyncio.run(run())
    _assert_call_subsequence(adj.calls, ["economy_check", "runtime_nlu"])
    assert "handle_runtime_nlu" not in adj.calls
    print("  PASS: stale_command_short_circuits_after_nlu_but_before_execution")


# --- 1. Reply Routing Tests ---

def test_single_pending_reply():
    """Single pending question → reply routed correctly."""
    kernel = MockKernel()
    kernel._tasks.append(MockTask("t1", "侦察"))
    kernel.add_pending_question("msg_1", "t1", "发现敌人，继续？", ["继续", "撤退"], priority=50)

    llm = MockProvider(responses=[
        LLMResponse(text='{"type":"reply","target_message_id":"msg_1","target_task_id":"t1","confidence":0.95}', model="mock"),
    ])
    adj = Adjutant(llm=llm, kernel=kernel, world_model=MockWorldModel())

    async def run():
        result = await adj.handle_player_input("继续")
        assert result["type"] == "reply"
        assert result["ok"] is True

    asyncio.run(run())
    assert kernel.submitted_responses[0].answer == "继续"
    assert kernel.submitted_responses[0].message_id == "msg_1"
    print("  PASS: single_pending_reply")


def test_multi_pending_priority_routing():
    """Multiple pending questions → ambiguous reply matches highest priority."""
    kernel = MockKernel()
    kernel._tasks.append(MockTask("t1", "进攻"))
    kernel._tasks.append(MockTask("t2", "侦察"))
    kernel._tasks.append(MockTask("t3", "生产"))
    kernel.add_pending_question("msg_low", "t3", "继续生产？", ["是", "否"], priority=30)
    kernel.add_pending_question("msg_mid", "t2", "改变方向？", ["是", "否"], priority=50)
    kernel.add_pending_question("msg_high", "t1", "继续进攻？", ["继续", "放弃"], priority=70)

    # LLM returns reply without specific target
    llm = MockProvider(responses=[
        LLMResponse(text='{"type":"reply","confidence":0.7}', model="mock"),
    ])
    adj = Adjutant(llm=llm, kernel=kernel, world_model=MockWorldModel())

    async def run():
        result = await adj.handle_player_input("放弃")
        assert result["type"] == "reply"
        assert result["ok"] is True

    asyncio.run(run())
    assert kernel.submitted_responses[0].message_id == "msg_high"  # Highest priority
    assert kernel.submitted_responses[0].task_id == "t1"
    print("  PASS: multi_pending_priority_routing")


def test_reply_no_pending_questions():
    """Reply classification but no pending questions → falls back to command (T-R7-2)."""
    kernel = MockKernel()
    kernel._tasks.append(MockTask("t1", "进攻"))
    # No pending questions

    llm = MockProvider(responses=[
        LLMResponse(text='{"type":"reply","confidence":0.6}', model="mock"),
    ])
    adj = Adjutant(llm=llm, kernel=kernel, world_model=MockWorldModel())

    async def run():
        result = await adj.handle_player_input("继续")
        # T-R7-2: reply(null) now falls back to command handler instead of discarding
        assert result["type"] == "command"

    asyncio.run(run())
    print("  PASS: reply_no_pending_questions")


# --- 2. Timeout Tests ---

def test_late_reply_after_timeout():
    """Question times out → late player reply gets rejection message."""
    kernel = MockKernel()
    kernel._tasks.append(MockTask("t1", "进攻"))
    kernel.add_pending_question("msg_1", "t1", "继续？", ["继续", "放弃"], priority=60)

    # Simulate timeout
    kernel.expire_question("msg_1")

    # LLM classifies as reply to msg_1
    llm = MockProvider(responses=[
        LLMResponse(text='{"type":"reply","target_message_id":"msg_1","target_task_id":"t1","confidence":0.9}', model="mock"),
    ])
    adj = Adjutant(llm=llm, kernel=kernel, world_model=MockWorldModel())

    async def run():
        result = await adj.handle_player_input("继续")
        assert result["type"] == "reply"
        assert result["ok"] is False
        assert result["status"] == "timed_out"
        assert "默认处理" in result["response_text"]

    asyncio.run(run())
    print("  PASS: late_reply_after_timeout")


# --- 3. Mixed Scenario Tests ---

def test_new_command_during_pending_question():
    """TaskA has pending question, player sends unrelated command → new Task created."""
    kernel = MockKernel()
    kernel._tasks.append(MockTask("t1", "进攻基地"))
    kernel.add_pending_question("msg_1", "t1", "继续还是放弃？", ["继续", "放弃"], priority=60)

    # LLM correctly classifies as command (not reply)
    llm = MockProvider(responses=[
        LLMResponse(text='{"type":"command","confidence":0.9}', model="mock"),
    ])
    adj = Adjutant(llm=llm, kernel=kernel, world_model=MockWorldModel())

    async def run():
        result = await adj.handle_player_input("生产5辆坦克")
        assert result["type"] == "command"
        assert result["ok"] is True

    asyncio.run(run())
    assert len(kernel.created_tasks) == 1
    assert kernel.created_tasks[0]["raw_text"] == "生产5辆坦克"
    assert len(kernel.submitted_responses) == 0  # No reply submitted
    print("  PASS: new_command_during_pending_question")


def test_query_during_pending_question():
    """Player asks a query while a question is pending → query answered, question stays."""
    kernel = MockKernel()
    kernel._tasks.append(MockTask("t1", "进攻基地"))
    kernel.add_pending_question("msg_1", "t1", "继续？", ["继续", "放弃"], priority=60)

    llm = MockProvider(responses=[
        # Classification: query
        LLMResponse(text='{"type":"query","confidence":0.95}', model="mock"),
        # Query answer
        LLMResponse(text="当前兵力优势，建议继续", model="mock"),
    ])
    adj = Adjutant(llm=llm, kernel=kernel, world_model=MockWorldModel())

    async def run():
        result = await adj.handle_player_input("战况如何？")
        assert result["type"] == "query"
        assert result["ok"] is True
        assert "兵力" in result["response_text"]

    asyncio.run(run())
    # No task created, no reply submitted
    assert len(kernel.created_tasks) == 0
    assert len(kernel.submitted_responses) == 0
    # Pending question still exists
    assert len(kernel.list_pending_questions()) == 1
    print("  PASS: query_during_pending_question")


# --- 4. Classification Robustness ---

def test_empty_input():
    """Empty input defaults to command."""
    llm = MockProvider(responses=[
        LLMResponse(text='{"type":"command","confidence":0.5}', model="mock"),
    ])
    kernel = MockKernel()
    adj = Adjutant(llm=llm, kernel=kernel, world_model=MockWorldModel())

    async def run():
        result = await adj.handle_player_input("")
        assert result["type"] == "command"

    asyncio.run(run())
    print("  PASS: empty_input")


def test_malformed_llm_response():
    """LLM returns garbage → defaults to command."""
    llm = MockProvider(responses=[
        LLMResponse(text="I don't understand the question", model="mock"),
    ])
    kernel = MockKernel()
    adj = Adjutant(llm=llm, kernel=kernel, world_model=MockWorldModel())

    async def run():
        result = await adj.handle_player_input("随便说点什么")
        assert result["type"] == "command"

    asyncio.run(run())
    assert len(kernel.created_tasks) == 1
    print("  PASS: malformed_llm_response")


def test_llm_returns_invalid_type():
    """LLM returns unknown type → defaults to command."""
    llm = MockProvider(responses=[
        LLMResponse(text='{"type":"unknown_type","confidence":0.9}', model="mock"),
    ])
    kernel = MockKernel()
    adj = Adjutant(llm=llm, kernel=kernel, world_model=MockWorldModel())

    async def run():
        result = await adj.handle_player_input("???")
        assert result["type"] == "command"

    asyncio.run(run())
    print("  PASS: llm_returns_invalid_type")


def test_sequential_interactions():
    """Multiple interactions build dialogue history correctly."""
    kernel = MockKernel()
    llm = MockProvider(responses=[
        LLMResponse(text='{"type":"query","confidence":0.9}', model="mock"),
        LLMResponse(text="回答", model="mock"),
    ])
    adj = Adjutant(llm=llm, kernel=kernel, world_model=MockWorldModel())

    async def run():
        await adj.handle_player_input("生产坦克")
        await adj.handle_player_input("探索地图")
        await adj.handle_player_input("战况如何？")

    asyncio.run(run())

    assert len(kernel.created_tasks) == 2
    assert len(kernel.started_jobs) == 2
    assert len(llm.call_log) == 2
    assert len(adj._dialogue_history) == 6  # 3 player + 3 adjutant
    print("  PASS: sequential_interactions")


# --- Run all tests ---

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
