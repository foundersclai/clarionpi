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

import os
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol, runtime_checkable


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


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    """The wired provider for app composition. M1: LLM_PROVIDER env selects "null" (default).

    A live Anthropic provider lands with the BAA/vendor decision (S4); until then classify
    degrades to the review queue via ProviderNotConfigured — visible, not silent.
    """
    # Read LLM_PROVIDER from the environment directly: config.py owns Settings, but this is a
    # temporary selector (gone once the live provider lands at S4), so it earns no Settings field.
    value = os.environ.get("LLM_PROVIDER", "null")
    if value == "null":
        return NullProvider()
    raise ProviderNotConfigured(f"unknown LLM_PROVIDER {value!r}")
