from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ExecutionResult:
    success: bool
    message: str
    observations: str = ""
    code: str = ""
    error: Optional[str] = None
    raw_result: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "observations": self.observations,
            "code": self.code,
            "error": self.error,
            "raw_result": self.raw_result,
        }
