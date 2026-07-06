"""ScriptedProvider: FIFO results, queued exceptions raised in order, exhaustion, call log."""

from __future__ import annotations

import pytest

from app.core.llm_provider import CompletionResult, ProviderNotConfigured, ScriptedProvider


def _result(text: str) -> CompletionResult:
    return CompletionResult(text=text, input_tokens=1, output_tokens=1, cost_cents=1)


def test_returns_results_in_fifo_order() -> None:
    provider = ScriptedProvider([_result("first"), _result("second")])
    assert provider.complete(stage="s", model="m", prompt="p1").text == "first"
    assert provider.complete(stage="s", model="m", prompt="p2").text == "second"


def test_queued_exception_is_raised_in_order() -> None:
    boom = RuntimeError("scripted failure")
    provider = ScriptedProvider([_result("ok"), boom])
    assert provider.complete(stage="s", model="m", prompt="p1").text == "ok"
    with pytest.raises(RuntimeError, match="scripted failure"):
        provider.complete(stage="s", model="m", prompt="p2")


def test_exhaustion_raises_provider_not_configured() -> None:
    provider = ScriptedProvider([_result("only")])
    provider.complete(stage="s", model="m", prompt="p1")
    with pytest.raises(ProviderNotConfigured):
        provider.complete(stage="s", model="m", prompt="p2")


def test_calls_are_recorded() -> None:
    provider = ScriptedProvider([_result("a"), _result("b")])
    provider.complete(stage="classify", model="haiku", prompt="p1")
    provider.complete(stage="extract", model="sonnet", prompt="p2")
    assert provider.calls == [
        ("classify", "haiku", "p1"),
        ("extract", "sonnet", "p2"),
    ]


def test_empty_script_exhausts_immediately_but_records_the_call() -> None:
    provider = ScriptedProvider([])
    with pytest.raises(ProviderNotConfigured):
        provider.complete(stage="s", model="m", prompt="p")
    assert provider.calls == [("s", "m", "p")]
