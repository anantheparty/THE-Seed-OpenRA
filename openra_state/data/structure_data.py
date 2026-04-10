from __future__ import annotations
from typing import Dict, Any, Optional
from .dataset import DATASET, cn_name_to_unit_id
from openra_api.production_names import production_name_unit_id


class StructureData:
    _BASE_PROVIDER_IDS = {"fact", "const"}

    @classmethod
    def _resolve_id(cls, type_name: str) -> Optional[str]:
        if not type_name:
            return None
        resolved = cn_name_to_unit_id(type_name)
        if resolved:
            return resolved
        lower_name = type_name.lower()
        if lower_name in DATASET:
            return lower_name
        return production_name_unit_id(type_name)

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
