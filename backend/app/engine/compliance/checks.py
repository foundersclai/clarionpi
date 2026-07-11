"""Deterministic G3 checks (inv 2, 3, 6, 13) — pure-code predicates over a rendered draft.

Every check here is a **code predicate**, never an LLM: it inspects a
:class:`~app.models.orm.DraftSection`'s ``body_tokenized`` / ``rendered_preview`` against the
live registry, the live ledger hash, the binder manifest, and the risk-flag dispositions, and
emits typed :class:`~app.models.orm.ComplianceFinding` rows. Findings are created **OPEN and
uncommitted** — the engine (:mod:`app.engine.compliance.engine`) owns persistence, severity, and
bucket routing; this module owns *detection* only (inv 13 — the deterministic side is code, and
there is no code-side post-filtering of anything semantic here).

The checks, each independently testable, keyed by :class:`~app.models.enums.CheckKind`:

* ``orphan_token`` — every ``[[..]]`` in a section's ``body_tokenized`` must resolve (outcome !=
  ``orphan``). An orphan is a hard block (inv 2 — nothing unanchored ships).
* ``amt_ledger_mismatch`` — each ``[[AMT_n]]`` is re-verified against the *live* ledger hash; a
  mismatch (a ledger edit landed after render) is a hard block (inv 3 — never trust the stored
  value, re-hash).
* ``dead_anchor`` — each token's anchors must be live: no anchor page beyond the document's
  ``page_count`` and no anchor on a dedup-superseded document. This is the page-bounds probe the
  registry's mint-time integrity check does NOT do (it checks supersession, not page ranges).
* ``missing_exhibit`` — each ``[[EX_n]]`` must be present in the binder manifest as a minted token
  AND its document's manifest entry must be integrity-``ok``.
* ``missing_statutory_term`` — v1 no-op (returns ``[]``); the seam for the time-limited demand's
  statutory response-window language.
* ``undisposed_adverse`` — any undispositioned adverse risk flag is ONE hard-block finding
  (inv 6 — an adverse fact with no disposition may not ship).
* ``prose_total_mismatch`` — every literal ``$…`` string in a ``rendered_preview`` must equal the
  display form of some AMT token minted for the matter (a rendered dollar figure that matches no
  token is an unanchored number in the prose).

A registry-version mismatch (``draft.registry_version != matter.registry_version``) is NOT a
finding here — it is the pass's precondition (a typed ``DraftRegistryDrift`` refusal in
:mod:`app.engine.compliance.engine`), and the guard's ``registry_version_match`` at G3.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.engine.tokenizer.registry import (
    TOKEN_RE,
    parse_token,
    resolve_for_render,
)
from app.models.enums import (
    CheckKind,
    DedupResolution,
    TokenKind,
)
from app.models.orm import (
    CaseDocument,
    ComplianceFinding,
    DedupDecision,
    DemandDraft,
    DraftSection,
    FactToken,
    Matter,
    RiskFlag,
)
from app.money.assemble import compute_matter_ledger
from app.package.manifest import build_draft_manifest
from app.rules.errors import RulesError
from app.rules.loader import load_pack_for_pin

# A literal written dollar figure in rendered prose (``$1,500.00`` / ``$500``). This matches the
# FORM of a dollar amount so ``prose_total_mismatch`` can check it against the AMT display forms —
# it is not a value normalizer (inv 13); the section's tokens carry the authoritative amounts.
_DOLLAR_RE = re.compile(r"\$[\d,]+(?:\.\d{2})?")


@dataclass(frozen=True)
class CheckContext:
    """Everything the deterministic checks read, assembled once per pass.

    Pure data — the checks never reach back to the DB except through the registry resolution
    calls they make per token (resolution is inherently a live read). ``live_ledger_hash`` is the
    current ledger's ``line_set_hash`` (``None`` when the jurisdiction rules can't load — the AMT
    re-verify then treats every AMT as mismatched, fail-visible, per inv 3).
    """

    matter: Matter
    draft: DemandDraft
    sections: tuple[DraftSection, ...]
    live_ledger_hash: str | None
    manifest_ok_documents: frozenset[uuid.UUID]
    manifest_token_ids: frozenset[str]
    page_counts: Mapping[uuid.UUID, int]
    superseded_docs: frozenset[uuid.UUID]
    open_adverse_flags: int


def _sections_for(db: Session, *, draft: DemandDraft) -> tuple[DraftSection, ...]:
    """The draft's sections in collation order (``sort_order``, then id for a total order)."""
    rows = list(db.execute(select(DraftSection).where(DraftSection.draft_id == draft.id)).scalars())
    rows.sort(key=lambda s: (s.sort_order, str(s.id)))
    return tuple(rows)


def _live_ledger_hash(db: Session, *, matter: Matter) -> str | None:
    """The matter's current specials-ledger ``line_set_hash``, or ``None`` when rules can't load.

    A missing/invalid jurisdiction pack yields ``None`` (a fail-visible signal — the AMT
    re-verify then flags every AMT as mismatched rather than silently trusting the stored hash).
    """
    try:
        # Pin door (BUS-02): unsupported/invalid AND pin-drifted packs all yield None — the
        # AMT re-verify then flags every AMT as mismatched (a G3 block), never silently
        # trusting a hash computed under law the matter did not attest to.
        pack = load_pack_for_pin(
            matter.jurisdiction,
            matter.rule_pack_version,
            matter.rule_pack_fingerprint,
            require_authoritative=False,
        )
    except RulesError:
        return None
    return compute_matter_ledger(db, matter=matter, pack=pack).line_set_hash


def _superseded_document_ids(db: Session, *, matter: Matter) -> frozenset[uuid.UUID]:
    """Document ids dropped by a dedup-superseded decision (mirrors the money/manifest rule)."""
    return frozenset(
        db.scalars(
            select(DedupDecision.document_id).where(
                DedupDecision.matter_id == matter.id,
                DedupDecision.resolution == DedupResolution.SUPERSEDED.value,
            )
        )
    )


def _page_counts(db: Session, *, matter: Matter) -> dict[uuid.UUID, int]:
    """``document_id -> page_count`` for every document in the matter."""
    return {
        doc.id: doc.page_count
        for doc in db.scalars(select(CaseDocument).where(CaseDocument.matter_id == matter.id))
    }


def _open_adverse_flag_count(db: Session, *, matter: Matter) -> int:
    """Count of undispositioned risk flags (``disposition IS NULL``) — inv 6's block source."""
    return db.execute(
        select(func.count())
        .select_from(RiskFlag)
        .where(
            RiskFlag.matter_id == matter.id,
            RiskFlag.disposition.is_(None),
        )
    ).scalar_one()


def build_check_context(db: Session, *, matter: Matter, draft: DemandDraft) -> CheckContext:
    """Assemble the :class:`CheckContext` for one deterministic pass over ``draft``.

    Reads the draft's sections, the live ledger hash, the binder manifest (integrity-``ok``
    document ids), the matter's already-minted EX bare token ids, the per-document page counts,
    the dedup-superseded document ids, and the undispositioned-adverse-flag count. Does NOT mint or
    mutate anything (the manifest is read WITHOUT ``mint_tokens`` — a check must never bump the
    registry mid-pass); the minted EX ids come from the persisted registry rows.
    """
    manifest = build_draft_manifest(db, matter=matter)
    ok_documents = frozenset(e.document_id for e in manifest.entries if e.integrity == "ok")
    manifest_token_ids = _minted_exhibit_token_ids(db, matter=matter)
    return CheckContext(
        matter=matter,
        draft=draft,
        sections=_sections_for(db, draft=draft),
        live_ledger_hash=_live_ledger_hash(db, matter=matter),
        manifest_ok_documents=ok_documents,
        manifest_token_ids=manifest_token_ids,
        page_counts=_page_counts(db, matter=matter),
        superseded_docs=_superseded_document_ids(db, matter=matter),
        open_adverse_flags=_open_adverse_flag_count(db, matter=matter),
    )


def _minted_exhibit_token_ids(db: Session, *, matter: Matter) -> frozenset[str]:
    """The bare token ids of every minted EX (exhibit) :class:`FactToken` for the matter.

    A section citing an EX token whose id is not here references an exhibit the registry never
    minted — the ``missing_exhibit`` "present in the manifest as a minted token" half.
    """
    rows = list(
        db.execute(
            select(FactToken).where(
                FactToken.matter_id == matter.id,
                FactToken.kind == TokenKind.EXHIBIT.value,
            )
        ).scalars()
    )
    return frozenset(row.token_id for row in rows)


def _bare_id(token: str) -> str:
    """The bare id inside a full token (``"[[AMT_1]]" -> "AMT_1"``)."""
    return token[2:-2]


def _distinct_tokens(text: str) -> list[str]:
    """Every distinct full token in ``text``, in first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for match in TOKEN_RE.finditer(text):
        token = match.group(0)
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _live_ledger_hash_fn(ctx: CheckContext) -> Callable[[dict], str]:
    """A ``live_ledger_hash`` callable for ``resolve_for_render`` — the current ledger hash.

    ``resolve_for_render`` calls this with the AMT's ``ledger_ref`` and compares the returned
    hash to the token's stored ``ledger_hash``. The ledger hash is a single matter-level value
    (not per-ref), so the argument is ignored; a ``None`` live hash returns ``""`` so it never
    matches a real stored hash — a fail-visible mismatch (inv 3).
    """

    def _fn(_ledger_ref: dict) -> str:
        return ctx.live_ledger_hash or ""

    return _fn


def _new_finding(
    ctx: CheckContext,
    *,
    section_id: str,
    check_kind: CheckKind,
    detail: str,
    anchors: Sequence[dict] = (),
    span: dict | None = None,
) -> ComplianceFinding:
    """Construct one OPEN :class:`ComplianceFinding` (uncommitted; firm/severity/bucket set later).

    The engine stamps ``firm_id`` (via ``tenant_add``), ``severity``, ``bucket``, and ``status``
    when it persists — this only carries the detection payload (section, kind, detail, anchors,
    optional splice span) plus the pinned registry version.
    """
    return ComplianceFinding(
        draft_id=ctx.draft.id,
        section_id=section_id,
        registry_version=ctx.draft.registry_version,
        check_kind=check_kind.value,
        detail=detail,
        anchors=[dict(a) for a in anchors],
        span=span,
    )


def _span_for_token(section: DraftSection, *, bare_token_id: str) -> dict | None:
    """The rendered ``{start, end}`` splice target for a token id, from the section's spans.

    Returns the first matching :class:`~app.models.schemas.RenderedSpan`'s char range (the
    mechanical-splice target an AMT/prose finding carries), or ``None`` when the section has not
    been rendered / has no span for the token.
    """
    spans = section.spans if isinstance(section.spans, list) else []
    for span in spans:
        if isinstance(span, dict) and span.get("token_id") == bare_token_id:
            start = span.get("start")
            end = span.get("end")
            if isinstance(start, int) and isinstance(end, int) and end > start:
                return {"start": start, "end": end}
    return None


# --------------------------------------------------------------------------------------
# Individual deterministic checks
# --------------------------------------------------------------------------------------


def _check_orphan_tokens(db: Session, ctx: CheckContext) -> list[ComplianceFinding]:
    """A finding per orphan token in any section's ``body_tokenized`` (inv 2 hard block)."""
    findings: list[ComplianceFinding] = []
    for section in ctx.sections:
        for token in _distinct_tokens(section.body_tokenized):
            result = resolve_for_render(db, matter=ctx.matter, token=token)
            if result.outcome == "orphan":
                findings.append(
                    _new_finding(
                        ctx,
                        section_id=section.section_id,
                        check_kind=CheckKind.ORPHAN_TOKEN,
                        detail=f"token {token} does not resolve in the registry (orphan)",
                    )
                )
    return findings


def _check_amt_ledger_mismatch(db: Session, ctx: CheckContext) -> list[ComplianceFinding]:
    """A finding per ``[[AMT_n]]`` whose live-ledger re-hash mismatches its stored hash (inv 3)."""
    findings: list[ComplianceFinding] = []
    live_fn = _live_ledger_hash_fn(ctx)
    for section in ctx.sections:
        for token in _distinct_tokens(section.body_tokenized):
            kind, _ = parse_token(token)
            if kind is not TokenKind.AMOUNT:
                continue
            result = resolve_for_render(
                db, matter=ctx.matter, token=token, live_ledger_hash=live_fn
            )
            if result.outcome == "amt_mismatch":
                findings.append(
                    _new_finding(
                        ctx,
                        section_id=section.section_id,
                        check_kind=CheckKind.AMT_LEDGER_MISMATCH,
                        detail=(
                            f"amount {token} no longer matches the live ledger "
                            "(a billing edit landed after render)"
                        ),
                        span=_span_for_token(section, bare_token_id=_bare_id(token)),
                    )
                )
    return findings


def _check_dead_anchors(db: Session, ctx: CheckContext) -> list[ComplianceFinding]:
    """A finding per token with a dead anchor: page beyond page_count, or superseded document.

    This is the page-bounds probe the registry mint-time integrity check lacks — the registry
    checks anchor *supersession* (see ``_anchors_pass_integrity``) but not that an anchor page is
    within the document's page range. Both conditions are checked here and the offending anchors
    are carried onto the finding (compliance inv 11 — the attorney sees the anchors).
    """
    findings: list[ComplianceFinding] = []
    for section in ctx.sections:
        for token in _distinct_tokens(section.body_tokenized):
            result = resolve_for_render(db, matter=ctx.matter, token=token)
            dead: list[dict] = []
            for anchor in result.anchors:
                doc_id = _anchor_document_id(anchor)
                if doc_id is None:
                    continue
                page = anchor.get("page")
                page_count = ctx.page_counts.get(doc_id)
                out_of_bounds = (
                    isinstance(page, int)
                    and page_count is not None
                    and (page < 1 or page > page_count)
                )
                if doc_id in ctx.superseded_docs or out_of_bounds:
                    dead.append(dict(anchor))
            if dead:
                findings.append(
                    _new_finding(
                        ctx,
                        section_id=section.section_id,
                        check_kind=CheckKind.DEAD_ANCHOR,
                        detail=(
                            f"token {token} has {len(dead)} dead anchor(s) "
                            "(page out of range or document superseded)"
                        ),
                        anchors=dead,
                    )
                )
    return findings


def _anchor_document_id(anchor: dict) -> uuid.UUID | None:
    """Parse the ``document_id`` out of an anchor dict (tolerating str or UUID), or ``None``."""
    raw = anchor.get("document_id")
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None


def _check_missing_exhibits(db: Session, ctx: CheckContext) -> list[ComplianceFinding]:
    """A finding per ``[[EX_n]]`` not minted in the manifest or whose document is not ok."""
    findings: list[ComplianceFinding] = []
    for section in ctx.sections:
        for token in _distinct_tokens(section.body_tokenized):
            kind, _ = parse_token(token)
            if kind is not TokenKind.EXHIBIT:
                continue
            bare = _bare_id(token)
            result = resolve_for_render(db, matter=ctx.matter, token=token)
            doc_id = _resolved_document_id(result.value)
            present = bare in ctx.manifest_token_ids
            ok = doc_id is not None and doc_id in ctx.manifest_ok_documents
            if not (present and ok):
                findings.append(
                    _new_finding(
                        ctx,
                        section_id=section.section_id,
                        check_kind=CheckKind.MISSING_EXHIBIT,
                        detail=(
                            f"exhibit {token} is not present in the binder manifest "
                            "as an integrity-ok minted exhibit"
                        ),
                    )
                )
    return findings


def _resolved_document_id(value: object) -> uuid.UUID | None:
    """The ``document_id`` from an EX token's resolved ``value`` dict, or ``None``."""
    if not isinstance(value, dict):
        return None
    raw = value.get("document_id")
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None


def _check_missing_statutory_terms(db: Session, ctx: CheckContext) -> list[ComplianceFinding]:
    """v1 no-op. The seam for the time-limited demand's statutory response-window language.

    A time-limited demand carries statutory terms that must appear when active; that demand type
    is a later version (the drafter's ``statutory_terms`` constraint list is empty at v1 too), so
    there is nothing to require yet. Returns ``[]`` — the check exists so the pass shape does not
    change when statutory terms land.
    """
    return []


def _check_undisposed_adverse(db: Session, ctx: CheckContext) -> list[ComplianceFinding]:
    """ONE finding when any adverse flag is undispositioned (inv 6 hard block)."""
    if ctx.open_adverse_flags <= 0:
        return []
    return [
        _new_finding(
            ctx,
            section_id="",
            check_kind=CheckKind.UNDISPOSED_ADVERSE,
            detail=(
                f"{ctx.open_adverse_flags} adverse risk flag(s) are undispositioned; "
                "an undisposed adverse fact may not ship (inv 6)"
            ),
        )
    ]


def _check_prose_total_mismatch(db: Session, ctx: CheckContext) -> list[ComplianceFinding]:
    """A finding per literal ``$…`` in a rendered preview that matches no AMT display form.

    Every dollar figure in the letter must trace to an ``[[AMT_n]]`` token (its display form). A
    literal that equals no minted AMT display form is an unanchored number in the prose (e.g. a
    fabricated total). The finding's span is the char range of the literal in the rendered text.
    """
    amt_display_forms = _amt_display_forms(db, matter=ctx.matter)
    findings: list[ComplianceFinding] = []
    for section in ctx.sections:
        rendered = section.rendered_preview or ""
        for match in _DOLLAR_RE.finditer(rendered):
            literal = match.group(0)
            if literal in amt_display_forms:
                continue
            findings.append(
                _new_finding(
                    ctx,
                    section_id=section.section_id,
                    check_kind=CheckKind.PROSE_TOTAL_MISMATCH,
                    detail=(
                        f"rendered prose carries the literal amount {literal!r}, which matches "
                        "no [[AMT_n]] token display form for this matter"
                    ),
                    span={"start": match.start(), "end": match.end()},
                )
            )
    return findings


def _amt_display_forms(db: Session, *, matter: Matter) -> frozenset[str]:
    """The set of AMT-token display forms for the matter (latest-version row per slot).

    A dollar literal in the prose is legitimate iff it equals one of these — the display forms
    are the only sanctioned rendered dollar strings (each is what an ``[[AMT_n]]`` renders to).
    """
    rows = list(
        db.execute(
            select(FactToken).where(
                FactToken.matter_id == matter.id,
                FactToken.kind == TokenKind.AMOUNT.value,
            )
        ).scalars()
    )
    latest: dict[str, FactToken] = {}
    for row in rows:
        seen = latest.get(row.token_id)
        if seen is None or row.registry_version > seen.registry_version:
            latest[row.token_id] = row
    return frozenset(row.display_form for row in latest.values())


# The ordered deterministic-check dispatch — one entry per deterministic-eligible CheckKind.
# The engine runs these in order and persists their findings (severity/bucket applied there).
_DETERMINISTIC_CHECKS: tuple[Callable[[Session, CheckContext], list[ComplianceFinding]], ...] = (
    _check_orphan_tokens,
    _check_amt_ledger_mismatch,
    _check_dead_anchors,
    _check_missing_exhibits,
    _check_missing_statutory_terms,
    _check_undisposed_adverse,
    _check_prose_total_mismatch,
)


def run_deterministic_checks(db: Session, ctx: CheckContext) -> list[ComplianceFinding]:
    """Run every deterministic check over ``ctx`` and return the OPEN findings (uncommitted).

    Findings are returned in check order (orphan, amt, dead-anchor, missing-exhibit,
    missing-statutory, undisposed-adverse, prose-total). Each is created OPEN with its
    ``check_kind`` / ``detail`` / anchors / optional splice span; the engine applies severity and
    bucket and persists. Nothing here commits or stamps ``firm_id`` — that is the engine's job so
    the whole pass is one unit of work.
    """
    findings: list[ComplianceFinding] = []
    for check in _DETERMINISTIC_CHECKS:
        findings.extend(check(db, ctx))
    return findings


__all__ = [
    "CheckContext",
    "build_check_context",
    "run_deterministic_checks",
]
