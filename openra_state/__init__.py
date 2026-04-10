"""Compatibility exports for the legacy ``openra_state`` package.

Keep this package import-light.  The capability/data layer now imports
``openra_state.data`` from inside ``openra_api.game_api``; eager re-export of
``GameAPI`` here would create a circular import during package initialization.
"""

from __future__ import annotations

from openra_api.models import (
    Actor,
    ControlPoint,
    ControlPointQueryResult,
    FrozenActor,
    Location,
    MapQueryResult,
    MatchInfoQueryResult,
    PlayerBaseInfo,
    ScreenInfoResult,
    TargetsQueryParam,
)

__all__ = [
    "GameAPI",
    "GameAPIError",
    "Location",
    "TargetsQueryParam",
    "Actor",
    "MapQueryResult",
    "FrozenActor",
    "ControlPoint",
    "ControlPointQueryResult",
    "MatchInfoQueryResult",
    "PlayerBaseInfo",
    "ScreenInfoResult",
]


def __getattr__(name: str):
    if name in {"GameAPI", "GameAPIError"}:
        from openra_api.game_api import GameAPI, GameAPIError

        return {"GameAPI": GameAPI, "GameAPIError": GameAPIError}[name]
    raise AttributeError(name)
