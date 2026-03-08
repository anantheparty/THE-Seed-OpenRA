from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..utils import LogManager

logger = LogManager.get_logger()


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


StatusCallback = Callable[[str, str], None]


@dataclass
class ExecutorContext:
    api: Any = None
    raw_api: Any = None
    api_rules: str = ""
    runtime_globals: Dict[str, Any] = field(default_factory=dict)
    observe_fn: Optional[Callable[[], str]] = None
    status_callback: Optional[StatusCallback] = None
    history: List[Dict[str, Any]] = field(default_factory=list)
    max_history: int = 5


class SimpleExecutor:
    def __init__(self, codegen: Any = None, ctx: Optional[ExecutorContext] = None):
        self.codegen = codegen
        self.ctx = ctx or ExecutorContext()

    def _send_status(self, stage: str, detail: str = "") -> None:
        callback = getattr(self.ctx, "status_callback", None)
        if callable(callback):
            try:
                callback(stage, detail)
            except Exception:
                logger.warning("status_callback failed", exc_info=True)

    def run(self, command: str) -> ExecutionResult:
        if self.codegen is None:
            return ExecutionResult(
                success=False,
                message="SimpleExecutor is unavailable after the-seed removal; no codegen backend is configured.",
                error="executor_unconfigured",
            )

        self._send_status("observing", "正在观测游戏状态...")
        game_state = ""
        if callable(self.ctx.observe_fn):
            try:
                game_state = self.ctx.observe_fn()
            except Exception as exc:
                logger.warning("observe_fn failed: %s", exc)
                game_state = f"观测失败: {exc}"

        self._send_status("thinking", "AI 正在分析并生成代码...")
        gen_result = self.codegen.generate(
            command=command,
            game_state=game_state,
            api_rules=self.ctx.api_rules,
            history=None,
        )
        code = getattr(gen_result, "code", "") or ""
        if not code.strip():
            return ExecutionResult(success=False, message="LLM 返回了空代码", error="empty_code")
        self._send_status("executing", "正在执行生成的代码...")
        result = self._execute_code(code)
        self._record_history(command, code, result)
        return result

    def _execute_code(self, code: str) -> ExecutionResult:
        globals_dict: Dict[str, Any] = {"__builtins__": __builtins__, "logger": logger}
        globals_dict.update(self.ctx.runtime_globals)
        try:
            exec(code, globals_dict, globals_dict)
        except Exception as exc:
            logger.exception("SimpleExecutor execution failed")
            return ExecutionResult(
                success=False,
                message=f"代码执行失败: {exc}",
                code=code,
                error=str(exc),
            )
        result = globals_dict.get("__result__")
        if not isinstance(result, dict):
            return ExecutionResult(
                success=False,
                message="代码未设置 __result__ 或格式错误",
                code=code,
                error="missing_result",
            )
        return ExecutionResult(
            success=bool(result.get("success", False)),
            message=str(result.get("message", "")),
            observations=str(result.get("observations", "")),
            code=code,
            raw_result=result,
        )

    def _record_history(self, command: str, code: str, result: ExecutionResult) -> None:
        self.ctx.history.append(
            {
                "command": command,
                "code": code,
                "success": result.success,
                "message": result.message,
            }
        )
        while len(self.ctx.history) > self.ctx.max_history * 2:
            self.ctx.history.pop(0)
