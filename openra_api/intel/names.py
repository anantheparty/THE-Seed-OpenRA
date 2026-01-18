from __future__ import annotations

from typing import Optional

from .rules import DEFAULT_NAME_ALIASES


def normalize_unit_name(name: Optional[str]) -> str:
    """将 RPC 返回的同义名归一化为内部规范名。"""
    if not name:
        return "未知"
    return DEFAULT_NAME_ALIASES.get(name, name)


