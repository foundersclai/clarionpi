"""The fact registry — the tokenizer spine (fact_registry / system_contract §2, 5, 10, 11).

One **per-matter namespace** of typed facts — ``[[FACT_n]]``, ``[[AMT_n]]``, ``[[CITE_n]]``,
``[[EX_n]]`` — each carrying ``value``, ``display_form``, anchors, verification status, and
source. This module is the **only minter of tokens** and the single resolution authority.

Boundaries this module holds:

* **[2]** Every token carries anchors; render resolution runs anchor integrity (a token whose
  anchor lands on a dedup-superseded document is not ``VERIFIED``).
* **[3 / money]** The registry **stores** ``[[AMT]]`` values (``snapshot_value_cents`` +
  ``ledger_ref`` + ``ledger_hash``) but **never computes** them — ``app.money.ledger`` owns the
  arithmetic. Drift is caught by re-hashing at render, not by mutating a stored value.
* **[5]** Prompt resolution exposes **only** ``display_form`` (Brain-2 never sees raw
  names/cites/amounts); render resolution adds value + anchors.
* **[10]** The registry is **derived state** — rebuildable from extractor rows / attorney
  elections / rules. Versioning makes rebuilds addressable; a ``token_id`` (e.g. ``FACT_12``)
  is a **stable fact-slot forever**, never recycled across versions.
* **[11]** An **orphan** (a token nothing resolves) renders as a :data:`SENTINEL` plus a loud
  log — never the raw token, never a guessed value. :func:`resolve_text_for_wire` is the one
  wire-facing helper and asserts nothing token-shaped survives it.

Transaction ownership: the version-bumping ``sync_*`` / ``mint_*`` entry points commit at the
end (they own a unit of work). The low-level :func:`bump_version` does **not** commit — its
caller owns the transaction.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.tenancy import tenant_add
from app.models.enums import DedupResolution, TokenKind, TokenSource, TokenStatus
from app.models.orm import (
    DedupDecision,
    FactToken,
    IncidentFacts,
    Matter,
    MedicalEncounter,
    RegistryVersion,
    User,
)
from app.models.schemas import AmountFact

# --------------------------------------------------------------------------------------
# Token grammar
# --------------------------------------------------------------------------------------

_KIND_PREFIX: dict[TokenKind, str] = {
    TokenKind.FACT: "FACT",
    TokenKind.AMOUNT: "AMT",
    TokenKind.CITATION: "CITE",
    TokenKind.EXHIBIT: "EX",
}
_PREFIX_KIND: dict[str, TokenKind] = {prefix: kind for kind, prefix in _KIND_PREFIX.items()}

# The one canonical token shape. Nothing matching this may leave a resolution path onto a wire.
TOKEN_RE = re.compile(r"\[\[(FACT|AMT|CITE|EX)_(\d+)\]\]")

# Deliberately NOT token-shaped (no ``[[..]]``): what a wire sees in place of an orphan, so a
# leaked sentinel can never be re-parsed as a real token.
SENTINEL = "[UNRESOLVED FACT]"

_LOG = logging.getLogger("clarionpi.registry")

# Version-bump reason vocabulary (the ``change_reason`` written on a new RegistryVersion).
_REASON_EXTRACTION = "extraction_sync"
_REASON_LEDGER = "ledger_sync"
_REASON_ATTORNEY = "attorney_fact"
_REASON_EXHIBIT = "exhibit_sync"


def token_str(kind: TokenKind, ordinal: int) -> str:
    """Render a token id, e.g. ``token_str(TokenKind.FACT, 7) == "[[FACT_7]]"``."""
    return f"[[{_KIND_PREFIX[kind]}_{ordinal}]]"


def parse_token(token: str) -> tuple[TokenKind, int]:
    """Inverse of :func:`token_str`. Raises ``ValueError`` on anything not a full token."""
    match = TOKEN_RE.fullmatch(token)
    if match is None:
        raise ValueError(f"not a token: {token!r}")
    return _PREFIX_KIND[match.group(1)], int(match.group(2))


def _token_id_inner(token: str) -> str:
    """The bare ``token_id`` stored on a row (``FACT_7``) from a full token (``[[FACT_7]]``)."""
    kind, ordinal = parse_token(token)
    return f"{_KIND_PREFIX[kind]}_{ordinal}"


# --------------------------------------------------------------------------------------
# Versioning
# --------------------------------------------------------------------------------------


def current_version(db: Session, *, matter: Matter) -> int:
    """The matter's current registry version (0 means no bump has happened yet)."""
    return matter.registry_version


def bump_version(db: Session, *, matter: Matter, reason: str) -> int:
    """Advance the matter to a new registry version and record its lineage.

    Inserts a :class:`RegistryVersion` row (``version = old + 1``, ``parent_version = old``,
    ``change_reason = reason``, ``frozen = False``) and sets ``matter.registry_version`` to it.

    **Does not commit** — the caller owns the transaction (the ``sync_*`` / ``mint_*`` entry
    points commit once at the end of their unit of work).
    """
    old = matter.registry_version
    new = old + 1
    row = RegistryVersion(
        matter_id=matter.id,
        version=new,
        frozen=False,
        parent_version=old,
        change_reason=reason,
    )
    tenant_add(db, row, matter.firm_id)
    matter.registry_version = new
    db.flush()
    return new


# --------------------------------------------------------------------------------------
# Minting / sync
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RegistrySyncOutcome:
    """Result of a mint/sync pass — exact counts plus the resulting version + whether it moved."""

    minted: int
    updated: int
    unchanged: int
    version: int
    bumped: bool


def _all_latest_rows(db: Session, *, matter: Matter) -> list[FactToken]:
    """Every ``FactToken`` for the matter, latest-version row per ``token_id``.

    A ``token_id`` may have several rows (one per version it was written at); we keep the one at
    the highest ``registry_version`` — the live fact-slot state.
    """
    rows = list(db.execute(select(FactToken).where(FactToken.matter_id == matter.id)).scalars())
    latest: dict[str, FactToken] = {}
    for row in rows:
        seen = latest.get(row.token_id)
        if seen is None or row.registry_version > seen.registry_version:
            latest[row.token_id] = row
    return list(latest.values())


def _next_ordinal(latest_rows: Sequence[FactToken]) -> int:
    """Next free ordinal in the matter's **single** shared namespace across all kinds.

    Token ids are never reused, so the next ordinal is one past the highest ordinal ever minted
    for the matter (regardless of kind) — this is what keeps ``FACT``/``AMT``/``CITE``/``EX``
    interleaved in one sequence.
    """
    highest = 0
    for row in latest_rows:
        _, ordinal = parse_token(f"[[{row.token_id}]]")
        highest = max(highest, ordinal)
    return highest + 1


def _latest_by_source_ref(latest_rows: Sequence[FactToken]) -> dict[str, FactToken]:
    """Index the latest rows by ``source_ref`` (skipping rows that carry none)."""
    return {row.source_ref: row for row in latest_rows if row.source_ref is not None}


def _anchor_document_ids(anchors: Sequence[dict]) -> list[uuid.UUID]:
    """Pull the ``document_id`` out of each anchor dict (tolerating str or UUID)."""
    out: list[uuid.UUID] = []
    for anchor in anchors:
        raw = anchor.get("document_id")
        if raw is None:
            continue
        out.append(raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw)))
    return out


def _anchors_pass_integrity(db: Session, *, matter: Matter, anchors: Sequence[dict]) -> bool:
    """Anchor-integrity check backing the extractor VERIFIED rule (inv 2).

    Passes iff ``anchors`` is non-empty **and** no anchor's document has been dedup-superseded
    (a :class:`DedupDecision` on that document with ``resolution == SUPERSEDED``). A superseded
    document has dropped out of the case, so a fact anchored only there is not verified.
    """
    doc_ids = _anchor_document_ids(anchors)
    if not doc_ids:
        return False
    superseded = set(
        db.execute(
            select(DedupDecision.document_id).where(
                DedupDecision.matter_id == matter.id,
                DedupDecision.document_id.in_(doc_ids),
                DedupDecision.resolution == DedupResolution.SUPERSEDED.value,
            )
        ).scalars()
    )
    return not any(doc_id in superseded for doc_id in doc_ids)


def _anchor_dicts(anchors: object) -> list[dict]:
    """Normalize a row's ``anchors`` JSON to a list of plain dicts (defensive copy)."""
    if not isinstance(anchors, list):
        return []
    return [dict(a) for a in anchors if isinstance(a, dict)]


def _rows_equivalent(
    existing: FactToken,
    *,
    value: object,
    display_form: str,
    anchors: Sequence[dict],
    status: TokenStatus,
) -> bool:
    """Whether an existing latest row already carries this exact content (idempotency test)."""
    return (
        existing.value == value
        and existing.display_form == display_form
        and _anchor_dicts(existing.anchors) == list(anchors)
        and existing.status == status.value
    )


@dataclass
class _Desired:
    """The content a source row wants minted, keyed by its deterministic ``source_ref``."""

    source_ref: str
    kind: TokenKind
    source: TokenSource
    display_form: str
    value: object
    anchors: list[dict]
    status: TokenStatus
    snapshot_value_cents: int | None = None
    ledger_ref: dict | None = None
    ledger_hash: str | None = None


def _apply_desired(
    db: Session,
    *,
    matter: Matter,
    desired: Sequence[_Desired],
    reason: str,
) -> RegistrySyncOutcome:
    """Shared mint/supersede engine for every source kind.

    For each desired item, against the LATEST row for its ``source_ref``:

    * no existing row -> **mint** a new ``token_id`` (next shared ordinal),
    * existing + identical content -> **unchanged**,
    * existing + differing content -> a **new row at the new version reusing the same
      ``token_id``** (supersession; the id is never recycled).

    Bumps the version **once** iff anything is minted or updated, writes every new row at that
    version, and commits. Nothing changed -> no bump, no commit needed (a read-only pass).
    """
    latest_rows = _all_latest_rows(db, matter=matter)
    by_ref = _latest_by_source_ref(latest_rows)
    next_ordinal = _next_ordinal(latest_rows)

    minted = 0
    updated = 0
    unchanged = 0
    to_write: list[_Desired] = []
    # token_id assigned to each write (existing id for supersede; freshly minted otherwise).
    write_token_ids: list[str] = []

    for item in desired:
        existing = by_ref.get(item.source_ref)
        if existing is None:
            write_token_ids.append(f"{_KIND_PREFIX[item.kind]}_{next_ordinal}")
            next_ordinal += 1
            to_write.append(item)
            minted += 1
        elif _rows_equivalent(
            existing,
            value=item.value,
            display_form=item.display_form,
            anchors=item.anchors,
            status=item.status,
        ):
            unchanged += 1
        else:
            write_token_ids.append(existing.token_id)
            to_write.append(item)
            updated += 1

    if not to_write:
        return RegistrySyncOutcome(
            minted=0,
            updated=0,
            unchanged=unchanged,
            version=matter.registry_version,
            bumped=False,
        )

    new_version = bump_version(db, matter=matter, reason=reason)
    for token_id, item in zip(write_token_ids, to_write, strict=True):
        row = FactToken(
            matter_id=matter.id,
            token_id=token_id,
            registry_version=new_version,
            kind=item.kind.value,
            value=item.value,
            display_form=item.display_form,
            anchors=item.anchors,
            status=item.status.value,
            source=item.source.value,
            source_ref=item.source_ref,
            snapshot_value_cents=item.snapshot_value_cents,
            ledger_ref=item.ledger_ref,
            ledger_hash=item.ledger_hash,
        )
        tenant_add(db, row, matter.firm_id)
    db.commit()
    return RegistrySyncOutcome(
        minted=minted,
        updated=updated,
        unchanged=unchanged,
        version=new_version,
        bumped=True,
    )


def _encounter_value(row: MedicalEncounter) -> dict:
    """The deterministic JSON ``value`` for an encounter — its business fields, order-stable."""
    return {
        "provider": row.provider,
        "facility": row.facility,
        "date_of_service": row.date_of_service.isoformat(),
        "encounter_type": row.encounter_type,
        "complaints": list(row.complaints),
        "findings": list(row.findings),
        "diagnoses": list(row.diagnoses),
        "procedures": list(row.procedures),
        "work_status": row.work_status,
    }


def _encounter_display_form(row: MedicalEncounter) -> str:
    """Deterministic display form for an encounter (fabrication-safe Brain-2 surface)."""
    return f"the {row.encounter_type} visit to {row.provider} on {row.date_of_service.isoformat()}"


def sync_extracted_facts(db: Session, *, matter: Matter) -> RegistrySyncOutcome:
    """Tokenize the matter's extracted facts — encounters + the incident row — idempotently.

    Scans :class:`MedicalEncounter` rows (ordered ``created_at, id``) and the matter's single
    :class:`IncidentFacts` row, minting/superseding one FACT token per source via
    ``source_ref`` (``encounter:<id>`` / ``incident:<id>``). Status is ``VERIFIED`` iff the
    row's anchors pass integrity (:func:`_anchors_pass_integrity`), else ``UNVERIFIED``.

    **Stale source rule:** a ``source_ref`` whose upstream row has vanished (e.g. a merge
    absorbed the encounter) is not deleted — on the next sync its slot gets a new version row
    with ``status=UNVERIFIED`` and its ``value`` / ``display_form`` unchanged. The fact-slot
    survives (resolution keeps working) and the stale-source condition is visible as the status
    drop. Bumps once (``extraction_sync``) iff anything changed; commits at the end.
    """
    desired: list[_Desired] = []

    encounters = list(
        db.execute(
            select(MedicalEncounter)
            .where(MedicalEncounter.matter_id == matter.id)
            .order_by(MedicalEncounter.created_at, MedicalEncounter.id)
        ).scalars()
    )
    live_refs: set[str] = set()
    for enc in encounters:
        anchors = _anchor_dicts(enc.anchors)
        status = (
            TokenStatus.VERIFIED
            if _anchors_pass_integrity(db, matter=matter, anchors=anchors)
            else TokenStatus.UNVERIFIED
        )
        source_ref = f"encounter:{enc.id}"
        live_refs.add(source_ref)
        desired.append(
            _Desired(
                source_ref=source_ref,
                kind=TokenKind.FACT,
                source=TokenSource.EXTRACTOR,
                display_form=_encounter_display_form(enc),
                value=_encounter_value(enc),
                anchors=anchors,
                status=status,
            )
        )

    incident = db.execute(
        select(IncidentFacts).where(IncidentFacts.matter_id == matter.id)
    ).scalar_one_or_none()
    if incident is not None:
        anchors = _anchor_dicts(incident.anchors)
        status = (
            TokenStatus.VERIFIED
            if _anchors_pass_integrity(db, matter=matter, anchors=anchors)
            else TokenStatus.UNVERIFIED
        )
        source_ref = f"incident:{incident.id}"
        live_refs.add(source_ref)
        desired.append(
            _Desired(
                source_ref=source_ref,
                kind=TokenKind.FACT,
                source=TokenSource.EXTRACTOR,
                display_form="the incident",
                value=dict(incident.payload),
                anchors=anchors,
                status=status,
            )
        )

    # Stale-source slots: a token whose source row is gone gets a fresh UNVERIFIED version row,
    # keeping its display_form/value so downstream resolution never orphans.
    for row in _all_latest_rows(db, matter=matter):
        if row.source != TokenSource.EXTRACTOR.value:
            continue
        if row.source_ref is None or not _is_extraction_ref(row.source_ref):
            continue
        if row.source_ref in live_refs:
            continue
        desired.append(
            _Desired(
                source_ref=row.source_ref,
                kind=TokenKind(row.kind),
                source=TokenSource.EXTRACTOR,
                display_form=row.display_form,
                value=row.value,
                anchors=_anchor_dicts(row.anchors),
                status=TokenStatus.UNVERIFIED,
            )
        )

    return _apply_desired(db, matter=matter, desired=desired, reason=_REASON_EXTRACTION)


def _is_extraction_ref(source_ref: str) -> bool:
    """Whether a ``source_ref`` is one this sync owns (encounter/incident)."""
    return source_ref.startswith("encounter:") or source_ref.startswith("incident:")


def mint_amounts(
    db: Session, *, matter: Matter, amounts: Sequence[AmountFact]
) -> RegistrySyncOutcome:
    """Register ``[[AMT]]`` facts handed over by ``app.money.ledger`` — store, never compute.

    Each :class:`AmountFact` becomes an ``AMOUNT`` token keyed by ``amt:<key>``, carrying the
    ledger's ``snapshot_value_cents`` / ``ledger_ref`` / ``ledger_hash`` and a
    ``value = {"cents": value_cents}``. Status is ``VERIFIED`` (ledger-derived; drift is caught
    by ``ledger_hash`` re-verification at render, not by status). Bumps once (``ledger_sync``)
    iff anything changed.

    Source pin: ``TokenSource.EXTRACTOR`` — amounts derive from extracted billing lines. The
    contract's source vocabulary (extractor|attorney|rules) has no exact "ledger" member;
    EXTRACTOR is the closest true provenance (the numbers trace to extracted bills).
    """
    desired = [
        _Desired(
            source_ref=f"amt:{a.key}",
            kind=TokenKind.AMOUNT,
            source=TokenSource.EXTRACTOR,
            display_form=a.display_form,
            value={"cents": a.value_cents},
            anchors=[],
            status=TokenStatus.VERIFIED,
            snapshot_value_cents=a.value_cents,
            ledger_ref=dict(a.ledger_ref),
            ledger_hash=a.ledger_hash,
        )
        for a in amounts
    ]
    return _apply_desired(db, matter=matter, desired=desired, reason=_REASON_LEDGER)


def mint_exhibits(
    db: Session, *, matter: Matter, entries: Sequence[Mapping[str, object]]
) -> RegistrySyncOutcome:
    """Register ``[[EX]]`` exhibit tokens for a matter's collated picks — mint, never resolve order.

    Each ``entry`` is a mapping with ``key`` (the source document id as a string), ``display_form``
    (e.g. ``"Exhibit 1 — bill.pdf"``), and ``anchors`` (a list of :class:`PageAnchor` dicts, one per
    included page). A token is keyed by ``exhibit:<key>`` so re-minting the same document's exhibit
    resolves to the same slot (idempotent). Kind ``EXHIBIT``, source ``ATTORNEY`` (a pick is a human
    election), status ``VERIFIED`` — the caller (``app.package.manifest.build_draft_manifest``) only
    hands over entries whose integrity already passed, so the token is verified on arrival. Value is
    ``{"document_id": <key>, "included_pages": [...]}`` (the pages pulled off the anchors), so a
    content change (added/removed page) supersedes the slot. Bumps once (``exhibit_sync``) iff
    anything changed; commits at the end — same idempotency/versioning machinery as
    :func:`mint_amounts`.
    """
    desired: list[_Desired] = []
    for entry in entries:
        key = str(entry["key"])
        anchors = _anchor_dicts(entry.get("anchors"))
        included_pages = [a["page"] for a in anchors if "page" in a]
        desired.append(
            _Desired(
                source_ref=f"exhibit:{key}",
                kind=TokenKind.EXHIBIT,
                source=TokenSource.ATTORNEY,
                display_form=str(entry["display_form"]),
                value={"document_id": key, "included_pages": included_pages},
                anchors=anchors,
                status=TokenStatus.VERIFIED,
            )
        )
    return _apply_desired(db, matter=matter, desired=desired, reason=_REASON_EXHIBIT)


def mint_attorney_fact(
    db: Session,
    *,
    matter: Matter,
    user: User,
    display_form: str,
    value: object,
    anchors: Sequence[dict] = (),
) -> FactToken:
    """Mint a single attorney-added FACT (the G1/G2a add path; v1 minimal).

    Source ``ATTORNEY``, status ``VERIFIED`` (verified-by-attorney), ``source_ref =
    attorney:<uuid4>`` so every add is its own slot. Bumps the version (``attorney_fact``) and
    commits; returns the new row.
    """
    new_version = bump_version(db, matter=matter, reason=_REASON_ATTORNEY)
    latest_rows = _all_latest_rows(db, matter=matter)
    ordinal = _next_ordinal(latest_rows)
    row = FactToken(
        matter_id=matter.id,
        token_id=f"{_KIND_PREFIX[TokenKind.FACT]}_{ordinal}",
        registry_version=new_version,
        kind=TokenKind.FACT.value,
        value=value,
        display_form=display_form,
        anchors=[dict(a) for a in anchors],
        status=TokenStatus.VERIFIED.value,
        source=TokenSource.ATTORNEY.value,
        source_ref=f"attorney:{uuid.uuid4()}",
    )
    tenant_add(db, row, matter.firm_id)
    db.commit()
    return row


# --------------------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolutionResult:
    """Render-mode resolution of one token.

    ``outcome`` ∈ {``ok``, ``orphan``, ``amt_mismatch``, ``unverified``, ``disputed``}.
    For an orphan, ``display_form`` is the :data:`SENTINEL` and value/anchors are empty.
    """

    token: str
    outcome: str
    display_form: str | None
    value: object | None
    anchors: tuple[dict, ...]


def _latest(db: Session, *, matter: Matter, token: str) -> FactToken | None:
    """The latest-version row for a token's ``token_id``, or ``None`` (an orphan)."""
    token_id = _token_id_inner(token)
    rows = list(
        db.execute(
            select(FactToken).where(
                FactToken.matter_id == matter.id,
                FactToken.token_id == token_id,
            )
        ).scalars()
    )
    if not rows:
        return None
    return max(rows, key=lambda r: r.registry_version)


def resolve_for_prompt(db: Session, *, matter: Matter, token: str) -> str:
    """Prompt-mode resolution: the token's ``display_form`` only (inv 5).

    Brain-2 never sees value or anchors here — only the fabrication-safe display string. A
    missing token returns the :data:`SENTINEL` plus a loud error log (inv 11); it never leaks
    the raw token or a guess.
    """
    row = _latest(db, matter=matter, token=token)
    if row is None:
        _LOG.error("orphan token %s for matter %s", token, matter.id)
        return SENTINEL
    return row.display_form


def resolve_for_render(
    db: Session,
    *,
    matter: Matter,
    token: str,
    live_ledger_hash: Callable[[dict], str] | None = None,
) -> ResolutionResult:
    """Render-mode resolution: value + anchors + integrity/verification outcome.

    Outcomes:

    * missing row -> ``orphan`` (:data:`SENTINEL`, empty value/anchors, loud log; a hard G3
      block downstream),
    * ``DISPUTED`` status -> ``disputed`` (blocks G3),
    * ``UNVERIFIED`` status -> ``unverified`` (display/value/anchors carried — visible, gated
      later),
    * ``AMOUNT`` with ``live_ledger_hash`` given -> re-hash ``ledger_ref``; a mismatch against
      the stored ``ledger_hash`` yields ``amt_mismatch`` (mechanical, span-patchable G3
      finding) with the snapshot value still exposed plus a ``ledger_mismatch`` flag in the
      value dict; a match yields ``ok``,
    * otherwise ``ok`` with display/value/anchors.
    """
    row = _latest(db, matter=matter, token=token)
    if row is None:
        _LOG.error("orphan token %s for matter %s", token, matter.id)
        return ResolutionResult(
            token=token,
            outcome="orphan",
            display_form=SENTINEL,
            value=None,
            anchors=(),
        )

    anchors = tuple(_anchor_dicts(row.anchors))

    if row.status == TokenStatus.DISPUTED.value:
        return ResolutionResult(
            token=token,
            outcome="disputed",
            display_form=row.display_form,
            value=row.value,
            anchors=anchors,
        )

    if row.status == TokenStatus.UNVERIFIED.value:
        return ResolutionResult(
            token=token,
            outcome="unverified",
            display_form=row.display_form,
            value=row.value,
            anchors=anchors,
        )

    if (
        row.kind == TokenKind.AMOUNT.value
        and live_ledger_hash is not None
        and row.ledger_hash is not None
    ):
        recomputed = live_ledger_hash(dict(row.ledger_ref or {}))
        if recomputed != row.ledger_hash:
            value: dict = {
                "cents": row.snapshot_value_cents,
                "ledger_mismatch": True,
                "stored_ledger_hash": row.ledger_hash,
                "live_ledger_hash": recomputed,
            }
            return ResolutionResult(
                token=token,
                outcome="amt_mismatch",
                display_form=row.display_form,
                value=value,
                anchors=anchors,
            )

    return ResolutionResult(
        token=token,
        outcome="ok",
        display_form=row.display_form,
        value=row.value,
        anchors=anchors,
    )


def resolve_text_for_wire(db: Session, *, matter: Matter, text: str) -> str:
    """Replace every token in ``text`` with its prompt-mode display form / :data:`SENTINEL`.

    This is the **one** helper wire-facing code uses, so a raw token can never escape onto a
    wire (inv 11). As a final safety net it asserts the result contains no token — a
    ``display_form`` that itself carried a token would be a data bug and raises rather than
    leaking.
    """

    def _sub(match: re.Match[str]) -> str:
        return resolve_for_prompt(db, matter=matter, token=match.group(0))

    resolved = TOKEN_RE.sub(_sub, text)
    if TOKEN_RE.search(resolved) is not None:
        raise ValueError(
            "token survived wire resolution — a display_form contains a token-shaped string"
        )
    return resolved


def scan_unregistered(db: Session, *, matter: Matter, text: str) -> list[str]:
    """Every token in ``text`` that has no latest row — the zero-unregistered-claims check.

    Returns the full tokens (``[[FACT_9]]``) in first-seen order, so chronology / compliance can
    fail loudly on a draft that cites a slot the registry never minted.
    """
    out: list[str] = []
    seen: set[str] = set()
    for match in TOKEN_RE.finditer(text):
        token = match.group(0)
        if token in seen:
            continue
        seen.add(token)
        if _latest(db, matter=matter, token=token) is None:
            out.append(token)
    return out
