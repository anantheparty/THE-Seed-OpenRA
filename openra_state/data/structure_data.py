from __future__ import annotations
from typing import Dict, Any, Optional
from .dataset import DATASET, CN_NAME_MAP


class StructureData:
    _BASE_PROVIDER_IDS = {"fact", "const"}
    _CN_TO_ID: Dict[str, str] = {}

    @classmethod
    def _ensure_init(cls) -> None:
        if not cls._CN_TO_ID:
            for u_id, cn_name in CN_NAME_MAP.items():
                cls._CN_TO_ID[cn_name] = u_id.lower()

    @classmethod
    def _resolve_id(cls, type_name: str) -> Optional[str]:
        cls._ensure_init()
        if not type_name:
            return None
        lower_name = type_name.lower()
        if lower_name in DATASET:
            return lower_name
        if type_name in cls._CN_TO_ID:
            return cls._CN_TO_ID[type_name]
        return None

    @classmethod
    def is_valid_structure(cls, type_name: str) -> bool:
        u_id = cls._resolve_id(type_name)
        info = DATASET.get(u_id)
        return bool(info and info.category == "Building")

    @classmethod
    def get_info(cls, type_name: str) -> Dict[str, Any]:
        u_id = cls._resolve_id(type_name)
        if not u_id:
            return {}
        info = DATASET.get(u_id)
        if not info:
            return {}
        result: Dict[str, Any] = {
            "type": info.id.lower(),
            "cost": info.cost,
            "power_usage": info.power,
        }
        if u_id in cls._BASE_PROVIDER_IDS:
            result["is_base_provider"] = True
        return result
