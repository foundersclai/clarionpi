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

from dataclasses import dataclass
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
