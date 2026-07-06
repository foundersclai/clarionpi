"""SSE wire-shape: correct frame format; only the closed vocabulary is accepted."""

from __future__ import annotations

import json

import pytest

from app.api.sse_utils import format_sse
from app.models.enums import SseEvent


def test_format_sse_frame_shape() -> None:
    frame = format_sse(SseEvent.STATUS, {"phase": "phase0", "step": 1})
    assert frame == 'event: status\ndata: {"phase": "phase0", "step": 1}\n\n'
    # Terminated by the blank-line delimiter SSE requires.
    assert frame.endswith("\n\n")


def test_format_sse_data_is_valid_json() -> None:
    frame = format_sse(SseEvent.BUDGET_WARNING, {"spent": 80, "cap": 100})
    data_line = frame.splitlines()[1]
    assert json.loads(data_line.removeprefix("data: ")) == {"spent": 80, "cap": 100}


def test_format_sse_rejects_non_sse_event() -> None:
    # A raw string (e.g. an "agent_reasoning" event) is refused by type — the
    # no-internal-reasoning-events rule enforced at the wire boundary.
    with pytest.raises(TypeError):
        format_sse("agent_reasoning", {})  # type: ignore[arg-type]
