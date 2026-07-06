"""Metered LLM client: ledger-per-call, budget refusal before the provider, 80% warn latch."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy.orm import Session

from app.core.llm_provider import CompletionResult
from app.core.llm_telemetry import MeteredLLMClient
from app.core.matter_budget import BudgetExceededError
from app.models.enums import GateState, SseEvent
from app.models.orm import AuditEvent, LlmCall, Matter, MatterBudget

from .conftest import make_firm


class FakeProvider:
    """Records calls and returns a fixed :class:`CompletionResult`."""

    def __init__(self, result: CompletionResult) -> None:
        self._result = result
        self.calls: list[dict[str, str]] = []

    def complete(self, *, stage: str, model: str, prompt: str) -> CompletionResult:
        self.calls.append({"stage": stage, "model": model, "prompt": prompt})
        return self._result


def _make_matter(session: Session, firm_id: uuid.UUID) -> Matter:
    matter = Matter(
        firm_id=firm_id,
        client_display_name="M",
        claim_type="mva",
        incident_date=dt.date(2026, 1, 15),
        jurisdiction="AZ",
        gate_state=GateState.CORPUS_PROCESSING.value,
        registry_version=0,
        sol_candidates=[],
    )
    session.add(matter)
    session.flush()
    return matter


def _budget(session: Session, matter_id: uuid.UUID) -> MatterBudget:
    return session.query(MatterBudget).filter(MatterBudget.matter_id == matter_id).one()


def test_ledger_row_written_per_call(session: Session) -> None:
    firm = make_firm(session, "Firm A")
    matter = _make_matter(session, firm.id)
    provider = FakeProvider(CompletionResult("hi", input_tokens=10, output_tokens=5, cost_cents=3))
    client = MeteredLLMClient(provider, session, firm.id, matter.id)

    client.complete(stage="classify", model="haiku", prompt="p1")
    client.complete(stage="extract_encounter", model="sonnet", prompt="p2")
    session.commit()

    rows = session.query(LlmCall).order_by(LlmCall.stage).all()
    assert [r.stage for r in rows] == ["classify", "extract_encounter"]
    assert all(r.cost_cents == 3 and r.matter_id == matter.id for r in rows)
    assert _budget(session, matter.id).spent_cents == 6


def test_budget_cap_refuses_before_provider_called(session: Session) -> None:
    firm = make_firm(session, "Firm A")
    matter = _make_matter(session, firm.id)
    # Pre-create an exhausted budget: spent == cap.
    session.add(
        MatterBudget(
            firm_id=firm.id, matter_id=matter.id, cap_cents=100, spent_cents=100, warned=False
        )
    )
    session.flush()
    provider = FakeProvider(CompletionResult("x", input_tokens=1, output_tokens=1, cost_cents=1))
    client = MeteredLLMClient(provider, session, firm.id, matter.id)

    with pytest.raises(BudgetExceededError):
        client.complete(stage="classify", model="haiku", prompt="p")

    assert provider.calls == []  # provider must NOT have been invoked
    assert session.query(LlmCall).count() == 0  # no ledger row on a pre-call refusal


def test_eighty_percent_crossing_latches_warned_and_audits(session: Session) -> None:
    firm = make_firm(session, "Firm A")
    matter = _make_matter(session, firm.id)
    session.add(
        MatterBudget(
            firm_id=firm.id, matter_id=matter.id, cap_cents=100, spent_cents=0, warned=False
        )
    )
    session.flush()
    # First call: 79 cents — below the 80% line, no warning.
    provider = FakeProvider(CompletionResult("x", input_tokens=1, output_tokens=1, cost_cents=79))
    client = MeteredLLMClient(provider, session, firm.id, matter.id)
    client.complete(stage="classify", model="haiku", prompt="p")
    session.commit()
    assert _budget(session, matter.id).warned is False
    assert _warning_events(session) == 0

    # Second call: +1 cent → 80/100 == 80%, crosses the threshold, warns exactly once.
    provider2 = FakeProvider(CompletionResult("x", input_tokens=1, output_tokens=1, cost_cents=1))
    MeteredLLMClient(provider2, session, firm.id, matter.id).complete(
        stage="classify", model="haiku", prompt="p"
    )
    session.commit()
    assert _budget(session, matter.id).warned is True
    assert _warning_events(session) == 1

    # Third call still over the line: no second warning (idempotent latch).
    provider3 = FakeProvider(CompletionResult("x", input_tokens=1, output_tokens=1, cost_cents=5))
    MeteredLLMClient(provider3, session, firm.id, matter.id).complete(
        stage="classify", model="haiku", prompt="p"
    )
    session.commit()
    assert _warning_events(session) == 1


def _warning_events(session: Session) -> int:
    return (
        session.query(AuditEvent)
        .filter(AuditEvent.event_kind == SseEvent.BUDGET_WARNING.value)
        .count()
    )
