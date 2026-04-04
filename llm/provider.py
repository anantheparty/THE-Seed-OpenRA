"""LLM model abstraction layer — swap providers in one line.

Usage:
    provider = QwenProvider()           # production
    provider = MockProvider(responses=[...])  # testing
    response = await provider.chat(messages)
"""

import asyncio
import os
import importlib.util
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, TypeVar


@dataclass
class ToolCall:
    """A single tool call from the LLM."""

    id: str
    name: str
    arguments: str  # JSON string


@dataclass
class LLMResponse:
    """Unified response from any LLM provider."""

    text: Optional[str] = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)  # prompt_tokens, completion_tokens
    model: str = ""
    raw: Any = None  # provider-specific raw response


def _require_dependency(module_name: str, provider_name: str) -> None:
    if importlib.util.find_spec(module_name) is not None:
        return
    raise RuntimeError(
        f"LLM provider '{provider_name}' requires Python package '{module_name}'. "
        f"Install it in the backend runtime environment before starting main.py."
    )


def _socks_proxy_configured() -> bool:
    proxy_keys = ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")
    for key in proxy_keys:
        value = os.environ.get(key, "").strip().lower()
        if value.startswith("socks"):
            return True
    return False


def _require_socks_support_if_needed(provider_name: str) -> None:
    if not _socks_proxy_configured():
        return
    if importlib.util.find_spec("socksio") is not None:
        return
    raise RuntimeError(
        f"LLM provider '{provider_name}' is running behind a SOCKS proxy, but Python package 'socksio' "
        "is not installed in the backend runtime environment. Install it before starting main.py."
    )


# ---------------------------------------------------------------------------
# Timeout + retry helper
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503})
_RETRY_DELAYS: list[float] = [1.0, 2.0]
_MAX_RETRIES: int = 2

_T = TypeVar("_T")


async def _call_with_retry(
    coro_fn: Callable[[], Awaitable[_T]],
    *,
    timeout_s: float,
) -> _T:
    """Wrap an LLM API call with per-attempt timeout and retry on transient errors.

    Retry policy:
    - Retryable HTTP status codes: 429, 500, 502, 503 (exponential backoff: 1s, 2s)
    - asyncio.TimeoutError: raised immediately, no retry
    - Non-retryable HTTP status (400, 401, 403, 404, ...): raised immediately
    - Network/unknown errors (no status_code): retried up to max_retries
    """
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return await asyncio.wait_for(coro_fn(), timeout=timeout_s)
        except asyncio.TimeoutError:
            raise  # Timeout propagates immediately — caller decides what to do
        except Exception as e:
            status = getattr(e, "status_code", None)
            if status is not None and status not in _RETRYABLE_STATUS:
                raise  # Non-transient HTTP error — fail fast
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_DELAYS[attempt])
            else:
                raise
    raise RuntimeError("unreachable")  # appease type checkers


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """Abstract base for LLM providers. All LLM usage in the system goes through this."""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        max_tokens: int = 800,
        temperature: float = 0.7,
        timeout_s: float = 30.0,
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: OpenAI-format messages [{"role": "...", "content": "..."}].
            tools: Optional tool definitions (OpenAI function-calling format).
            max_tokens: Max tokens in response.
            temperature: Sampling temperature.
            timeout_s: Seconds before raising asyncio.TimeoutError (default 30).

        Returns:
            LLMResponse with text and/or tool_calls.
        """

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        max_tokens: int = 800,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Stream chat completion tokens. Default: falls back to non-streaming."""
        response = await self.chat(messages, tools, max_tokens, temperature)
        if response.text:
            yield response.text


# ---------------------------------------------------------------------------
# Qwen provider
# ---------------------------------------------------------------------------


class QwenProvider(LLMProvider):
    """Qwen via OpenAI-compatible API (DashScope)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "qwen-plus",
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("QWEN_API_KEY", "")
        self.base_url = base_url
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            _require_dependency("openai", "qwen")
            _require_socks_support_if_needed("qwen")
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        max_tokens: int = 800,
        temperature: float = 0.7,
        timeout_s: float = 30.0,
    ) -> LLMResponse:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        async def _do_call() -> LLMResponse:
            resp = await client.chat.completions.create(**kwargs)
            choice = resp.choices[0]
            tool_calls = []
            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    tool_calls.append(
                        ToolCall(
                            id=tc.id,
                            name=tc.function.name,
                            arguments=tc.function.arguments,
                        )
                    )
            return LLMResponse(
                text=choice.message.content,
                tool_calls=tool_calls,
                usage={
                    "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                    "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
                },
                model=resp.model or self.model,
                raw=resp,
            )

        return await _call_with_retry(_do_call, timeout_s=timeout_s)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        max_tokens: int = 800,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        stream_resp = await client.chat.completions.create(**kwargs)
        async for chunk in stream_resp:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


# ---------------------------------------------------------------------------
# DeepSeek provider
# ---------------------------------------------------------------------------


class DeepSeekProvider(LLMProvider):
    """DeepSeek via OpenAI-compatible API (api.deepseek.com)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = base_url
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            _require_dependency("openai", "deepseek")
            _require_socks_support_if_needed("deepseek")
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        max_tokens: int = 800,
        temperature: float = 0.7,
        timeout_s: float = 30.0,
    ) -> LLMResponse:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        async def _do_call() -> LLMResponse:
            resp = await client.chat.completions.create(**kwargs)
            choice = resp.choices[0]
            tool_calls = []
            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    tool_calls.append(
                        ToolCall(
                            id=tc.id,
                            name=tc.function.name,
                            arguments=tc.function.arguments,
                        )
                    )
            return LLMResponse(
                text=choice.message.content,
                tool_calls=tool_calls,
                usage={
                    "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                    "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
                },
                model=resp.model or self.model,
                raw=resp,
            )

        return await _call_with_retry(_do_call, timeout_s=timeout_s)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        max_tokens: int = 800,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        stream_resp = await client.chat.completions.create(**kwargs)
        async for chunk in stream_resp:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider. Requires `pip install anthropic`.

    Limitation: multi-turn tool-use transcript conversion (tool_result messages)
    is not yet implemented. Single-turn chat + tool calls work. Full multi-turn
    agentic loop support will be added in Phase 1 Task Agent development.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-20250514",
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            _require_dependency("anthropic", "anthropic")
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=self.api_key)
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        max_tokens: int = 800,
        temperature: float = 0.7,
        timeout_s: float = 30.0,
    ) -> LLMResponse:
        client = self._get_client()

        # Extract system message if present
        system = ""
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                chat_messages.append(msg)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": chat_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system

        # Convert OpenAI tool format to Anthropic format
        if tools:
            anthropic_tools = []
            for tool in tools:
                fn = tool.get("function", tool)
                anthropic_tools.append(
                    {
                        "name": fn["name"],
                        "description": fn.get("description", ""),
                        "input_schema": fn.get("parameters", {}),
                    }
                )
            kwargs["tools"] = anthropic_tools

        async def _do_call() -> LLMResponse:
            resp = await client.messages.create(**kwargs)
            text_parts = []
            tool_calls = []
            for block in resp.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    import json
                    tool_calls.append(
                        ToolCall(
                            id=block.id,
                            name=block.name,
                            arguments=json.dumps(block.input),
                        )
                    )
            return LLMResponse(
                text="\n".join(text_parts) if text_parts else None,
                tool_calls=tool_calls,
                usage={
                    "prompt_tokens": resp.usage.input_tokens,
                    "completion_tokens": resp.usage.output_tokens,
                },
                model=resp.model,
                raw=resp,
            )

        return await _call_with_retry(_do_call, timeout_s=timeout_s)


# ---------------------------------------------------------------------------
# Mock provider (testing only — no timeout/retry needed)
# ---------------------------------------------------------------------------


class MockProvider(LLMProvider):
    """Mock provider for testing — returns pre-set responses in sequence."""

    def __init__(self, responses: Optional[list[LLMResponse]] = None):
        self._responses = list(responses) if responses else []
        self._call_count = 0
        self.call_log: list[dict[str, Any]] = []

    def add_response(self, response: LLMResponse) -> None:
        self._responses.append(response)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        max_tokens: int = 800,
        temperature: float = 0.7,
        timeout_s: float = 30.0,
    ) -> LLMResponse:
        self.call_log.append(
            {
                "messages": messages,
                "tools": tools,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )

        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
        else:
            resp = LLMResponse(text="[mock] no more responses", model="mock")

        self._call_count += 1
        return resp
