from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging_system
from session_browser import build_session_history_payload


def setup_function() -> None:
    logging_system.clear()
    logging_system.stop_persistence_session()


def test_session_history_payload_includes_structured_query_response_entries() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = logging_system.start_persistence_session(tmpdir, session_name="query-history")
        logger = logging_system.get_logger("dashboard_publish")
        logger.info(
            "历史副官回复",
            event="adjutant_response_sent",
            content="历史副官回复",
            response_type="command",
            ok=True,
            task_id="t_cap",
            existing_task_id="t_cap",
        )
        logger.info(
            "历史追问回复",
            event="query_response_sent",
            response_text="历史追问回复",
            response_type="reply",
            ok=False,
            task_id="t_task",
            message_id="m_1",
        )
        logging_system.stop_persistence_session()

        payload = build_session_history_payload(tmpdir, session_dir=session_dir)

    query_responses = payload["query_response_entries"]
    assert [item["answer"] for item in query_responses] == ["历史副官回复", "历史追问回复"]
    assert query_responses[0]["response_type"] == "command"
    assert query_responses[0]["ok"] is True
    assert query_responses[0]["task_id"] == "t_cap"
    assert query_responses[0]["existing_task_id"] == "t_cap"
    assert query_responses[1]["response_type"] == "reply"
    assert query_responses[1]["ok"] is False
    assert query_responses[1]["task_id"] == "t_task"
    assert query_responses[1]["message_id"] == "m_1"


def test_session_history_payload_includes_structured_task_message_entries() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = logging_system.start_persistence_session(tmpdir, session_name="task-message-history")
        logger = logging_system.get_logger("kernel")
        logger.info(
            "Task message registered",
            event="task_message_registered",
            task_id="t_hist",
            message_id="msg_info",
            message_type="task_info",
            content="保持推进",
            priority=60,
        )
        logger.info(
            "Task message registered",
            event="task_message_registered",
            task_id="t_hist",
            message_id="msg_done",
            message_type="task_complete_report",
            content="侦察完成",
            priority=80,
        )
        logger.info(
            "需要确认",
            event="task_question",
            task_id="t_hist",
            message_id="msg_q",
            content="是否继续侦察？",
            options=["继续", "停止"],
            timeout_s=15,
            default_option="继续",
        )
        logging_system.stop_persistence_session()

        payload = build_session_history_payload(tmpdir, session_dir=session_dir)

    task_messages = payload["task_message_entries"]
    assert [item["message_type"] for item in task_messages] == [
        "task_info",
        "task_complete_report",
        "task_question",
    ]
    assert task_messages[0]["message_id"] == "msg_info"
    assert task_messages[0]["content"] == "保持推进"
    assert task_messages[0]["priority"] == 60
    assert task_messages[1]["message_id"] == "msg_done"
    assert task_messages[1]["content"] == "侦察完成"
    assert task_messages[1]["priority"] == 80
    assert task_messages[2]["message_id"] == "msg_q"
    assert task_messages[2]["content"] == "需要确认"
    assert task_messages[2]["options"] == ["继续", "停止"]
    assert task_messages[2]["timeout_s"] == 15.0
    assert task_messages[2]["default_option"] == "继续"


def test_session_history_payload_includes_structured_notification_entries() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = logging_system.start_persistence_session(tmpdir, session_name="notification-history")
        logger = logging_system.get_logger("dashboard_publish")
        logger.info(
            "任务已取消",
            event="player_notification_sent",
            content="任务已取消",
            task_id="t_hist",
            data={"task_id": "t_hist"},
        )
        logger.info(
            "全局提醒",
            event="player_notification_sent",
            content="全局提醒",
            data={},
        )
        logging_system.stop_persistence_session()

        payload = build_session_history_payload(tmpdir, session_dir=session_dir)

    notifications = payload["notification_entries"]
    assert [item["content"] for item in notifications] == ["任务已取消", "全局提醒"]
    assert notifications[0]["task_id"] == "t_hist"
    assert notifications[1]["task_id"] == ""


def test_session_history_payload_returns_empty_structured_lists_without_session() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        payload = build_session_history_payload(tmpdir, session_dir=None)

    assert payload["log_entries"] == []
    assert payload["benchmark_records"] == []
    assert payload["player_visible_entries"] == []
    assert payload["notification_entries"] == []
    assert payload["query_response_entries"] == []
    assert payload["task_message_entries"] == []
