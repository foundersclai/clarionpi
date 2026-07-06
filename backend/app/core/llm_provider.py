"""LLM provider abstraction — the port every model call travels through.

A provider is anything that satisfies :class:`LLMProvider`: given a stage, model, and prompt,
it returns a :class:`CompletionResult` carrying the text *and* the token/cost accounting the
metering ledger needs. Returning a structured result (not a bare ``str``) keeps the metered
client from having to guess token counts.

At M0 there is no live model, so the wired provider is :class:`NullProvider`, which raises
:class:`ProviderNotConfigured`. The important structural fact is not the provider but the
*door*: :class:`~app.core.llm_telemetry.MeteredLLMClient` is the only sanctioned call path
(invariant 12), so no unmetered handle exists anywhere.
"""

from __future__ import annotations

import math
import os
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol, runtime_checkable

import httpx

from app.core.config import get_settings


class ProviderNotConfigured(Exception):
    """Raised by a provider that has no live backend (e.g. :class:`NullProvider` at M0)."""


@dataclass(frozen=True)
class CompletionResult:
    """A provider's response plus its metering facts.

    ``cost_cents`` is integer cents (money discipline — never a float); token counts feed the
    :class:`~app.models.orm.LlmCall` ledger row.
    """

    text: str
    input_tokens: int
    output_tokens: int
    cost_cents: int


@runtime_checkable
class LLMProvider(Protocol):
    """The provider port. One method: complete a prompt for a stage/model."""

    def complete(self, *, stage: str, model: str, prompt: str) -> CompletionResult:
        """Run a completion and return text + token/cost accounting."""
        ...


class NullProvider:
    """The M0 provider: there is no live model, so every call is refused, loudly.

    Wiring this (rather than leaving the provider ``None``) means an accidental model call at
    M0 fails with a typed error at the provider boundary instead of a confusing ``NoneType``
    error deeper in the stack.
    """

    def complete(self, *, stage: str, model: str, prompt: str) -> CompletionResult:
        raise ProviderNotConfigured(
            f"no live LLM provider configured at M0 (stage={stage!r}, model={model!r})"
        )


class ScriptedProvider:
    """A deterministic provider for tests and offline dev.

    Returns queued :class:`CompletionResult`s (or *raises* queued exceptions) in FIFO order,
    and records every ``(stage, model, prompt)`` it was asked to complete. Exhausting the
    script raises :class:`ProviderNotConfigured`, so an unexpected extra call fails loudly at
    the provider boundary rather than returning stale data.
    """

    def __init__(self, script: Sequence[CompletionResult | Exception]) -> None:
        self._queue: deque[CompletionResult | Exception] = deque(script)
        self.calls: list[tuple[str, str, str]] = []

    def complete(self, *, stage: str, model: str, prompt: str) -> CompletionResult:
        self.calls.append((stage, model, prompt))
        if not self._queue:
            raise ProviderNotConfigured(
                f"ScriptedProvider script exhausted (stage={stage!r}, model={model!r})"
            )
        nxt = self._queue.popleft()
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


# (input, output) integer cents per million tokens, prefix-matched on the model id. A model id
# like "claude-sonnet-5-20260203" matches the "claude-sonnet-5" prefix.
_PRICES_CENTS_PER_MTOK: dict[str, tuple[int, int]] = {
    "claude-sonnet-5": (300, 1500),
    "claude-haiku-4-5": (100, 500),
    "claude-opus": (1500, 7500),
    "claude-fable": (3000, 15000),
}
# An unknown model is priced like the most expensive tier: the meter must never *undercount*
# (the TM lesson — an unknown model that silently prices at zero hides spend).
_DEFAULT_PRICE = (3000, 15000)


def _price_for(model: str) -> tuple[int, int]:
    """Prefix-match ``model`` against the price table; unknown -> the most-expensive default."""
    for prefix, price in _PRICES_CENTS_PER_MTOK.items():
        if model.startswith(prefix):
            return price
    return _DEFAULT_PRICE


def _cost_cents(model: str, input_tokens: int, output_tokens: int) -> int:
    """Integer-cent cost of a call, **ceiled per direction**.

    ``ceil(tokens * price / 1_000_000)`` for input and output separately, then summed. Ceil,
    never round or floor: undercounting is the exact failure mode the meter exists to prevent,
    so 1 token at a positive price costs at least 1 cent.
    """
    in_price, out_price = _price_for(model)
    in_cost = math.ceil(input_tokens * in_price / 1_000_000)
    out_cost = math.ceil(output_tokens * out_price / 1_000_000)
    return in_cost + out_cost


class AnthropicProvider:
    """A live provider: a direct ``httpx`` client for the Anthropic ``/v1/messages`` API.

    Still a metered-door-only backend — :class:`~app.core.llm_telemetry.MeteredLLMClient` is the
    only sanctioned caller (invariant 12); nothing else may hold this handle. The API key is read
    from ``ANTHROPIC_API_KEY`` at construction and :class:`ProviderNotConfigured` is raised if it
    is unset or blank, so a misconfiguration fails at wiring time rather than on the first call.

    No retries live here: the metered client ledgers every attempt, and retry policy belongs to
    the callers that own the budget context.
    """

    _BASE_URL = "https://api.anthropic.com/v1/messages"
    _ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, *, timeout_seconds: float = 120.0) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key is None or api_key.strip() == "":
            raise ProviderNotConfigured(
                "AnthropicProvider requires ANTHROPIC_API_KEY (unset or blank)"
            )
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def complete(self, *, stage: str, model: str, prompt: str) -> CompletionResult:
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": self._ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        body = {
            "model": model,
            "max_tokens": get_settings().llm_max_output_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        with httpx.Client(timeout=self._timeout_seconds) as client:
            response = client.post(self._BASE_URL, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()
        text = "".join(block["text"] for block in data["content"] if block.get("type") == "text")
        usage = data["usage"]
        input_tokens = int(usage["input_tokens"])
        output_tokens = int(usage["output_tokens"])
        return CompletionResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_cents=_cost_cents(model, input_tokens, output_tokens),
        )


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    """The wired provider for app composition. ``LLM_PROVIDER`` env selects ``null`` (default) or
    ``anthropic``.

    ``null`` (default) refuses every call via :class:`ProviderNotConfigured` — classification and
    extraction degrade to the review queue, visible not silent. ``anthropic`` wires a live
    :class:`AnthropicProvider` (requires ``ANTHROPIC_API_KEY``), so S2 evals can run with a key.
    """
    # Read LLM_PROVIDER from the environment directly: config.py owns Settings, but this is a
    # deployment selector, so it earns no Settings field.
    value = os.environ.get("LLM_PROVIDER", "null")
    if value == "null":
        return NullProvider()
    if value == "anthropic":
        return AnthropicProvider()
    raise ProviderNotConfigured(f"unknown LLM_PROVIDER {value!r}")
