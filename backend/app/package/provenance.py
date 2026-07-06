"""The provenance report — the E4 malpractice-defense trail (invariant 2, package_builder).

A re-runnable, side-effect-light builder (reads the DB, writes no rows) that renders the
``provenance_report.pdf``: every rendered fact traced to a live source, plus the adverse-facts
trail and the judgment-call log. This is the artifact that makes invariant 2 auditable — for each
rendered span, *which* source (doc + page) it resolves to *now*, and its live verification outcome.

Three parts:

* **Part 1 — Rendered facts.** Walk each section's ``spans`` (the render-time char-offset spans) in
  order; per span resolve the token with
  :func:`~app.engine.tokenizer.registry.resolve_for_render` (display form + outcome + anchors) and
  read the token's :class:`~app.models.orm.FactToken` ``source``. **Completeness property:** every
  span in every section appears exactly once — the report has as many fact entries as there are
  spans across the sections (asserted in tests). An orphan span shows the registry
  :data:`~app.engine.tokenizer.registry.SENTINEL` with the ``orphan`` outcome (never a raw token).

* **Part 2 — Adverse facts omitted with rationale.** The ``omit_with_rationale`` risk flags (the
  defense trail: what was left out and why), plus ``need_more_records`` flags listed as open items.

* **Part 3 — Judgment calls.** The :class:`~app.models.orm.ComplianceFinding` rows the attorney
  dispositioned as ``OVERRIDE`` (proceeded past an advisory finding with a recorded reason); empty
  renders "None recorded."

Determinism (inv 10): reportlab ``invariant=1`` + pinned producer, so identical inputs produce an
identical sha256. Every rendered string is token-scanned (:func:`_scan_or_raise`) before the file
is finalized — the registry render guarantees display forms are token-free, but the report asserts
it anyway (inv 11).
"""

from __future__ import annotations

import io
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.tokenizer import registry
from app.engine.tokenizer.registry import TOKEN_RE
from app.models.enums import FindingDisposition, FlagDisposition
from app.models.orm import ComplianceFinding, DemandDraft, DraftSection, FactToken, Matter, RiskFlag
from app.package.artifacts import ArtifactTokenLeak

_PRODUCER = "ClarionPI"


def _scan_or_raise(text: str, *, locus: str) -> None:
    """Raise :class:`ArtifactTokenLeak` if ``text`` carries anything token-shaped (inv 11)."""
    match = TOKEN_RE.search(text)
    if match is not None:
        raise ArtifactTokenLeak(locus, match.group(0))


def _bracketed(token_id: str) -> str:
    """The bracketed token (``[[FACT_3]]``) for a bare ``token_id`` (``FACT_3``) — resolver input.

    ``DraftSection.spans`` carry the BARE id (inv 11 — nothing token-shaped persisted); resolution
    (:func:`registry.resolve_for_render`) takes the bracketed form, so the report re-wraps it for
    the lookup only (the bracketed string never reaches the rendered PDF text).
    """
    return f"[[{token_id}]]"


def _source_for(db: Session, *, matter: Matter, token_id: str) -> str:
    """The latest :class:`FactToken` ``source`` for a bare ``token_id`` (or ``"—"`` if absent).

    Mirrors the registry ``_latest`` semantics (highest ``registry_version`` for the slot). The
    resolver already returns value/anchors/outcome; ``source`` (extractor|attorney|rules) is read
    off the row here so the report can show provenance authorship next to the fact.
    """
    rows = list(
        db.scalars(
            select(FactToken).where(
                FactToken.matter_id == matter.id,
                FactToken.token_id == token_id,
            )
        )
    )
    if not rows:
        return "—"
    latest = max(rows, key=lambda r: r.registry_version)
    return latest.source


def _anchor_lines(anchors: Sequence[dict]) -> list[str]:
    """Render anchors as ``"doc <first 8 of id> p.<page>"`` lines (empty -> a "no anchors" line)."""
    lines: list[str] = []
    for anchor in anchors:
        doc_id = str(anchor.get("document_id", ""))
        page = anchor.get("page", "?")
        lines.append(f"doc {doc_id[:8]} p.{page}")
    return lines or ["(no anchors)"]


class _Pdf:
    """A tiny deterministic flowing-text reportlab wrapper (``invariant=1``), one column, wrapping.

    Keeps the report drawing code readable: :meth:`line` emits one line (scanning it for tokens),
    :meth:`heading` a bold line, handling page breaks. Everything routes through :meth:`line` so a
    single token scan covers all rendered text.
    """

    def __init__(self) -> None:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        self._buffer = io.BytesIO()
        self._width, self._height = letter
        self._pdf = canvas.Canvas(self._buffer, pagesize=letter, invariant=1)
        self._pdf.setProducer(_PRODUCER)
        self._y = self._height - 72

    def _newpage_if_needed(self) -> None:
        if self._y < 72:
            self._pdf.showPage()
            self._y = self._height - 72

    def heading(self, text: str, *, size: int = 14, locus: str) -> None:
        _scan_or_raise(text, locus=locus)
        self._y -= 6
        self._newpage_if_needed()
        self._pdf.setFont("Helvetica-Bold", size)
        self._pdf.drawString(72, self._y, text)
        self._y -= size + 6

    def line(self, text: str, *, indent: int = 0, locus: str) -> None:
        _scan_or_raise(text, locus=locus)
        self._newpage_if_needed()
        self._pdf.setFont("Helvetica", 10)
        self._pdf.drawString(72 + indent, self._y, text)
        self._y -= 14

    def save(self) -> bytes:
        self._pdf.showPage()
        self._pdf.save()
        return self._buffer.getvalue()


def build_provenance_report(
    db: Session,
    *,
    matter: Matter,
    draft: DemandDraft,
    sections: Sequence[DraftSection],
    flags: Sequence[RiskFlag],
) -> bytes:
    """Build the ``provenance_report.pdf`` — the invariant-2 audit trail. Returns the bytes.

    See the module docstring for the three parts and the completeness property. Reads the DB (token
    sources, override findings) but writes no rows. Deterministic bytes (inv 10); every rendered
    string token-scanned (inv 11).
    """
    pdf = _Pdf()
    pdf.heading("Provenance Report", size=16, locus="provenance")
    pdf.line(f"Matter: {matter.client_display_name}", locus="provenance")
    pdf.line(
        f"Draft version {draft.version} · registry version {draft.registry_version}",
        locus="provenance",
    )

    # -- Part 1: Rendered facts (every span, exactly once — the completeness property). --
    pdf.heading("Part 1 — Rendered facts", locus="provenance.facts")
    total_spans = 0
    for section in sections:
        spans = section.spans if isinstance(section.spans, list) else []
        pdf.line(f"Section: {section.section_id}", locus=f"section:{section.section_id}")
        for span in spans:
            total_spans += 1
            token_id = str(span.get("token_id", "")) if isinstance(span, dict) else ""
            resolution = registry.resolve_for_render(db, matter=matter, token=_bracketed(token_id))
            source = _source_for(db, matter=matter, token_id=token_id)
            display = resolution.display_form or ""
            locus = f"section:{section.section_id}:{token_id}"
            pdf.line(
                f"[{token_id}] {display}  ({resolution.outcome}; source: {source})",
                indent=18,
                locus=locus,
            )
            for anchor_line in _anchor_lines(resolution.anchors):
                pdf.line(anchor_line, indent=36, locus=locus)
    pdf.line(f"Total rendered facts: {total_spans}", locus="provenance.facts")

    # -- Part 2: Adverse facts omitted with rationale (+ open "need more records" items). --
    pdf.heading("Part 2 — Adverse facts omitted with rationale", locus="provenance.adverse")
    omitted = [f for f in flags if f.disposition == FlagDisposition.OMIT_WITH_RATIONALE.value]
    open_items = [f for f in flags if f.disposition == FlagDisposition.NEED_MORE_RECORDS.value]
    if not omitted:
        pdf.line("None recorded.", indent=18, locus="provenance.adverse")
    for flag in omitted:
        role = flag.disposition_role or "—"
        pdf.line(f"{flag.kind}: {flag.detail}", indent=18, locus="provenance.adverse")
        rationale = flag.disposition_rationale or "(no rationale recorded)"
        pdf.line(f"rationale ({role}): {rationale}", indent=36, locus="provenance.adverse")
    if open_items:
        pdf.line("Open items (need more records):", indent=18, locus="provenance.adverse")
        for flag in open_items:
            pdf.line(f"{flag.kind}: {flag.detail}", indent=36, locus="provenance.adverse")

    # -- Part 3: Judgment calls — the OVERRIDE-dispositioned compliance findings. --
    pdf.heading("Part 3 — Judgment calls", locus="provenance.judgment")
    overrides = list(
        db.scalars(
            select(ComplianceFinding).where(
                ComplianceFinding.draft_id == draft.id,
                ComplianceFinding.disposition == FindingDisposition.OVERRIDE.value,
            )
        )
    )
    if not overrides:
        pdf.line("None recorded.", indent=18, locus="provenance.judgment")
    for finding in overrides:
        pdf.line(
            f"{finding.check_kind} [{finding.section_id or '—'}]: {finding.detail}",
            indent=18,
            locus="provenance.judgment",
        )
        reason = finding.override_reason or "(no reason recorded)"
        pdf.line(f"override reason: {reason}", indent=36, locus="provenance.judgment")

    return pdf.save()


__all__ = ["build_provenance_report"]
