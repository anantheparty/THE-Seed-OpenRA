"""Tests for LLM provider timeout and retry logic (T10).

Tests _call_with_retry directly — no real API calls needed.
"""

from __future__ import annotations

import asyncio
import sys
import os
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.provider import _call_with_retry, _MAX_RETRIES, _RETRY_DELAYS, LLMResponse, MockProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeHTTPError(Exception):
    """Simulates an SDK HTTP error with a status_code attribute."""
    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def _succeeds(response: LLMResponse):
    async def _coro():
        return response
    return _coro


def _always_raises(exc: Exception):
    async def _coro():
        raise exc
    return _coro


def _raises_then_succeeds(exc: Exception, fail_times: int, response: LLMResponse):
    """Raises exc for first `fail_times` calls, then returns response."""
    calls = [0]
    async def _coro():
        calls[0] += 1
        if calls[0] <= fail_times:
            raise exc
        return response
    return _coro, calls


async def _run(coro_fn, *, timeout_s=5.0):
    return await _call_with_retry(coro_fn, timeout_s=timeout_s)


# ---------------------------------------------------------------------------
# Success cases
# ---------------------------------------------------------------------------

def test_success_no_retry():
    """Successful call returns immediately with no retries."""
    resp = LLMResponse(text="hello", model="mock")
    result = asyncio.run(_run(_succeeds(resp)))
    assert result.text == "hello"
    print("  PASS: success_no_retry")


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------

def test_timeout_raises_immediately():
    """asyncio.TimeoutError propagates immediately without retry."""
    sleep_calls = [0]

    async def _slow():
        sleep_calls[0] += 1
        await asyncio.sleep(10)
        return LLMResponse(text="never")

    try:
        asyncio.run(_run(_slow, timeout_s=0.05))
        assert False, "Expected TimeoutError"
    except asyncio.TimeoutError:
        pass
    # Should only have been called once (no retry on timeout)
    assert sleep_calls[0] == 1
    print("  PASS: timeout_raises_immediately")


# ---------------------------------------------------------------------------
# Non-retryable HTTP errors
# ---------------------------------------------------------------------------

def test_400_raises_immediately():
    """HTTP 400 is non-retryable — raised on first attempt."""
    calls = [0]
    async def _coro():
        calls[0] += 1
        raise _FakeHTTPError(400)

    try:
        asyncio.run(_run(_coro))
        assert False, "Expected _FakeHTTPError"
    except _FakeHTTPError as e:
        assert e.status_code == 400
    assert calls[0] == 1  # Only tried once
    print("  PASS: 400_raises_immediately")


def test_401_raises_immediately():
    """HTTP 401 auth error is non-retryable."""
    calls = [0]
    async def _coro():
        calls[0] += 1
        raise _FakeHTTPError(401)

    try:
        asyncio.run(_run(_coro))
        assert False
    except _FakeHTTPError:
        pass
    assert calls[0] == 1
    print("  PASS: 401_raises_immediately")


def test_404_raises_immediately():
    """HTTP 404 is non-retryable."""
    calls = [0]
    async def _coro():
        calls[0] += 1
        raise _FakeHTTPError(404)

    try:
        asyncio.run(_run(_coro))
        assert False
    except _FakeHTTPError:
        pass
    assert calls[0] == 1
    print("  PASS: 404_raises_immediately")


# ---------------------------------------------------------------------------
# Retryable HTTP errors
# ---------------------------------------------------------------------------

def test_429_retried_then_succeeds():
    """HTTP 429 is retried; succeeds on 2nd attempt."""
    resp = LLMResponse(text="ok after retry", model="mock")
    coro_fn, calls = _raises_then_succeeds(_FakeHTTPError(429), fail_times=1, response=resp)

    result = asyncio.run(_run(coro_fn, timeout_s=10.0))
    assert result.text == "ok after retry"
    assert calls[0] == 2  # 1 fail + 1 success
    print("  PASS: 429_retried_then_succeeds")


def test_500_retried_then_succeeds():
    """HTTP 500 is retried; succeeds on 2nd attempt."""
    resp = LLMResponse(text="ok", model="mock")
    coro_fn, calls = _raises_then_succeeds(_FakeHTTPError(500), fail_times=1, response=resp)

    result = asyncio.run(_run(coro_fn, timeout_s=10.0))
    assert result.text == "ok"
    assert calls[0] == 2
    print("  PASS: 500_retried_then_succeeds")


def test_503_retried_twice_then_raises():
    """HTTP 503: retried _MAX_RETRIES times, then raises."""
    calls = [0]
    async def _coro():
        calls[0] += 1
        raise _FakeHTTPError(503)

    try:
        asyncio.run(_run(_coro, timeout_s=10.0))
        assert False, "Expected _FakeHTTPError"
    except _FakeHTTPError as e:
        assert e.status_code == 503
    assert calls[0] == _MAX_RETRIES + 1  # 1 original + 2 retries
    print("  PASS: 503_retried_twice_then_raises")


def test_502_retried_then_succeeds():
    """HTTP 502 retried; succeeds on attempt 3 (after 2 failures)."""
    resp = LLMResponse(text="finally ok", model="mock")
    coro_fn, calls = _raises_then_succeeds(_FakeHTTPError(502), fail_times=2, response=resp)

    result = asyncio.run(_run(coro_fn, timeout_s=10.0))
    assert result.text == "finally ok"
    assert calls[0] == 3  # 2 fails + 1 success
    print("  PASS: 502_retried_then_succeeds")


# ---------------------------------------------------------------------------
# Network errors (no status_code)
# ---------------------------------------------------------------------------

def test_network_error_retried():
    """Exception without status_code (network error) is retried."""
    resp = LLMResponse(text="recovered", model="mock")
    coro_fn, calls = _raises_then_succeeds(ConnectionError("refused"), fail_times=1, response=resp)

    result = asyncio.run(_run(coro_fn, timeout_s=10.0))
    assert result.text == "recovered"
    assert calls[0] == 2
    print("  PASS: network_error_retried")


def test_network_error_exhausted():
    """Network error exhausts retries and raises."""
    calls = [0]
    async def _coro():
        calls[0] += 1
        raise ConnectionError("always fails")

    try:
        asyncio.run(_run(_coro, timeout_s=10.0))
        assert False
    except ConnectionError:
        pass
    assert calls[0] == _MAX_RETRIES + 1
    print("  PASS: network_error_exhausted")


# ---------------------------------------------------------------------------
# MockProvider accepts timeout_s parameter
# ---------------------------------------------------------------------------

def test_mock_provider_accepts_timeout_s():
    """MockProvider.chat() accepts timeout_s without error."""
    provider = MockProvider([LLMResponse(text="mocked", model="mock")])
    result = asyncio.run(provider.chat([{"role": "user", "content": "hi"}], timeout_s=5.0))
    assert result.text == "mocked"
    print("  PASS: mock_provider_accepts_timeout_s")


# ---------------------------------------------------------------------------
# Run all
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
