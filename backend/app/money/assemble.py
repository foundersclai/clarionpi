"""The thin owning-module query layer for the specials ledger.

This is the *only* place in ``app.money`` that touches the database: it reads the matter's
``BillingLine`` rows and the dedup verdicts, shapes them into the pure inputs
:func:`app.money.specials.build_specials_ledger` needs, and composes the result. Everything it
computes flows through the pure layer — nothing here sums money itself, and nothing writes a total
anywhere (inv 10: the ledger is a derived view, a correction is a ``BillingLine`` edit upstream).

Two anti-double-count rules it enforces at the DB boundary (money_engine §4), both **document-
level** at M2 (page-level overlap is a finer refinement, deferred):

* a ``DedupDecision`` resolved ``SUPERSEDED`` excludes its document;
* a document classified ``DUPLICATE_OF`` is excluded unless its decision is resolved ``KEPT`` —
  an unresolved (``PENDING``) exact duplicate must not sum; resolving it ``KEPT`` re-includes it
  on the next recompute.

``PARTIAL_OVERLAP`` documents are **included**: their unique pages are real money, and the
overlapping pages are a page-level refinement left for later.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import DedupResolution, DedupStatus
from app.models.orm import BillingLine, CaseDocument, DedupDecision, Matter
from app.money.specials import LedgerLine, SpecialsLedger, build_specials_ledger
from app.rules.loader import RulePack


class MalformedAnchor(ValueError):
    """A billing line whose anchor carries no usable ``document_id``.

    Fail loud: an unattributable money line must not silently join a demand (it has no source
    document to exclude on dedup, and no page to anchor provenance to). Carries the offending
    line id and the raw anchor for the operator.
    """

    diagnostic_kind = "billing_line_malformed_anchor"

    def __init__(self, line_id: uuid.UUID, anchor: object) -> None:
        self.line_id = line_id
        self.anchor = anchor
        super().__init__(
            f"billing line {line_id} has no parseable document_id in anchor {anchor!r}"
        )


@dataclass(frozen=True)
class LedgerInputs:
    """The pure inputs to :func:`app.money.specials.build_specials_ledger`.

    ``lines`` are the matter's billing lines shaped as :class:`~app.money.specials.LedgerLine`;
    ``excluded_doc_ids`` are the document ids the dedup rules drop from every sum.
    """

    lines: tuple[LedgerLine, ...]
    excluded_doc_ids: frozenset[uuid.UUID]


def _document_id_from_anchor(line: BillingLine) -> uuid.UUID:
    """Parse the source document id out of a billing line's ``anchor`` JSON.

    The document id lives inside the anchor dict (the ORM row has no ``document_id`` column). A
    missing key, a non-dict anchor, or an unparseable value is a :class:`MalformedAnchor` — never
    a silently dropped or defaulted line.
    """
    anchor = line.anchor
    if not isinstance(anchor, dict):
        raise MalformedAnchor(line.id, anchor)
    raw = anchor.get("document_id")
    if raw is None:
        raise MalformedAnchor(line.id, anchor)
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError) as exc:
        raise MalformedAnchor(line.id, anchor) from exc


def _excluded_doc_ids(db: Session, *, matter: Matter) -> frozenset[uuid.UUID]:
    """The document ids dropped from every sum by the document-level dedup rules.

    See the module docstring: SUPERSEDED decisions, plus DUPLICATE_OF documents whose decision is
    not resolved KEPT (PENDING counts as excluded). PARTIAL_OVERLAP documents are not excluded.
    """
    excluded: set[uuid.UUID] = set()

    decisions = list(
        db.scalars(
            select(DedupDecision).where(
                DedupDecision.matter_id == matter.id,
                DedupDecision.firm_id == matter.firm_id,
            )
        )
    )
    # A SUPERSEDED decision excludes its new document outright; a KEPT decision is the *only*
    # signal that re-includes a DUPLICATE_OF document (below). One decision row per suspected doc
    # at M2; if several ever collide, a single KEPT wins re-inclusion.
    kept_doc_ids: set[uuid.UUID] = set()
    for decision in decisions:
        if decision.resolution == DedupResolution.SUPERSEDED.value:
            excluded.add(decision.document_id)
        elif decision.resolution == DedupResolution.KEPT.value:
            kept_doc_ids.add(decision.document_id)

    duplicate_docs = list(
        db.scalars(
            select(CaseDocument).where(
                CaseDocument.matter_id == matter.id,
                CaseDocument.firm_id == matter.firm_id,
                CaseDocument.dedup_status == DedupStatus.DUPLICATE_OF.value,
            )
        )
    )
    for doc in duplicate_docs:
        # Excluded unless an attorney has resolved its decision KEPT. No decision row yet, or a
        # PENDING/SUPERSEDED one, means the unresolved exact duplicate does not sum.
        if doc.id not in kept_doc_ids:
            excluded.add(doc.id)

    return frozenset(excluded)


def collect_ledger_inputs(db: Session, *, matter: Matter) -> LedgerInputs:
    """Read the matter's billing lines + dedup verdicts into the pure ledger inputs.

    Each ``BillingLine`` becomes a :class:`~app.money.specials.LedgerLine` with its source
    ``document_id`` parsed from the anchor (a missing/unparseable id raises
    :class:`MalformedAnchor` — fail loud). ``excluded_doc_ids`` applies the document-level dedup
    rules (see the module docstring).
    """
    rows = list(
        db.scalars(
            select(BillingLine).where(
                BillingLine.matter_id == matter.id,
                BillingLine.firm_id == matter.firm_id,
            )
        )
    )
    lines = tuple(
        LedgerLine(
            id=row.id,
            document_id=_document_id_from_anchor(row),
            billed_cents=row.billed_cents,
            adjusted_cents=row.adjusted_cents,
            paid_cents=row.paid_cents,
            outstanding_cents=row.outstanding_cents,
            category=row.category,
        )
        for row in rows
    )
    return LedgerInputs(lines=lines, excluded_doc_ids=_excluded_doc_ids(db, matter=matter))


def compute_matter_ledger(db: Session, *, matter: Matter, pack: RulePack) -> SpecialsLedger:
    """Collect the matter's inputs and build its specials ledger under the pack's demand basis.

    The basis comes from ``pack.billed_vs_paid_basis`` (rules own the value; AZ v1 = billed). This
    is the module's one composed entry point: DB read (:func:`collect_ledger_inputs`) → pure build
    (:func:`~app.money.specials.build_specials_ledger`).
    """
    inputs = collect_ledger_inputs(db, matter=matter)
    return build_specials_ledger(
        inputs.lines,
        excluded_doc_ids=inputs.excluded_doc_ids,
        basis=pack.billed_vs_paid_basis,
    )
