from __future__ import annotations
from typing import Dict, List, Set, Any, Optional
from the_seed.utils import LogManager
from openra_api.data.dataset import DATASET, CN_NAME_MAP

logger = LogManager.get_logger()

class StructureData:
    """
    Structure metadata provider using `dataset.py` as the source of truth.
    Replaces the legacy YAML-based loading.
    """
    
    _BASE_PROVIDER_IDS = {"fact", "const"} # "const" just in case
    
    # Reverse map for Chinese Name -> ID (Cached)
    _CN_TO_ID: Dict[str, str] = {}

    @classmethod
    def _ensure_init(cls):
        if not cls._CN_TO_ID:
            # Build reverse map from CN_NAME_MAP
            # CN_NAME_MAP is ID -> CN Name (e.g. "FACT": "建造厂")
            # We want "建造厂" -> "fact"
            for u_id, cn_name in CN_NAME_MAP.items():
                cls._CN_TO_ID[cn_name] = u_id.lower()
            
            # Also ensure DATASET is loaded (it is loaded on import)
            pass

    @classmethod
    def _resolve_id(cls, type_name: str) -> Optional[str]:
        """Resolve a type name (English ID or Chinese Name) to a normalized lowercase ID."""
        cls._ensure_init()
        if not type_name:
            return None
            
        lower_name = type_name.lower()
        
        # 1. Check if it's already a valid ID in DATASET (case-insensitive)
        if lower_name in DATASET:
            return lower_name
            
        # 2. Check if it's a Chinese Name
        if type_name in cls._CN_TO_ID:
            return cls._CN_TO_ID[type_name]
            
        return None

    @classmethod
    def is_valid_structure(cls, type_name: str) -> bool:
        """
        Check if the type corresponds to a valid structure (Building).
        """
        u_id = cls._resolve_id(type_name)
        if not u_id:
            return False
            
        # Check in DATASET
        info = DATASET.get(u_id)
        if info and info.category == "Building":
            return True
            
        return False

    @classmethod
    def get_info(cls, type_name: str) -> Dict[str, Any]:
        """
        Get structure info. Returns a dict to maintain compatibility with legacy API.
        Keys: is_base_provider, power_usage, cost, etc.
        """
        u_id = cls._resolve_id(type_name)
        if not u_id:
            return {}
            
        info = DATASET.get(u_id)
        result = {}
        
        if info:
            result["type"] = info.id.lower()
            result["cost"] = info.cost
            result["power_usage"] = info.power # DATASET uses 'power' (pos/neg), legacy used 'power_usage'
            
            # Infer properties
            if u_id in cls._BASE_PROVIDER_IDS:
                result["is_base_provider"] = True
                    
        return result
