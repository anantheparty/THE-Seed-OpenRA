from __future__ import annotations
import os
import yaml
from typing import Dict, List, Set, Any
from the_seed.utils import LogManager

logger = LogManager.get_logger()

class StructureData:
    """建筑元数据加载器"""
    
    _data: Dict[str, Any] = {}
    _valid_structure_types: Set[str] = set()
    _defense_types: Set[str] = set()
    _wall_types: Set[str] = set()

    @classmethod
    def load(cls, yaml_path: str = None):
        if not yaml_path:
            # Default path relative to project root
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            yaml_path = os.path.join(base_dir, "openra_api", "data", "structures.yaml")
            
        try:
            with open(yaml_path, 'r', encoding='utf-8') as f:
                content = yaml.safe_load(f)
                if content and "structures" in content:
                    cls._data = content["structures"]
                    
                    # Cache sets for fast lookup
                    for key, info in cls._data.items():
                        struct_type = info.get("type", "").lower()
                        if struct_type:
                            if info.get("is_wall"):
                                cls._wall_types.add(struct_type)
                            else:
                                cls._valid_structure_types.add(struct_type)
                                
                            if info.get("is_defense"):
                                cls._defense_types.add(struct_type)
                                
            logger.info(f"Loaded {len(cls._valid_structure_types)} structure types from {yaml_path}")
            
        except Exception as e:
            logger.error(f"Failed to load structure data: {e}")

    @classmethod
    def is_valid_structure(cls, type_name: str) -> bool:
        """是否是有效建筑（排除围墙）"""
        if not cls._data: cls.load()
        return type_name.lower() in cls._valid_structure_types

    @classmethod
    def is_wall(cls, type_name: str) -> bool:
        if not cls._data: cls.load()
        return type_name.lower() in cls._wall_types
        
    @classmethod
    def is_defense(cls, type_name: str) -> bool:
        if not cls._data: cls.load()
        return type_name.lower() in cls._defense_types

    @classmethod
    def get_info(cls, type_name: str) -> Dict[str, Any]:
        if not cls._data: cls.load()
        # Need to search values because keys are UPPERCASE YAML keys but type_name might be lowercase
        # Optimally we should map type->info during load.
        # For now, let's just loop or improve the cache structure.
        # The yaml structure is Key: { type: "lower" ... }
        # Let's map type -> info
        if not hasattr(cls, "_type_map"):
            cls._type_map = {}
            for k, v in cls._data.items():
                t = v.get("type", "").lower()
                if t: cls._type_map[t] = v
        
        return cls._type_map.get(type_name.lower(), {})
