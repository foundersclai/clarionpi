"""View-models — the only shapes the wire ever sees (04 invariant 11).

Handlers return these, never ORM rows: the view-model is the contract with the frontend, and AI
overlays (none yet at M0) would live only here on responses, never echoed back on a submit. A
:class:`MatterView` is built from an ORM :class:`~app.models.orm.Matter` via
:func:`matter_to_view`, which rehydrates the stored ``sol_candidates`` JSON into typed
:class:`~app.models.schemas.DeadlineCandidate` objects so the deadline banner data is validated,
not raw JSON.
"""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, Field

from app.models.enums import ClaimType, GateState
from app.models.orm import Matter
from app.models.schemas import DeadlineCandidate


class MatterView(BaseModel):
    """The matter shape returned by the matter endpoints (create + fetch).

    ``deadline_candidates`` are the rules-computed SOL / notice-of-claim deadlines the FE renders
    as a non-dismissible banner until the attorney confirms them at G1 (invariant 4).
    """

    id: uuid.UUID
    client_display_name: str
    claim_type: ClaimType
    jurisdiction: str
    incident_date: date
    gate_state: GateState
    registry_version: int
    deadline_candidates: list[DeadlineCandidate] = Field(default_factory=list)


def matter_to_view(matter: Matter) -> MatterView:
    """Project an ORM :class:`~app.models.orm.Matter` into its wire :class:`MatterView`.

    The persisted ``sol_candidates`` JSON is validated back into
    :class:`~app.models.schemas.DeadlineCandidate` models here, so a malformed stored candidate
    surfaces as a validation error rather than leaking raw JSON to the client.
    """
    candidates = [DeadlineCandidate.model_validate(raw) for raw in matter.sol_candidates]
    return MatterView(
        id=matter.id,
        client_display_name=matter.client_display_name,
        claim_type=ClaimType(matter.claim_type),
        jurisdiction=matter.jurisdiction,
        incident_date=matter.incident_date,
        gate_state=GateState(matter.gate_state),
        registry_version=matter.registry_version,
        deadline_candidates=candidates,
    )
