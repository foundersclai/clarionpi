"""SSE wire formatting — the one place a server-sent event is serialized.

The event name is constrained to the :class:`~app.models.enums.SseEvent` vocabulary by type:
``format_sse`` accepts only an ``SseEvent`` member, so a stray ``agent_reasoning`` /
``agent_thinking`` event *cannot* be emitted. That exclusion is a design rule, not an oversight
— internal-reasoning events leak design and go unused by the UI (04 §4: "no
``agent_reasoning``/``agent_thinking`` events"). Payloads are JSON; ids/replay are a later wave.
"""

from __future__ import annotations

import json
from typing import Any

from app.models.enums import SseEvent


def format_sse(event: SseEvent, data: dict[str, Any]) -> str:
    """Serialize one SSE frame: ``event: <name>\\ndata: <json>\\n\\n``.

    ``event`` must be an :class:`~app.models.enums.SseEvent` member — passing anything else is a
    ``TypeError``, which is how the no-internal-reasoning-events rule is enforced at the wire
    boundary rather than trusted to callers.
    """
    if not isinstance(event, SseEvent):
        raise TypeError(
            f"SSE event must be an SseEvent member (the closed 04 §4 vocabulary), got {event!r}"
        )
    return f"event: {event.value}\ndata: {json.dumps(data)}\n\n"
