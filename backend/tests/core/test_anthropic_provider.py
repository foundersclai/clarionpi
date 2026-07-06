"""AnthropicProvider: cost-ceil math, prefix pricing, construction guard, canned completion.

Everything here is offline except the one ``@pytest.mark.integration`` live test, which is
skipped unless ``ANTHROPIC_API_KEY`` is set. The unit tests monkeypatch ``httpx.Client.post`` so
no network is touched, and assert the ceil semantics of :func:`_cost_cents` exactly (undercounting
is the failure mode the meter exists to prevent).
"""

from __future__ import annotations

import math
from typing import Any

import httpx
import pytest

from app.core.config import get_settings
from app.core.llm_provider import (
    _DEFAULT_PRICE,
    AnthropicProvider,
    ProviderNotConfigured,
    _cost_cents,
    get_llm_provider,
)

# --------------------------------------------------------------------------------------
# _cost_cents — ceil per direction, never round/floor
# --------------------------------------------------------------------------------------


def test_zero_tokens_cost_zero() -> None:
    assert _cost_cents("claude-sonnet-5", 0, 0) == 0


def test_one_token_costs_at_least_one_cent_when_price_positive() -> None:
    # sonnet-5 input price is 300 cents / 1M tokens -> 1 token = ceil(300/1e6) = 1 cent.
    assert _cost_cents("claude-sonnet-5", 1, 0) == 1
    # output price 1500 cents / 1M -> 1 output token still ceils to 1 cent.
    assert _cost_cents("claude-sonnet-5", 0, 1) == 1


def test_cost_ceils_each_direction_then_sums() -> None:
    # Choose token counts whose exact cost is fractional in BOTH directions, so a floor/round
    # would disagree with ceil. sonnet-5 = (300, 1500) cents/MTok.
    input_tokens, output_tokens = 3334, 667
    expected = math.ceil(input_tokens * 300 / 1_000_000) + math.ceil(
        output_tokens * 1500 / 1_000_000
    )
    # in: ceil(1.0002) = 2 ; out: ceil(1.0005) = 2 ; total 4.
    assert expected == 4
    assert _cost_cents("claude-sonnet-5", input_tokens, output_tokens) == 4


def test_exact_million_tokens_is_whole_price() -> None:
    # No ceil rounding when it divides evenly: 1M input @ 300 = 300, 1M output @ 1500 = 1500.
    assert _cost_cents("claude-sonnet-5", 1_000_000, 1_000_000) == 300 + 1500


# --------------------------------------------------------------------------------------
# prefix price matching
# --------------------------------------------------------------------------------------


def test_long_dated_model_id_matches_prefix() -> None:
    # A full dated id resolves to the same price as the "claude-sonnet-5" prefix.
    long_id = "claude-sonnet-5-20260203"
    assert _cost_cents(long_id, 1_000_000, 0) == _cost_cents("claude-sonnet-5", 1_000_000, 0)
    assert _cost_cents(long_id, 1_000_000, 0) == 300


def test_haiku_and_opus_prefixes_price_distinctly() -> None:
    assert _cost_cents("claude-haiku-4-5", 1_000_000, 0) == 100
    assert _cost_cents("claude-opus-4-8", 1_000_000, 0) == 1500


def test_unknown_model_uses_most_expensive_default() -> None:
    in_price, out_price = _DEFAULT_PRICE
    assert (in_price, out_price) == (3000, 15000)
    assert _cost_cents("gpt-4o", 1_000_000, 1_000_000) == in_price + out_price


# --------------------------------------------------------------------------------------
# construction guard
# --------------------------------------------------------------------------------------


def test_constructor_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderNotConfigured):
        AnthropicProvider()


def test_constructor_raises_with_blank_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    with pytest.raises(ProviderNotConfigured):
        AnthropicProvider()


# --------------------------------------------------------------------------------------
# complete() against a canned /v1/messages payload (no network)
# --------------------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def test_complete_joins_text_and_counts_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    get_settings.cache_clear()

    payload = {
        "content": [
            {"type": "text", "text": "Hello, "},
            {"type": "thinking", "thinking": "ignored"},  # non-text block must be skipped
            {"type": "text", "text": "world."},
        ],
        "usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    }

    captured: dict[str, Any] = {}

    def fake_post(self: httpx.Client, url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        captured["json"] = kwargs["json"]
        return _FakeResponse(payload)

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    provider = AnthropicProvider()
    result = provider.complete(stage="extract", model="claude-sonnet-5", prompt="hi")

    assert result.text == "Hello, world."
    assert result.input_tokens == 1_000_000
    assert result.output_tokens == 1_000_000
    # sonnet-5 = (300, 1500) cents/MTok on exactly 1M each.
    assert result.cost_cents == 300 + 1500

    # Wire contract: endpoint, required headers, and body shape.
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "sk-test"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["json"]["model"] == "claude-sonnet-5"
    assert captured["json"]["max_tokens"] == get_settings().llm_max_output_tokens
    assert captured["json"]["messages"] == [{"role": "user", "content": "hi"}]


# --------------------------------------------------------------------------------------
# factory wiring (lru_cached — clear it)
# --------------------------------------------------------------------------------------


def test_factory_selects_anthropic_when_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    get_llm_provider.cache_clear()
    try:
        provider = get_llm_provider()
        assert isinstance(provider, AnthropicProvider)
    finally:
        get_llm_provider.cache_clear()


def test_factory_anthropic_without_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    get_llm_provider.cache_clear()
    try:
        with pytest.raises(ProviderNotConfigured):
            get_llm_provider()
    finally:
        get_llm_provider.cache_clear()


# --------------------------------------------------------------------------------------
# live smoke — skipped unless a key is present
# --------------------------------------------------------------------------------------


@pytest.mark.integration
def test_live_haiku_completion_returns_text_and_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    get_settings.cache_clear()
    provider = AnthropicProvider()
    result = provider.complete(
        stage="smoke",
        model="claude-haiku-4-5",
        prompt="Write a one-sentence haiku about the sea.",
    )
    assert result.text.strip() != ""
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.cost_cents > 0
