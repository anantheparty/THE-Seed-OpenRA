"""Task replay bundle builders shared by diagnostics surfaces."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Optional


def build_live_task_replay_bundle(
    task_id: str,
    entries: list[dict[str, Any]],
    *,
    runtime_state: Optional[dict[str, Any]],
    tasks: Sequence[Any],
    jobs_for_task: Callable[[str], list[Any]],
    task_payload_builder: Callable[..., dict[str, Any]],
    compute_runtime_facts: Optional[Callable[..., dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Build a replay bundle enriched with the current live runtime view."""
    current_runtime = None
    current_status_line = ""
    live_runtime_facts: dict[str, Any] = {}
    live_task = next((task for task in tasks if getattr(task, "task_id", None) == task_id), None)
    if live_task is not None:
        current_runtime = task_payload_builder(
            live_task,
            jobs_for_task(task_id),
            runtime_state=runtime_state,
        )
        triage = current_runtime.get("triage") if isinstance(current_runtime, dict) else None
        if isinstance(triage, dict):
            current_status_line = str(triage.get("status_line") or "")
        if callable(compute_runtime_facts):
            try:
                live_runtime_facts = compute_runtime_facts(task_id, include_buildable=False) or {}
            except Exception:
                live_runtime_facts = {}
    return build_task_replay_bundle(
        task_id,
        entries,
        runtime_state=runtime_state,
        current_runtime=current_runtime,
        current_status_line=current_status_line,
        live_runtime_facts=live_runtime_facts,
    )


def build_task_replay_bundle(
    task_id: str,
    entries: list[dict[str, Any]],
    *,
    runtime_state: Optional[dict[str, Any]] = None,
    current_runtime: Optional[dict[str, Any]] = None,
    current_status_line: str = "",
    live_runtime_facts: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Summarize persisted task logs into a task-centric debug bundle."""
    if not entries:
        return {
            "task_id": task_id,
            "summary": "无持久化任务记录",
            "entry_count": 0,
            "duration_s": 0.0,
            "last_transition": None,
            "timeline": [],
            "lifecycle_events": [],
            "expert_runs": [],
            "llm_turns": [],
            "unit_pipeline": {"unfulfilled_requests": [], "unit_reservations": []},
            "blockers": [],
            "highlights": [],
            "player_visible": [],
            "llm": {"rounds": 0, "failures": 0, "prompt_tokens": 0, "completion_tokens": 0, "tool_rounds": 0},
            "tools": [],
            "experts": [],
            "signals": [],
            "current_runtime": None,
            "debug": {},
        }

    def _entry_data(entry: dict[str, Any]) -> dict[str, Any]:
        payload = entry.get("data")
        return payload if isinstance(payload, dict) else {}

    def _safe_int(value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _preview(entry: dict[str, Any], start_timestamp: float, entry_index: int) -> dict[str, Any]:
        data = _entry_data(entry)
        signal_kind = data.get("signal_kind")
        label = entry.get("event") or entry.get("component") or "log"
        if entry.get("event") == "expert_signal" and signal_kind:
            label = f"expert:{signal_kind}"
        message = (
            data.get("summary")
            or data.get("content")
            or entry.get("message")
            or label
        )
        ts = float(entry.get("timestamp") or start_timestamp)
        return {
            "timestamp": ts,
            "elapsed_s": round(max(0.0, ts - start_timestamp), 1),
            "component": entry.get("component", "log"),
            "level": entry.get("level", "INFO"),
            "label": label,
            "message": str(message),
            "task_id": data.get("task_id") or task_id,
            "job_id": data.get("job_id"),
            "expert_type": data.get("expert_type"),
            "signal_kind": data.get("signal_kind"),
            "result": data.get("result"),
            "data": data,
            "entry_index": entry_index,
        }

    def _dedupe(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        last_key: Optional[tuple[Any, ...]] = None
        last_index: Optional[int] = None
        for item in items:
            key = (item.get("label"), item.get("message"))
            item_index = _safe_int(item.get("entry_index"))
            if key == last_key and item_index is not None and last_index is not None and item_index == last_index + 1:
                continue
            out.append(item)
            last_key = key
            last_index = item_index
        return out[-limit:]

    def _timeline_item(preview: dict[str, Any], start_timestamp: float) -> dict[str, Any]:
        ts = float(preview.get("timestamp") or start_timestamp)
        return {
            "elapsed_s": round(max(0.0, ts - start_timestamp), 1),
            "level": preview.get("level", "INFO"),
            "label": preview.get("label"),
            "message": preview.get("message"),
        }

    def _related_job_id(preview: dict[str, Any]) -> Optional[str]:
        job_id = preview.get("job_id")
        if isinstance(job_id, str) and job_id:
            return job_id
        nested = preview["data"].get("result")
        if isinstance(nested, dict):
            nested_job_id = nested.get("job_id") or nested.get("holder_job_id")
            if isinstance(nested_job_id, str) and nested_job_id:
                return nested_job_id
        return None

    start_ts = float(entries[0].get("timestamp") or 0.0)
    previews = [_preview(entry, start_ts, entry_index=index) for index, entry in enumerate(entries)]
    end_ts = float(entries[-1].get("timestamp") or start_ts)
    duration_s = max(0.0, end_ts - start_ts)
    llm_rounds = 0
    llm_failures = 0
    llm_prompt_tokens = 0
    llm_completion_tokens = 0
    llm_tool_rounds = 0
    planned_tool_counts: dict[str, int] = {}
    executed_tool_counts: dict[str, int] = {}
    counted_tool_calls: set[str] = set()
    expert_counts: dict[str, int] = {}
    signal_counts: dict[str, int] = {}
    latest_context_packet: Optional[dict[str, Any]] = None
    latest_llm_input: Optional[dict[str, Any]] = None
    latest_context_by_wake: dict[int, dict[str, Any]] = {}
    llm_turns: list[dict[str, Any]] = []
    expert_runs: dict[str, dict[str, Any]] = {}

    def _ensure_llm_turn(preview: dict[str, Any]) -> dict[str, Any]:
        wake = _safe_int(preview["data"].get("wake"))
        attempt = _safe_int(preview["data"].get("attempt"))
        incomplete_key = wake is None or attempt is None
        if not (preview.get("label") == "llm_input" and incomplete_key):
            for turn in reversed(llm_turns):
                if turn.get("_completed"):
                    continue
                if wake is not None and turn.get("wake") != wake:
                    continue
                if attempt is not None and turn.get("attempt") != attempt:
                    continue
                return turn
        turn = {
            "turn_index": len(llm_turns) + 1,
            "wake": wake,
            "attempt": attempt,
            "timestamp": preview["timestamp"],
            "elapsed_s": preview["elapsed_s"],
            "status": "pending",
            "input_messages": [],
            "input_tools": [],
            "context_packet": latest_context_by_wake.get(wake) if wake is not None else latest_context_packet,
            "response_text": None,
            "reasoning_content": None,
            "tool_calls_detail": [],
            "usage": {},
            "error": None,
            "error_type": None,
            "event_log": [],
            "_completed": False,
        }
        llm_turns.append(turn)
        return turn

    def _ensure_expert_run(job_id: str, preview: dict[str, Any]) -> dict[str, Any]:
        run = expert_runs.get(job_id)
        if run is None:
            run = {
                "job_id": job_id,
                "expert_type": preview.get("expert_type") or None,
                "started_at": None,
                "started_elapsed_s": None,
                "config": None,
                "events": [],
                "signals": [],
                "tool_results": [],
                "latest_signal": None,
                "terminal_signal": None,
            }
            expert_runs[job_id] = run
        if not run.get("expert_type") and preview.get("expert_type"):
            run["expert_type"] = preview.get("expert_type")
        return run

    for entry, preview in zip(entries, previews):
        event = entry.get("event")
        data = preview["data"]
        if event == "llm_succeeded":
            llm_rounds += 1
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            llm_prompt_tokens += int(usage.get("prompt_tokens") or 0)
            llm_completion_tokens += int(usage.get("completion_tokens") or 0)
            tool_calls = data.get("tool_calls_detail") if isinstance(data.get("tool_calls_detail"), list) else []
            if tool_calls:
                llm_tool_rounds += 1
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                tool_name = str(tool_call.get("name") or "").strip()
                if tool_name:
                    planned_tool_counts[tool_name] = planned_tool_counts.get(tool_name, 0) + 1
        elif event == "llm_failed":
            llm_failures += 1
        elif event == "context_snapshot":
            packet = data.get("packet")
            if isinstance(packet, dict):
                latest_context_packet = packet
                wake = _safe_int(data.get("wake"))
                if wake is not None:
                    latest_context_by_wake[wake] = packet
        elif event == "llm_input":
            latest_llm_input = data

        if event in {"tool_execute", "tool_execute_completed", "tool_execute_failed"}:
            tool_name = str(
                data.get("tool_name")
                or data.get("name")
                or data.get("tool")
                or ""
            ).strip()
            if tool_name:
                tool_call_id = str(data.get("tool_call_id") or "").strip()
                if event == "tool_execute":
                    count_key = tool_call_id or f"tool_execute:{preview['timestamp']}:{tool_name}"
                    if count_key not in counted_tool_calls:
                        counted_tool_calls.add(count_key)
                        executed_tool_counts[tool_name] = executed_tool_counts.get(tool_name, 0) + 1
                elif tool_call_id and tool_call_id not in counted_tool_calls:
                    counted_tool_calls.add(tool_call_id)
                    executed_tool_counts[tool_name] = executed_tool_counts.get(tool_name, 0) + 1

        if event == "job_started":
            expert_type = str(data.get("expert_type") or "").strip()
            if expert_type:
                expert_counts[expert_type] = expert_counts.get(expert_type, 0) + 1

        if event == "expert_signal":
            expert_type = str(data.get("expert_type") or "").strip()
            if expert_type:
                expert_counts[expert_type] = expert_counts.get(expert_type, 0) + 1
            signal_kind = str(data.get("signal_kind") or "").strip()
            if signal_kind:
                signal_counts[signal_kind] = signal_counts.get(signal_kind, 0) + 1

        if event == "llm_input":
            turn = _ensure_llm_turn(preview)
            turn["status"] = "running"
            turn["input_messages"] = list(data.get("messages") or [])
            turn["input_tools"] = list(data.get("tools") or [])
            turn["context_packet"] = latest_context_by_wake.get(turn.get("wake")) or latest_context_packet
            turn["event_log"].append(preview)
        elif event == "llm_succeeded":
            turn = _ensure_llm_turn(preview)
            turn["status"] = "succeeded"
            turn["model"] = data.get("model")
            turn["usage"] = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            turn["response_text"] = data.get("response_text")
            turn["reasoning_content"] = data.get("reasoning_content")
            turn["tool_calls_detail"] = list(data.get("tool_calls_detail") or [])
            turn["event_log"].append(preview)
            turn["_completed"] = True
        elif event in {"llm_call_error", "llm_failed", "llm_empty_output"}:
            turn = _ensure_llm_turn(preview)
            turn["status"] = "failed"
            turn["error_type"] = data.get("error_type") or event
            turn["error"] = data.get("error") or data.get("last_error") or preview["message"]
            turn["event_log"].append(preview)
            if event == "llm_failed":
                turn["_completed"] = True
        elif event == "llm_reasoning" and llm_turns:
            llm_turns[-1]["event_log"].append(preview)

        related_job_id = _related_job_id(preview)
        if related_job_id:
            run = _ensure_expert_run(related_job_id, preview)
            if event == "job_started":
                run["started_at"] = preview["timestamp"]
                run["started_elapsed_s"] = preview["elapsed_s"]
                run["config"] = data.get("config")
            if event in {
                "job_started",
                "job_paused",
                "job_resumed",
                "job_aborted",
                "expert_signal",
                "resource_granted",
                "resource_revoked",
                "signal_routed",
            }:
                run["events"].append(preview)
            if event == "expert_signal":
                run["signals"].append(preview)
                run["latest_signal"] = preview
                if preview.get("signal_kind") == "task_complete" or preview.get("result") in {
                    "succeeded",
                    "failed",
                    "partial",
                    "aborted",
                }:
                    run["terminal_signal"] = preview
            if event in {"tool_execute_completed", "tool_execute_failed"}:
                run["tool_results"].append(
                    {
                        "timestamp": preview["timestamp"],
                        "elapsed_s": preview["elapsed_s"],
                        "label": preview["label"],
                        "message": preview["message"],
                        "tool_name": data.get("tool_name") or data.get("name") or data.get("tool"),
                        "result": data.get("result"),
                        "error": data.get("error"),
                    }
                )

    tool_counts = executed_tool_counts or planned_tool_counts

    blockers = [
        preview
        for entry, preview in zip(entries, previews)
        if (
            preview["level"] in {"WARN", "ERROR"}
            or preview["label"] in {"job_aborted", "task_failed", "tool_execute_failed", "wake_cycle_error"}
            or (preview["label"] == "expert:resource_lost")
            or (preview["label"] == "expert:risk_alert")
            or (
                preview["label"] == "expert:task_complete"
                and str(_entry_data(entry).get("result")) in {"failed", "partial", "aborted"}
            )
        )
    ]

    highlights = [
        preview
        for preview in previews
        if preview["label"] in {
            "task_created",
            "job_started",
            "task_completed",
            "expert:progress",
            "expert:target_found",
            "expert:task_complete",
            "signal_routed",
            "llm_succeeded",
            "tool_execute_completed",
        }
    ]

    player_visible = [
        preview
        for entry, preview in zip(entries, previews)
        if preview["label"] in {
            "task_completed",
            "task_message_registered",
            "task_warning",
            "task_info",
            "query_response_sent",
            "player_notification_sent",
            "adjutant_response_sent",
        }
        or entry.get("component") == "adjutant"
    ]

    timeline = [
        _timeline_item(preview, start_ts)
        for preview in previews
        if preview["label"] in {
            "task_created",
            "job_started",
            "task_completed",
            "expert:progress",
            "expert:resource_lost",
            "expert:target_found",
            "expert:task_complete",
            "signal_routed",
            "llm_succeeded",
            "llm_failed",
            "tool_execute_failed",
            "wake_cycle_error",
        }
    ]

    last_transition = None
    for preview in reversed(highlights):
        if preview["label"] in {"task_completed", "expert:task_complete", "job_aborted", "job_started"}:
            last_transition = preview
            break
    if last_transition is None:
        last_transition = previews[-1]

    lifecycle_events = [
        {
            "timestamp": preview["timestamp"],
            "elapsed_s": preview["elapsed_s"],
            "component": preview["component"],
            "level": preview["level"],
            "label": preview["label"],
            "message": preview["message"],
            "task_id": preview["task_id"],
            "job_id": preview["job_id"],
            "expert_type": preview["expert_type"],
            "signal_kind": preview["signal_kind"],
            "result": preview["result"],
        }
        for preview in previews
    ][-500:]

    summary = "任务记录已加载"
    for entry in reversed(entries):
        data = _entry_data(entry)
        if entry.get("event") == "task_completed":
            summary = str(data.get("summary") or entry.get("message") or summary)
            break
        if entry.get("event") == "expert_signal" and data.get("signal_kind") == "task_complete":
            summary = str(data.get("summary") or entry.get("message") or summary)
            break
    else:
        if blockers:
            summary = blockers[-1]["message"]
        elif highlights:
            summary = highlights[-1]["message"]
        else:
            summary = previews[-1]["message"]

    if current_status_line and current_runtime:
        current_status = str(current_runtime.get("status") or "")
        if current_status not in {"succeeded", "failed", "aborted", "partial"}:
            summary = current_status_line

    debug: dict[str, Any] = {}
    if isinstance(latest_context_packet, dict):
        context_runtime_facts = latest_context_packet.get("runtime_facts")
        context_jobs = latest_context_packet.get("jobs")
        context_signals = latest_context_packet.get("recent_signals")
        context_events = latest_context_packet.get("recent_events")
        debug["latest_context"] = {
            "job_count": len(context_jobs) if isinstance(context_jobs, list) else 0,
            "signal_count": len(context_signals) if isinstance(context_signals, list) else 0,
            "event_count": len(context_events) if isinstance(context_events, list) else 0,
            "other_task_count": len(latest_context_packet.get("other_active_tasks") or []),
            "open_decision_count": len(latest_context_packet.get("open_decisions") or []),
            "runtime_fact_keys": sorted((context_runtime_facts or {}).keys())[:12]
            if isinstance(context_runtime_facts, dict)
            else [],
            "packet": latest_context_packet,
        }
    if isinstance(latest_llm_input, dict):
        debug["latest_llm_input"] = {
            "message_count": len(latest_llm_input.get("messages") or []),
            "tool_count": len(latest_llm_input.get("tools") or []),
            "attempt": int(latest_llm_input.get("attempt", 0) or 0),
            "wake": int(latest_llm_input.get("wake", 0) or 0),
            "messages": latest_llm_input.get("messages") or [],
            "tools": latest_llm_input.get("tools") or [],
        }

    def _compact_request(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "request_id": str(item.get("request_id") or ""),
            "reservation_id": str(item.get("reservation_id") or ""),
            "task_id": str(item.get("task_id") or ""),
            "task_label": str(item.get("task_label") or ""),
            "unit_type": str(item.get("unit_type") or ""),
            "queue_type": str(item.get("queue_type") or ""),
            "count": int(item.get("count", 0) or 0),
            "fulfilled": int(item.get("fulfilled", 0) or 0),
            "remaining_count": int(item.get("remaining_count", 0) or 0),
            "blocking": bool(item.get("blocking", True)),
            "min_start_package": int(item.get("min_start_package", 1) or 1),
            "bootstrap_job_id": str(item.get("bootstrap_job_id") or ""),
            "bootstrap_task_id": str(item.get("bootstrap_task_id") or ""),
            "reservation_status": str(item.get("reservation_status") or ""),
            "reason": str(item.get("reason") or ""),
            "disabled_producers": list(item.get("disabled_producers") or []),
        }

    def _compact_reservation(item: dict[str, Any]) -> dict[str, Any]:
        assigned = list(item.get("assigned_actor_ids") or [])
        produced = list(item.get("produced_actor_ids") or [])
        return {
            "reservation_id": str(item.get("reservation_id") or ""),
            "request_id": str(item.get("request_id") or ""),
            "task_id": str(item.get("task_id") or ""),
            "task_label": str(item.get("task_label") or ""),
            "unit_type": str(item.get("unit_type") or ""),
            "queue_type": str(item.get("queue_type") or ""),
            "count": int(item.get("count", 0) or 0),
            "remaining_count": int(item.get("remaining_count", 0) or 0),
            "status": str(item.get("status") or ""),
            "blocking": bool(item.get("blocking", True)),
            "min_start_package": int(item.get("min_start_package", 1) or 1),
            "start_released": bool(item.get("start_released", False)),
            "bootstrap_job_id": str(item.get("bootstrap_job_id") or ""),
            "bootstrap_task_id": str(item.get("bootstrap_task_id") or ""),
            "reason": str(item.get("reason") or ""),
            "assigned_count": len(assigned),
            "produced_count": len(produced),
        }

    unit_pipeline = {"unfulfilled_requests": [], "unit_reservations": []}
    latest_runtime_facts = (
        latest_context_packet.get("runtime_facts")
        if isinstance(latest_context_packet, dict)
        else {}
    )
    latest_requests = latest_runtime_facts.get("unfulfilled_requests") if isinstance(latest_runtime_facts, dict) else None
    latest_reservations = latest_runtime_facts.get("unit_reservations") if isinstance(latest_runtime_facts, dict) else None
    if isinstance(latest_requests, list):
        unit_pipeline["unfulfilled_requests"] = [
            _compact_request(item)
            for item in latest_requests
            if isinstance(item, dict) and str(item.get("task_id") or "") == task_id
        ]
    if isinstance(latest_reservations, list):
        unit_pipeline["unit_reservations"] = [
            _compact_reservation(item)
            for item in latest_reservations
            if isinstance(item, dict) and str(item.get("task_id") or "") == task_id
        ]
    if live_runtime_facts and (
        not unit_pipeline["unfulfilled_requests"] or not unit_pipeline["unit_reservations"]
    ):
        if not unit_pipeline["unfulfilled_requests"]:
            current_requests = live_runtime_facts.get("unfulfilled_requests")
            if isinstance(current_requests, list):
                unit_pipeline["unfulfilled_requests"] = [
                    _compact_request(item)
                    for item in current_requests
                    if isinstance(item, dict) and str(item.get("task_id") or "") == task_id
                ]
        if not unit_pipeline["unit_reservations"]:
            current_reservations = live_runtime_facts.get("unit_reservations")
            if isinstance(current_reservations, list):
                unit_pipeline["unit_reservations"] = [
                    _compact_reservation(item)
                    for item in current_reservations
                    if isinstance(item, dict) and str(item.get("task_id") or "") == task_id
                ]
    if not unit_pipeline["unit_reservations"]:
        runtime_reservations = runtime_state.get("unit_reservations") if isinstance(runtime_state, dict) else None
        if isinstance(runtime_reservations, list):
            unit_pipeline["unit_reservations"] = [
                _compact_reservation(item)
                for item in runtime_reservations
                if isinstance(item, dict) and str(item.get("task_id") or "") == task_id
            ]

    return {
        "task_id": task_id,
        "summary": summary,
        "status_line": current_status_line,
        "entry_count": len(entries),
        "duration_s": round(duration_s, 1),
        "last_transition": last_transition,
        "timeline": _dedupe(timeline, limit=12),
        "lifecycle_events": lifecycle_events,
        "expert_runs": sorted(
            expert_runs.values(),
            key=lambda item: (
                float(item["started_at"]) if item.get("started_at") is not None else float("inf"),
                item["job_id"],
            ),
        ),
        "llm_turns": [
            {
                key: value
                for key, value in turn.items()
                if key not in {"_completed", "event_log"}
            }
            for turn in llm_turns
        ],
        "unit_pipeline": unit_pipeline,
        "blockers": _dedupe(blockers, limit=4),
        "highlights": _dedupe(highlights, limit=6),
        "player_visible": _dedupe(player_visible, limit=5),
        "llm": {
            "rounds": llm_rounds,
            "failures": llm_failures,
            "prompt_tokens": llm_prompt_tokens,
            "completion_tokens": llm_completion_tokens,
            "tool_rounds": llm_tool_rounds,
        },
        "tools": [
            {"name": name, "count": count}
            for name, count in sorted(tool_counts.items(), key=lambda item: (-item[1], item[0]))[:6]
        ],
        "experts": [
            {"name": name, "count": count}
            for name, count in sorted(expert_counts.items(), key=lambda item: (-item[1], item[0]))[:6]
        ],
        "signals": [
            {"name": name, "count": count}
            for name, count in sorted(signal_counts.items(), key=lambda item: (-item[1], item[0]))[:6]
        ],
        "current_runtime": current_runtime,
        "debug": debug,
    }
