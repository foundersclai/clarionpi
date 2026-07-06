"""The metered LLM client — the single door for every model call (invariant 12).

:class:`MeteredLLMClient` is the *only* sanctioned path from application code to a provider.
Each :meth:`~MeteredLLMClient.complete` call, in order:

1. loads-or-creates the matter's budget (default cap from settings),
2. refuses up front with :class:`~app.core.matter_budget.BudgetExceededError` if the matter is
   already at cap — **the provider is not called**,
3. calls the provider,
4. writes an :class:`~app.models.orm.LlmCall` ledger row — on **every attempt**, including a
   failure *after* the provider was invoked (a zero-cost row records the attempt),
5. increments ``spent_cents``,
6. emits an audit ``budget_warning`` event the first time spend crosses 80%,
7. returns the :class:`~app.core.llm_provider.CompletionResult`.

This is the TM lesson made structural: partial wiring undercounts, so the meter is wired from
day 1 and there is no side door — no unmetered provider handle exists.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.audit import record_event
from app.core.llm_provider import CompletionResult, LLMProvider
from app.core.matter_budget import (
    assert_within_budget,
    commit_spend,
    load_or_create_budget,
)
from app.models.enums import SseEvent
from app.models.orm import LlmCall


def _record_ledger_row(
    session: Session,
    *,
    firm_id: uuid.UUID,
    matter_id: uuid.UUID,
    stage: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_cents: int,
) -> LlmCall:
    """Insert one metering ledger row and return it (unflushed)."""
    call = LlmCall(
        firm_id=firm_id,
        matter_id=matter_id,
        stage=stage,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_cents=cost_cents,
    )
    session.add(call)
    return call


class MeteredLLMClient:
    """Wraps a provider so every completion is budget-checked and ledgered.

    Bound to a single ``(firm_id, matter_id)`` — the metering context — plus the session the
    ledger/budget rows are written on. Construct one per matter run; never call a provider
    directly.
    """

    def __init__(
        self,
        provider: LLMProvider,
        session: Session,
        firm_id: uuid.UUID,
        matter_id: uuid.UUID,
    ) -> None:
        self._provider = provider
        self._session = session
        self._firm_id = firm_id
        self._matter_id = matter_id

    def complete(self, *, stage: str, model: str, prompt: str) -> CompletionResult:
        """Meter, budget-check, and run one completion. See the module docstring for order."""
        budget = load_or_create_budget(
            self._session, firm_id=self._firm_id, matter_id=self._matter_id
        )
        # Refuse BEFORE calling the provider — a capped matter never reaches the model.
        assert_within_budget(budget)

        try:
            result = self._provider.complete(stage=stage, model=model, prompt=prompt)
        except Exception:
            # Failure after (or during) the provider call still records the attempt: a
            # zero-cost ledger row, so the meter never silently drops a call.
            _record_ledger_row(
                self._session,
                firm_id=self._firm_id,
                matter_id=self._matter_id,
                stage=stage,
                model=model,
                input_tokens=0,
                output_tokens=0,
                cost_cents=0,
            )
            raise

        _record_ledger_row(
            self._session,
            firm_id=self._firm_id,
            matter_id=self._matter_id,
            stage=stage,
            model=model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_cents=result.cost_cents,
        )
        crossed_warn = commit_spend(budget, cost_cents=result.cost_cents)
        if crossed_warn:
            record_event(
                self._session,
                firm_id=self._firm_id,
                actor_id=None,
                event_kind=SseEvent.BUDGET_WARNING.value,
                payload={
                    "matter_id": str(self._matter_id),
                    "spent_cents": budget.spent_cents,
                    "cap_cents": budget.cap_cents,
                },
            )
        return result
