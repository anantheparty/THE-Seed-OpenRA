"""Tests for singleton shared queue manager."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from queue_manager import QueueManager, QueueManagerConfig


class MockWorldModel:
    def __init__(self, queues):
        self.queues = queues

    def query(self, query_type: str, params=None):
        del params
        if query_type != "production_queues":
            raise ValueError(query_type)
        return self.queues


class MockGameAPI:
    def __init__(self) -> None:
        self.place_calls: list[dict] = []

    def place_building(self, queue_type: str, location=None, owner_actor_id=None) -> None:
        self.place_calls.append(
            {"queue_type": queue_type, "location": location, "owner_actor_id": owner_actor_id}
        )


def test_queue_manager_auto_places_ready_building_after_timeout() -> None:
    notifications = []
    manager = QueueManager(
        world_model=MockWorldModel(
            {
                "Building": {
                    "queue_type": "Building",
                    "has_ready_item": True,
                    "items": [
                        {"name": "barr", "display_name": "兵营", "done": True, "owner_actor_id": 3},
                    ],
                }
            }
        ),
        game_api=MockGameAPI(),
        notify=lambda kind, content, **kwargs: notifications.append((kind, content, kwargs)),
        config=QueueManagerConfig(mode="auto_place", ready_timeout_s=5.0),
    )

    manager.tick(now=100.0)
    assert notifications == []
    assert manager.game_api.place_calls == []

    manager.tick(now=106.0)
    assert manager.game_api.place_calls == [
        {"queue_type": "Building", "location": None, "owner_actor_id": 3}
    ]
    assert notifications[-1][0] == "queue_auto_placed"
    print("  PASS: queue_manager_auto_places_ready_building_after_timeout")


def test_queue_manager_warn_mode_does_not_place() -> None:
    notifications = []
    api = MockGameAPI()
    manager = QueueManager(
        world_model=MockWorldModel(
            {
                "Building": {
                    "queue_type": "Building",
                    "has_ready_item": True,
                    "items": [
                        {"name": "dome", "display_name": "雷达站", "done": True, "owner_actor_id": 3},
                    ],
                }
            }
        ),
        game_api=api,
        notify=lambda kind, content, **kwargs: notifications.append((kind, content, kwargs)),
        config=QueueManagerConfig(mode="warn", ready_timeout_s=5.0),
    )

    manager.tick(now=100.0)
    manager.tick(now=106.0)

    assert api.place_calls == []
    assert notifications[-1][0] == "queue_ready_stuck"
    print("  PASS: queue_manager_warn_mode_does_not_place")


if __name__ == "__main__":
    test_queue_manager_auto_places_ready_building_after_timeout()
    test_queue_manager_warn_mode_does_not_place()
    print("All queue manager tests passed.")
