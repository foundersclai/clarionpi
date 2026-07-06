"""The Tier-1 scorer — pure scoring of a processed matter against its gold (M2 exit + spike S2).

:func:`score_matter` reads a matter that has already been driven through Phase-0 extraction + merge
+ ledger + tokenizer sync (and had its chronology built) and scores it against a
:class:`~tests.evals.gold_fixtures.GoldMatter`, implementing the ``11_spike_briefs`` §3 metrics
exactly. It **scores; it does not orchestrate** — narrative generation, merge, and the ledger all
ran upstream; the caller passes in the :class:`~app.engine.brain1.chronology.ChronologyBuildOutcome`
whose ``unregistered_claims`` this scorer reads (it never re-runs the build).

Matching rule (deterministic, documented):

* A persisted encounter MATCHES a gold encounter iff same ``date_of_service`` AND casefolded
  provider-token **Jaccard ≥ 0.6** AND same casefolded ``encounter_type``.
* Matching is **greedy one-to-one** in ``(date_of_service, provider)`` order: gold encounters are
  visited in that order, each claiming the first still-unmatched persisted encounter that satisfies
  the rule. No persisted row is matched twice.
* ``encounter_recall`` = matched gold / total gold; ``encounter_precision`` = matched persisted /
  total persisted (post-merge).

Anchor rule (doc-scoped, anti-fabrication is already enforced upstream by the extractor, which
drops out-of-window anchors before persistence — here we verify the extractor cited the RIGHT
page for each fact): a matched pair passes iff, restricting the persisted row's anchors to the
gold encounter's document, that set is **non-empty and every page ∈ the gold anchor pages** (exact
page, no ±1). A merge survivor that also carries anchors on the OTHER pull's document is tolerated
— those extra anchors have their own gold coverage and are out of scope for this pair's doc.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.brain1.chronology import ChronologyBuildOutcome
from app.models.enums import DedupStatus
from app.models.orm import BillingLine, CaseDocument, DedupDecision, Matter, MedicalEncounter
from app.money.assemble import compute_matter_ledger
from app.rules.loader import load_pack

# The provider-token Jaccard floor for an encounter match (chronology spine identity).
_PROVIDER_JACCARD_FLOOR = 0.6


# --------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tier1Report:
    """The Tier-1 fidelity scorecard for one matter — the M2-exit evidence.

    ``ledger_delta_cents`` is ``persisted grand billed − gold grand billed`` (0 on an exact
    reconcile). ``duplicate_quarantined`` is ``None`` when the fixture has no duplicate,
    ``True``/``False`` otherwise. :meth:`passes` encodes the M2 exit thresholds.
    """

    encounter_recall: float
    encounter_precision: float
    dos_provider_accuracy: float
    anchor_accuracy: float
    anchored_rows_ratio: float
    ledger_exact: bool
    ledger_delta_cents: int
    ledger_by_category_exact: bool
    unregistered_claims: tuple[str, ...]
    duplicate_quarantined: bool | None

    def passes(self) -> bool:
        """Whether this matter clears the M2 exit criterion.

        Recall ≥ 0.95, precision ≥ 0.90, DOS+provider accuracy ≥ 0.98, anchor accuracy ≥ 0.98,
        every persisted encounter/billing row anchored (ratio == 1.0), the ledger reconciles to the
        penny (and per-category), the chronology has zero unregistered claims, and — when the
        fixture has a duplicate — it did not fail to quarantine (``duplicate_quarantined`` is not
        ``False``; ``None`` = no dup in this fixture is fine).
        """
        return (
            self.encounter_recall >= 0.95
            and self.encounter_precision >= 0.90
            and self.dos_provider_accuracy >= 0.98
            and self.anchor_accuracy >= 0.98
            and self.anchored_rows_ratio == 1.0
            and self.ledger_exact
            and self.ledger_by_category_exact
            and not self.unregistered_claims
            and self.duplicate_quarantined is not False
        )

    def as_markdown_row(
        self, *, label: str, prompt_version: str, model: str, cost_cents: int
    ) -> str:
        """Render this report as one RESULTS.md table row (see the S2 spike table)."""
        return (
            f"| {label} | {prompt_version} | {model} | "
            f"{self.encounter_recall:.3f} | {self.encounter_precision:.3f} | "
            f"{self.dos_provider_accuracy:.3f} | {self.anchor_accuracy:.3f} | "
            f"{'yes' if self.ledger_exact else 'no'} | "
            f"{'none' if not self.unregistered_claims else ', '.join(self.unregistered_claims)} | "
            f"{'PASS' if self.passes() else 'FAIL'} | {cost_cents} |"
        )


# --------------------------------------------------------------------------------------
# Normalization helpers (mirror the merge module's provider-identity notions)
# --------------------------------------------------------------------------------------


def _provider_tokens(provider: str) -> set[str]:
    """Casefolded word-token set of a provider name, punctuation stripped (for Jaccard)."""
    return {tok for tok in re.split(r"[^a-z0-9]+", provider.casefold()) if tok}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity of two token sets; 0.0 when the union is empty."""
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _norm(value: str) -> str:
    """Casefold + whitespace-collapse a string (encounter_type / provider equality)."""
    return " ".join(value.casefold().split())


# --------------------------------------------------------------------------------------
# Persisted-state readers
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _PersistedEncounter:
    """The scored view of one persisted encounter: identity fields + its anchor (doc, page) set."""

    date_of_service: date
    provider: str
    encounter_type: str
    anchor_pages_by_doc: dict[uuid.UUID, set[int]]


def _anchor_pages_by_doc(anchors: object) -> dict[uuid.UUID, set[int]]:
    """Group an ``anchors`` JSON list into ``{document_id -> {page, ...}}`` (tolerating str ids)."""
    out: dict[uuid.UUID, set[int]] = {}
    if not isinstance(anchors, list):
        return out
    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        raw_doc = anchor.get("document_id")
        page = anchor.get("page")
        if raw_doc is None or page is None:
            continue
        doc_id = raw_doc if isinstance(raw_doc, uuid.UUID) else uuid.UUID(str(raw_doc))
        out.setdefault(doc_id, set()).add(int(page))
    return out


def _load_persisted_encounters(db: Session, *, matter: Matter) -> list[_PersistedEncounter]:
    """The matter's persisted (post-merge) encounters, ordered ``(date_of_service, provider)``."""
    rows = list(
        db.execute(
            select(MedicalEncounter).where(MedicalEncounter.matter_id == matter.id)
        ).scalars()
    )
    persisted = [
        _PersistedEncounter(
            date_of_service=row.date_of_service,
            provider=row.provider,
            encounter_type=row.encounter_type,
            anchor_pages_by_doc=_anchor_pages_by_doc(row.anchors),
        )
        for row in rows
    ]
    persisted.sort(key=lambda e: (e.date_of_service, e.provider))
    return persisted


# --------------------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------------------


def _matches(
    persisted: _PersistedEncounter, gold_dos: date, gold_provider: str, gold_type: str
) -> bool:
    """The match predicate: same date, provider Jaccard ≥ 0.6, same encounter_type (casefolded)."""
    if persisted.date_of_service != gold_dos:
        return False
    if _norm(persisted.encounter_type) != _norm(gold_type):
        return False
    jac = _jaccard(_provider_tokens(persisted.provider), _provider_tokens(gold_provider))
    return jac >= _PROVIDER_JACCARD_FLOOR


def _greedy_match(
    persisted: Sequence[_PersistedEncounter],
    gold: Sequence[object],
) -> list[tuple[object, _PersistedEncounter]]:
    """Greedy one-to-one match in ``(date, provider)`` order; returns matched (gold, persisted).

    Gold encounters are visited in ``(date_of_service, provider)`` order; each claims the first
    still-unmatched persisted row satisfying :func:`_matches`. A persisted row is claimed at most
    once.
    """
    from tests.evals.gold_fixtures import (
        GoldEncounter,  # local: avoid test-import cycle at module load
    )

    gold_sorted = sorted(gold, key=lambda g: (g.date_of_service, g.provider))  # type: ignore[attr-defined]
    used: set[int] = set()
    pairs: list[tuple[object, _PersistedEncounter]] = []
    for g in gold_sorted:
        assert isinstance(g, GoldEncounter)
        for idx, p in enumerate(persisted):
            if idx in used:
                continue
            if _matches(p, g.date_of_service, g.provider, g.encounter_type):
                used.add(idx)
                pairs.append((g, p))
                break
    return pairs


# --------------------------------------------------------------------------------------
# Scorer
# --------------------------------------------------------------------------------------


def score_matter(
    db: Session,
    *,
    matter: Matter,
    gold: object,
    doc_id_by_key: Mapping[str, uuid.UUID],
    chronology: ChronologyBuildOutcome,
) -> Tier1Report:
    """Score a processed ``matter`` against its ``gold`` — see the module docstring for the rules.

    ``gold`` is a :class:`~tests.evals.gold_fixtures.GoldMatter`. ``doc_id_by_key`` maps each
    fixture-doc key to the real persisted :class:`~app.models.orm.CaseDocument` id (so the anchor
    check can resolve a gold's ``anchor_doc`` key to the document id the extractor anchored to).
    ``chronology`` is the outcome the caller already built (``generate_narratives=False`` in
    scripted mode); its ``unregistered_claims`` is read verbatim — the scorer never re-orchestrates.
    """
    from tests.evals.gold_fixtures import GoldEncounter, GoldMatter

    assert isinstance(gold, GoldMatter)

    persisted = _load_persisted_encounters(db, matter=matter)
    pairs = _greedy_match(persisted, gold.encounters)

    total_gold = len(gold.encounters)
    total_persisted = len(persisted)
    matched = len(pairs)

    encounter_recall = matched / total_gold if total_gold else 1.0
    encounter_precision = matched / total_persisted if total_persisted else 1.0

    # --- DOS + provider field accuracy over matched pairs ---
    # DOS is exact by the match rule; provider "correct" is the stronger casefold-collapsed equality
    # (Jaccard ≥ 0.6 admits a match; here we score whether the extractor got the provider verbatim).
    field_correct = 0
    for g, p in pairs:
        assert isinstance(g, GoldEncounter)
        dos_ok = p.date_of_service == g.date_of_service
        provider_ok = _norm(p.provider) == _norm(g.provider)
        if dos_ok and provider_ok:
            field_correct += 1
    dos_provider_accuracy = field_correct / matched if matched else 1.0

    # --- Anchor accuracy (doc-scoped) over matched pairs ---
    anchor_correct = 0
    for g, p in pairs:
        assert isinstance(g, GoldEncounter)
        gold_doc_id = doc_id_by_key.get(g.anchor_doc)
        if gold_doc_id is None:
            # A gold anchor whose doc never persisted cannot pass (fail loud via the ratio).
            continue
        cited = p.anchor_pages_by_doc.get(gold_doc_id)
        if cited and cited.issubset(set(g.anchor_pages)):
            anchor_correct += 1
    anchor_accuracy = anchor_correct / matched if matched else 1.0

    # --- Anchored-rows ratio: every persisted encounter + billing line carries ≥1 anchor ---
    encounters_anchored = sum(1 for p in persisted if p.anchor_pages_by_doc)
    billing_rows = list(
        db.execute(select(BillingLine).where(BillingLine.matter_id == matter.id)).scalars()
    )
    billing_anchored = sum(1 for row in billing_rows if _row_has_anchor(row.anchor))
    total_rows = total_persisted + len(billing_rows)
    anchored_rows = encounters_anchored + billing_anchored
    anchored_rows_ratio = anchored_rows / total_rows if total_rows else 1.0

    # --- Ledger reconciliation (penny-exact grand billed + per-category) ---
    pack = load_pack(matter.jurisdiction)
    ledger = compute_matter_ledger(db, matter=matter, pack=pack)
    grand_billed = ledger.grand_total.billed_cents
    ledger_delta_cents = grand_billed - gold.ledger_grand_billed_cents
    ledger_exact = ledger_delta_cents == 0

    persisted_by_category = {
        category: cols.billed_cents for category, cols in ledger.by_category.items()
    }
    ledger_by_category_exact = persisted_by_category == gold.ledger_by_category_billed

    # --- Duplicate quarantine (None when the fixture has no dup) ---
    duplicate_quarantined: bool | None
    if not gold.excluded_doc_keys:
        duplicate_quarantined = None
    else:
        duplicate_quarantined = _all_excluded_docs_quarantined(
            db, matter=matter, gold=gold, doc_id_by_key=doc_id_by_key
        )

    return Tier1Report(
        encounter_recall=encounter_recall,
        encounter_precision=encounter_precision,
        dos_provider_accuracy=dos_provider_accuracy,
        anchor_accuracy=anchor_accuracy,
        anchored_rows_ratio=anchored_rows_ratio,
        ledger_exact=ledger_exact,
        ledger_delta_cents=ledger_delta_cents,
        ledger_by_category_exact=ledger_by_category_exact,
        unregistered_claims=tuple(chronology.unregistered_claims),
        duplicate_quarantined=duplicate_quarantined,
    )


def _row_has_anchor(anchor: object) -> bool:
    """Whether a billing line's ``anchor`` JSON carries a usable ``document_id`` + ``page``."""
    return (
        isinstance(anchor, dict)
        and anchor.get("document_id") is not None
        and anchor.get("page") is not None
    )


def _all_excluded_docs_quarantined(
    db: Session,
    *,
    matter: Matter,
    gold: object,
    doc_id_by_key: Mapping[str, uuid.UUID],
) -> bool:
    """Whether each ``excluded_doc_keys`` document's byte-duplicate GROUP collapsed under dedup.

    The gold's excluded docs are exact byte-duplicates the money engine must drop; dedup
    quarantines exactly one copy of a pair as ``DUPLICATE_OF``. The check is direction-agnostic:
    dedup flags the later-arriving copy, but which copy that is can hinge on insertion order, so an
    excluded doc counts as collapsed iff it is itself ``DUPLICATE_OF`` OR a ``DUPLICATE_OF``
    decision names it as the ``against_document_id`` (its byte-twin was quarantined against it).
    Returns ``False`` if an excluded doc is missing or its group did not collapse at all (the money
    engine would then double-count — though ``ledger_exact`` independently guards the total).
    """
    from tests.evals.gold_fixtures import GoldMatter

    assert isinstance(gold, GoldMatter)
    quarantined_against = set(
        db.execute(
            select(DedupDecision.against_document_id).where(
                DedupDecision.matter_id == matter.id,
                DedupDecision.status == DedupStatus.DUPLICATE_OF.value,
            )
        ).scalars()
    )
    for key in gold.excluded_doc_keys:
        doc_id = doc_id_by_key.get(key)
        if doc_id is None:
            return False
        doc = db.get(CaseDocument, doc_id)
        if doc is None:
            return False
        is_the_dup = doc.dedup_status == DedupStatus.DUPLICATE_OF.value
        is_the_survivor_of_a_dup = doc_id in quarantined_against
        if not (is_the_dup or is_the_survivor_of_a_dup):
            return False
    return True
