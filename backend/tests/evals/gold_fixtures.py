"""Two synthetic gold matters for the Tier-1 extraction-fidelity eval (M2 exit + spike S2).

Everything here is **deterministic** (no randomness, no wall clock) and **synthetic** (no PHI —
obviously-fake names like "Jane Sample", providers "Dr. Rivera" / "Desert Spine PT"). Each builder
returns a frozen :class:`GoldMatter`: the fixture PDFs, the encounter/ledger truth the extractor
must recover, and — crucially — a :func:`scripted_provider_for` factory that plays a "perfect-ish"
extractor so the scripted-mode tests prove the harness math + pipeline plumbing without a live
model. The live-mode test feeds the SAME gold to :class:`~app.core.llm_provider.AnthropicProvider`.

Two matters, two stresses (from ``11_spike_briefs`` §3):

* :func:`build_gm1` — **the merge matter.** Two medical pulls where two visits recur across both
  pulls (same provider/date/type, differently worded) so the deterministic exact-key merge fires
  and the gold expects the *merged* distinct count. Also a multi-category bill and a police report.
* :func:`build_gm2` — **the exclusion matter.** One medical record + a bill + an EXACT byte-copy of
  that bill; the copy is quarantined ``DUPLICATE_OF`` and (once resolved ``SUPERSEDED``) the ledger
  equals the single-copy total. No police report.

Gold ledger cents are computed by summing the SAME dollar literals rendered into the bill pages —
one source of truth inside the builder (:class:`_BillLine`), so the gold can never drift from what
the extractor is handed.

Scripted-call-order derivation (the load-bearing part — see :func:`scripted_provider_for`):
:func:`~app.corpus.ingest.phase0.run_phase0` processes pending docs in ``(created_at, id)`` order;
for each ``uploaded`` doc it makes ONE ``classify`` call, then the extraction stage makes ONE
extractor call per window. Every fixture doc here is sized ≤ the 8-page window (``pull_1`` is
exactly 8 pages = one window at size 8), so each doc is exactly ONE window ⇒ exactly one classify
+ one extractor call per doc. The post-loop sync stage needs no model call in these fixtures: the
overlap visits are merged by the DETERMINISTIC exact-key path (identical provider/date/type
strings), so the LLM tiebreak never fires; registry sync + ledger use no model; chronology
narratives are generated with ``generate_narratives=False``. So the FIFO script for a matter is,
in ``(created_at, id)`` doc order: ``[classify(doc), extractor_batch(doc)] * n_docs``.

Because ``created_at`` is second-resolution on SQLite and ties break on the random ``id``, the
caller must build the script in the ACTUAL sorted doc order (not insertion order). The test/CLI do
this: insert all docs, read them back sorted by ``(created_at, id)``, and pass that order to
:func:`scripted_provider_for`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from app.core.llm_provider import CompletionResult, ScriptedProvider
from app.models.enums import DocType, LedgerCategory
from app.money.types import dollars_str_to_cents
from tests.corpus.pdf_builders import build_text_pdf

# --------------------------------------------------------------------------------------
# Gold value objects
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldEncounter:
    """One encounter the extractor must recall, with its exact page-level anchor.

    ``anchor_doc`` is the fixture-doc key (e.g. ``"records_pull_1"``) the fact appears on;
    ``anchor_pages`` are the 1-based absolute page numbers within that doc. For a visit that
    recurs across two pulls (GM-1), the gold anchor names the pull the merge SURVIVOR originates
    from (the earliest-created row wins); the merged survivor also carries the other pull's anchor,
    which the doc-scoped anchor rule tolerates (see :func:`tests.evals.tier1.score_matter`).
    """

    date_of_service: date
    provider: str
    encounter_type: str
    anchor_doc: str
    anchor_pages: tuple[int, ...]


@dataclass(frozen=True)
class GoldMatter:
    """A frozen synthetic gold matter — the fixtures plus the truth the eval scores against.

    ``documents`` maps a fixture-doc key to ``(pdf_bytes, DocType value)``. ``encounters`` is the
    post-merge distinct-encounter truth (for GM-1 that is the MERGED count). ``ledger_*`` are the
    penny-exact ledger expectations over the INCLUDED (non-excluded) bills. ``excluded_doc_keys``
    names docs whose billing lines must NOT sum (a resolved duplicate). ``incident_required`` is
    ``True`` when the matter has a police report whose incident fact must be present.
    """

    key: str
    documents: dict[str, tuple[bytes, str]]
    encounters: tuple[GoldEncounter, ...]
    ledger_grand_billed_cents: int
    ledger_by_category_billed: dict[str, int]
    excluded_doc_keys: tuple[str, ...]
    incident_required: bool


# --------------------------------------------------------------------------------------
# Internal builder value objects (the single source of truth for page text + gold)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Visit:
    """One clinical visit: the gold identity + the clinical prose rendered onto its page.

    The extractor's scripted reply for this visit is derived FROM these fields (so the scripted
    "perfect-ish" extractor cannot disagree with the gold), and the page text is realistic-but-
    synthetic clinical prose containing the same provider/date so a LIVE model reads it correctly.
    """

    date_of_service: date
    provider: str
    encounter_type: str
    facility: str
    chief_complaint: str
    exam: str
    plan: str

    def page_text(self) -> str:
        """The synthetic clinical note rendered onto this visit's page."""
        return (
            f"VISIT NOTE — {self.facility}\n"
            f"Patient: Jane Sample   DOB: 1991-04-02\n"
            f"Date of Service: {self.date_of_service.isoformat()}\n"
            f"Provider: {self.provider}\n"
            f"Encounter type: {self.encounter_type}\n"
            f"CHIEF COMPLAINT: {self.chief_complaint}\n"
            f"EXAM: {self.exam}\n"
            f"PLAN: {self.plan}\n"
        )

    def variant_page_text(self) -> str:
        """A differently-WORDED note for the SAME visit (the pull-2 recurrence in GM-1).

        Same provider / date / encounter_type (so the deterministic exact-key merge fires), but the
        surrounding prose is reworded — a live model must still read the same identity fields.
        """
        return (
            f"CLINIC ENCOUNTER SUMMARY ({self.facility})\n"
            f"Seen: Jane Sample on {self.date_of_service.isoformat()}\n"
            f"Seen by: {self.provider}\n"
            f"Type of visit: {self.encounter_type}\n"
            f"Reason for visit: {self.chief_complaint}\n"
            f"Findings on examination: {self.exam}\n"
            f"Recommended plan of care: {self.plan}\n"
        )

    def gold(self, *, anchor_doc: str, anchor_page: int) -> GoldEncounter:
        return GoldEncounter(
            date_of_service=self.date_of_service,
            provider=self.provider,
            encounter_type=self.encounter_type,
            anchor_doc=anchor_doc,
            anchor_pages=(anchor_page,),
        )

    def extracted_json(self, *, anchor_page: int) -> dict:
        """The scripted extractor's per-encounter JSON for this visit, anchored to ``anchor_page``.

        Derived from the gold fields, so the scripted extractor is a faithful "perfect" reader; the
        live extractor produces its own JSON from :meth:`page_text` / :meth:`variant_page_text`.
        """
        return {
            "date_of_service": self.date_of_service.isoformat(),
            "provider": self.provider,
            "facility": self.facility,
            "encounter_type": self.encounter_type,
            "complaints": [self.chief_complaint],
            "findings": [self.exam],
            "diagnoses": [],
            "procedures": [],
            "work_status": None,
            "anchor_pages": [anchor_page],
            "field_confidence": {"provider": 0.95, "date_of_service": 0.95},
        }


@dataclass(frozen=True)
class _BillLine:
    """One billing line: the dollar LITERAL rendered onto the page AND summed into the gold.

    ``billed`` is the printed dollar string (e.g. ``"$1,284.50"``); ``billed_cents`` derives from
    it via the money engine's own parser, so the gold ledger is summed from the exact literals the
    extractor is handed — one source of truth.
    """

    provider: str
    date_of_service: date
    code: str
    description: str
    billed: str
    category: LedgerCategory

    @property
    def billed_cents(self) -> int:
        return dollars_str_to_cents(self.billed)

    def row_text(self) -> str:
        """The printed statement row: ``03/12/2026  99213  Office visit  $185.00``."""
        us_date = self.date_of_service.strftime("%m/%d/%Y")
        return f"{us_date}  {self.code}  {self.description}  {self.billed}"

    def extracted_json(self, *, anchor_page: int) -> dict:
        """The scripted bill-extractor's per-line JSON (literal dollar string preserved)."""
        return {
            "provider": self.provider,
            "date_of_service": self.date_of_service.isoformat(),
            "code": self.code,
            "billed": self.billed,
            "adjusted": None,
            "paid": None,
            "outstanding": None,
            "category": self.category.value,
            "anchor_page": anchor_page,
        }


# --------------------------------------------------------------------------------------
# Scripted-reply helpers (mirror tests/corpus/test_phase0_extraction.py shapes)
# --------------------------------------------------------------------------------------


def _result(text: str) -> CompletionResult:
    """One scripted model reply with nominal token/cost accounting (1 cent — scripted mode)."""
    return CompletionResult(text=text, input_tokens=40, output_tokens=20, cost_cents=1)


def _classify(doc_type: str) -> CompletionResult:
    """An above-floor (0.95) classify verdict for ``doc_type`` — routes to the typed extractor."""
    return _result(json.dumps({"doc_type": doc_type, "confidence": 0.95, "rationale": "gold"}))


def _encounter_batch(entries: list[dict]) -> CompletionResult:
    """A scripted medical-extractor window reply carrying ``entries`` encounters."""
    return _result(json.dumps({"encounters": entries}))


def _bill_batch(entries: list[dict]) -> CompletionResult:
    """A scripted bill-extractor window reply carrying ``entries`` billing lines."""
    return _result(json.dumps({"lines": entries}))


def _incident(*, location: str, narrative: str, anchor_pages: list[int]) -> CompletionResult:
    """A scripted incident (police-report) window reply."""
    return _result(
        json.dumps(
            {
                "location": location,
                "incident_narrative": narrative,
                "parties": [
                    {"name": "Jane Sample", "role": "driver"},
                    {"name": "Robert Doe", "role": "driver"},
                ],
                "citations_issued": ["ARS 28-701 (failure to control speed)"],
                "anchor_pages": anchor_pages,
            }
        )
    )


# The per-doc scripted reply is (classify, extractor_batch). We stash it on the fixture so the
# provider factory can assemble the FIFO script in the caller's ACTUAL (created_at, id) doc order.
@dataclass(frozen=True)
class _DocScript:
    """The two scripted replies one uploaded doc consumes: its classify, then its one window."""

    doc_type: DocType
    classify_reply: CompletionResult
    extractor_reply: CompletionResult


# --------------------------------------------------------------------------------------
# GM-1 — the merge matter
# --------------------------------------------------------------------------------------

# Six pull-1 visits. The first two RECUR in pull-2 (identical provider/date/type) so the exact-key
# merge collapses them; the gold anchors those two to pull-1 (the survivor's origin). Each visit is
# fully on ONE page for anchor determinism.
_GM1_PULL1_VISITS: tuple[_Visit, ...] = (
    _Visit(
        date_of_service=date(2026, 2, 3),
        provider="Dr. Rivera",
        encounter_type="ER",
        facility="Valley Regional ER",
        chief_complaint="neck and upper back pain after a rear-end motor vehicle collision",
        exam="cervical paraspinal tenderness, limited range of motion, neuro intact",
        plan="cervical x-ray, muscle relaxant, refer to orthopedics",
    ),
    _Visit(
        date_of_service=date(2026, 2, 10),
        provider="Dr. Chen",
        encounter_type="office visit",
        facility="Desert Orthopedics",
        chief_complaint="persistent neck pain and stiffness one week post-collision",
        exam="reduced cervical flexion, positive facet loading on the right",
        plan="start physical therapy, continue NSAIDs, MRI if no improvement",
    ),
    _Visit(
        date_of_service=date(2026, 2, 17),
        provider="Desert Spine PT",
        encounter_type="PT",
        facility="Desert Spine Physical Therapy",
        chief_complaint="neck pain limiting work and sleep",
        exam="cervical mobility deficits, protective guarding of the trapezius",
        plan="twice-weekly PT for four weeks, home exercise program issued",
    ),
    _Visit(
        date_of_service=date(2026, 2, 24),
        provider="Desert Spine PT",
        encounter_type="PT",
        facility="Desert Spine Physical Therapy",
        chief_complaint="follow-up for cervical strain rehabilitation",
        exam="improving range of motion, residual tenderness at C5-C6",
        plan="continue PT, add cervical stabilization exercises",
    ),
    _Visit(
        date_of_service=date(2026, 3, 3),
        provider="Dr. Chen",
        encounter_type="imaging",
        facility="Desert Imaging Center",
        chief_complaint="ordered cervical MRI for persistent radicular symptoms",
        exam="MRI shows C5-C6 disc protrusion with mild foraminal narrowing",
        plan="orthopedic follow-up to review surgical vs conservative options",
    ),
    _Visit(
        date_of_service=date(2026, 3, 12),
        provider="Dr. Chen",
        encounter_type="office visit",
        facility="Desert Orthopedics",
        chief_complaint="review of MRI findings and treatment planning",
        exam="stable neuro exam, symptoms improving with conservative care",
        plan="continue PT, re-evaluate in six weeks, no surgery at this time",
    ),
)

# Two NEW pull-2 visits (do not recur in pull-1) — later dates, distinct providers.
_GM1_PULL2_NEW_VISITS: tuple[_Visit, ...] = (
    _Visit(
        date_of_service=date(2026, 3, 20),
        provider="Dr. Osei",
        encounter_type="office visit",
        facility="Canyon Pain Management",
        chief_complaint="ongoing neck pain with intermittent right-arm tingling",
        exam="mild sensory changes in the C6 distribution, strength preserved",
        plan="trial of cervical epidural steroid injection, continue therapy",
    ),
    _Visit(
        date_of_service=date(2026, 3, 31),
        provider="Canyon Pain Management",
        encounter_type="injection",
        facility="Canyon Pain Management",
        chief_complaint="scheduled cervical epidural steroid injection",
        exam="pre-procedure vitals stable, informed consent obtained",
        plan="C6-C7 epidural steroid injection performed, monitor response",
    ),
)

# The two recurrences in pull-2 reuse the FIRST TWO pull-1 visits verbatim on identity.
_GM1_PULL2_RECUR_VISITS: tuple[_Visit, ...] = (_GM1_PULL1_VISITS[0], _GM1_PULL1_VISITS[1])

_GM1_BILLS: tuple[_BillLine, ...] = (
    _BillLine(
        provider="Valley Regional ER",
        date_of_service=date(2026, 2, 3),
        code="99284",
        description="Emergency department visit, level 4",
        billed="$1,284.50",
        category=LedgerCategory.ER,
    ),
    _BillLine(
        provider="Valley Ambulance Service",
        date_of_service=date(2026, 2, 3),
        code="A0429",
        description="Ambulance transport, BLS emergency",
        billed="$950.00",
        category=LedgerCategory.AMBULANCE,
    ),
    _BillLine(
        provider="Desert Imaging Center",
        date_of_service=date(2026, 3, 3),
        code="72141",
        description="MRI cervical spine without contrast",
        billed="$2,100.75",
        category=LedgerCategory.IMAGING,
    ),
    _BillLine(
        provider="Desert Spine Physical Therapy",
        date_of_service=date(2026, 2, 17),
        code="97110",
        description="Therapeutic exercise, per 15 min",
        billed="$185.00",
        category=LedgerCategory.PT_CHIRO,
    ),
    _BillLine(
        provider="Desert Spine Physical Therapy",
        date_of_service=date(2026, 2, 24),
        code="97112",
        description="Neuromuscular re-education, per 15 min",
        billed="$210.25",
        category=LedgerCategory.PT_CHIRO,
    ),
    _BillLine(
        provider="Desert Orthopedics",
        date_of_service=date(2026, 3, 12),
        code="99213",
        description="Office visit, established patient, level 3",
        billed="$225.00",
        category=LedgerCategory.ORTHO,
    ),
)

_GM1_POLICE_PAGES: tuple[str, ...] = (
    (
        "ARIZONA TRAFFIC CRASH REPORT\n"
        "Report #: 2026-004821   Agency: Maricopa County Sheriff\n"
        "Date/Time: 02/03/2026 08:14\n"
        "Location: Intersection of E Baseline Rd and S Rural Rd, Tempe, AZ\n"
        "Unit 1 Driver: Jane Sample   Unit 2 Driver: Robert Doe\n"
        "Narrative: Unit 2 failed to stop and struck Unit 1 from the rear while Unit 1 was "
        "stopped at a red light. Unit 1 driver reported neck pain at the scene.\n"
        "Citation issued to Unit 2 driver: ARS 28-701 failure to control speed.\n"
    ),
    (
        "CRASH REPORT (page 2) — Report #: 2026-004821\n"
        "Weather: clear   Road: dry   Light: daylight\n"
        "Injuries: Unit 1 driver transported by ambulance for evaluation.\n"
        "Diagram and witness statements attached in the case file.\n"
    ),
)


def build_gm1() -> GoldMatter:
    """GM-1 — the merge matter (two medical pulls with two recurring visits + bills + police).

    ``records_pull_1``: 8 pages — 6 visit notes on pages 1-6, an admin summary on pages 7-8 (no new
    encounter), so each of the 6 encounters anchors to exactly one page. 8 pages = one window.
    ``records_pull_2``: 4 pages — the two recurrences (reworded) on pages 1-2 + two NEW visits on
    pages 3-4. The recurrences share provider/date/type with pull-1 so the deterministic exact-key
    merge collapses them (no LLM tiebreak). Gold distinct count = 6 + 2 = 8.
    ``bills_1``: one page, 6 line-item rows across ER/ambulance/imaging/PT/ortho.
    ``police_1``: a 2-page crash report.
    """
    # ---- records_pull_1: 6 visit pages + 2 admin pages (8 pages = one window) ----
    pull1_pages: list[str] = [v.page_text() for v in _GM1_PULL1_VISITS]
    pull1_pages.append(
        "TREATMENT SUMMARY (administrative) — Jane Sample\n"
        "This page summarizes billing and scheduling notes for the visits above. It records no new "
        "clinical encounter.\n"
    )
    pull1_pages.append(
        "RECORDS CUSTODIAN CERTIFICATION — Valley Regional Health System\n"
        "The foregoing are true and correct copies of the medical records for the above patient. "
        "No clinical encounter is documented on this certification page.\n"
    )
    records_pull_1 = build_text_pdf(pull1_pages)

    # ---- records_pull_2: 2 reworded recurrences + 2 new visits (4 pages = one window) ----
    pull2_pages: list[str] = [v.variant_page_text() for v in _GM1_PULL2_RECUR_VISITS]
    pull2_pages.extend(v.page_text() for v in _GM1_PULL2_NEW_VISITS)
    records_pull_2 = build_text_pdf(pull2_pages)

    # ---- bills_1: 6 rows on one statement page ----
    bill_rows = "\n".join(line.row_text() for line in _GM1_BILLS)
    bills_page = (
        "ITEMIZED STATEMENT OF CHARGES — Jane Sample\n"
        "Account: MVA-2026-0203    Statement date: 04/01/2026\n"
        "Date        Code    Description                                  Billed\n"
        f"{bill_rows}\n"
        "Please remit the total balance due within 30 days.\n"
    )
    bills_1 = build_text_pdf([bills_page])

    # ---- police_1 ----
    police_1 = build_text_pdf(list(_GM1_POLICE_PAGES))

    # ---- gold encounters (post-merge distinct set): pull-1's 6 + pull-2's 2 new ----
    gold_encounters: list[GoldEncounter] = [
        v.gold(anchor_doc="records_pull_1", anchor_page=i + 1)
        for i, v in enumerate(_GM1_PULL1_VISITS)
    ]
    # The two NEW pull-2 visits live on pages 3 and 4 of pull-2.
    gold_encounters.append(
        _GM1_PULL2_NEW_VISITS[0].gold(anchor_doc="records_pull_2", anchor_page=3)
    )
    gold_encounters.append(
        _GM1_PULL2_NEW_VISITS[1].gold(anchor_doc="records_pull_2", anchor_page=4)
    )

    by_category: dict[str, int] = {}
    for line in _GM1_BILLS:
        cat = line.category.value
        by_category[cat] = by_category.get(cat, 0) + line.billed_cents
    grand = sum(line.billed_cents for line in _GM1_BILLS)

    return GoldMatter(
        key="gm1",
        documents={
            "records_pull_1": (records_pull_1, DocType.MEDICAL_RECORD.value),
            "records_pull_2": (records_pull_2, DocType.MEDICAL_RECORD.value),
            "bills_1": (bills_1, DocType.BILL.value),
            "police_1": (police_1, DocType.POLICE_REPORT.value),
        },
        encounters=tuple(gold_encounters),
        ledger_grand_billed_cents=grand,
        ledger_by_category_billed=by_category,
        excluded_doc_keys=(),
        incident_required=True,
    )


def _gm1_doc_scripts() -> dict[str, _DocScript]:
    """Per-doc scripted replies for GM-1, keyed by fixture-doc key.

    pull-1 returns its 6 encounters anchored to pages 1-6. pull-2 returns the two recurrences
    (anchored to pull-2 pages 1-2, SAME identity as pull-1 so the exact-key merge fires) plus the
    two new visits (pages 3-4). bills returns 6 lines. police returns one incident.
    """
    pull1_entries = [v.extracted_json(anchor_page=i + 1) for i, v in enumerate(_GM1_PULL1_VISITS)]
    pull2_entries = [
        _GM1_PULL2_RECUR_VISITS[0].extracted_json(anchor_page=1),
        _GM1_PULL2_RECUR_VISITS[1].extracted_json(anchor_page=2),
        _GM1_PULL2_NEW_VISITS[0].extracted_json(anchor_page=3),
        _GM1_PULL2_NEW_VISITS[1].extracted_json(anchor_page=4),
    ]
    bill_entries = [line.extracted_json(anchor_page=1) for line in _GM1_BILLS]
    return {
        "records_pull_1": _DocScript(
            DocType.MEDICAL_RECORD, _classify("medical_record"), _encounter_batch(pull1_entries)
        ),
        "records_pull_2": _DocScript(
            DocType.MEDICAL_RECORD, _classify("medical_record"), _encounter_batch(pull2_entries)
        ),
        "bills_1": _DocScript(DocType.BILL, _classify("bill"), _bill_batch(bill_entries)),
        "police_1": _DocScript(
            DocType.POLICE_REPORT,
            _classify("police_report"),
            _incident(
                location="E Baseline Rd and S Rural Rd, Tempe, AZ",
                narrative="Unit 2 rear-ended Unit 1 stopped at a red light.",
                anchor_pages=[1],
            ),
        ),
    }


# --------------------------------------------------------------------------------------
# GM-2 — the duplicate-exclusion matter
# --------------------------------------------------------------------------------------

_GM2_VISITS: tuple[_Visit, ...] = (
    _Visit(
        date_of_service=date(2026, 1, 20),
        provider="Dr. Alvarez",
        encounter_type="ER",
        facility="St. Jude Emergency Center",
        chief_complaint="lower back pain and left knee pain after a side-impact collision",
        exam="lumbar paraspinal spasm, left knee effusion, gait antalgic",
        plan="lumbar and knee x-rays, analgesia, orthopedic referral",
    ),
    _Visit(
        date_of_service=date(2026, 1, 27),
        provider="Dr. Alvarez",
        encounter_type="office visit",
        facility="St. Jude Orthopedics",
        chief_complaint="follow-up for low back and knee injuries",
        exam="improving lumbar motion, persistent medial knee tenderness",
        plan="begin physical therapy, knee MRI if symptoms persist",
    ),
    _Visit(
        date_of_service=date(2026, 2, 5),
        provider="Summit Rehab PT",
        encounter_type="PT",
        facility="Summit Rehabilitation",
        chief_complaint="back and knee stiffness limiting activity",
        exam="lumbar mobility deficits, quadriceps weakness on the left",
        plan="physical therapy three times weekly, home program issued",
    ),
    _Visit(
        date_of_service=date(2026, 2, 19),
        provider="Dr. Alvarez",
        encounter_type="imaging",
        facility="Summit Imaging",
        chief_complaint="ordered left knee MRI for persistent pain",
        exam="MRI shows a small medial meniscus tear without displacement",
        plan="continue conservative care, orthopedic re-evaluation",
    ),
)

_GM2_BILLS: tuple[_BillLine, ...] = (
    _BillLine(
        provider="St. Jude Emergency Center",
        date_of_service=date(2026, 1, 20),
        code="99283",
        description="Emergency department visit, level 3",
        billed="$980.00",
        category=LedgerCategory.ER,
    ),
    _BillLine(
        provider="St. Jude Orthopedics",
        date_of_service=date(2026, 1, 27),
        code="99213",
        description="Office visit, established patient, level 3",
        billed="$215.50",
        category=LedgerCategory.ORTHO,
    ),
    _BillLine(
        provider="Summit Rehabilitation",
        date_of_service=date(2026, 2, 5),
        code="97110",
        description="Therapeutic exercise, per 15 min",
        billed="$165.00",
        category=LedgerCategory.PT_CHIRO,
    ),
    _BillLine(
        provider="Summit Imaging",
        date_of_service=date(2026, 2, 19),
        code="73721",
        description="MRI left knee without contrast",
        billed="$1,750.25",
        category=LedgerCategory.IMAGING,
    ),
)


def build_gm2() -> GoldMatter:
    """GM-2 — the duplicate-exclusion matter (one record + a bill + an EXACT byte-copy bill).

    ``records_1``: 4 pages, one visit note per page ⇒ 4 encounters, each anchored to its page.
    ``bills_1``: one page, 4 line-item rows across ER/ortho/PT/imaging.
    ``bills_dup``: the SAME bytes as ``bills_1`` — dedup quarantines it ``DUPLICATE_OF`` and the
    ledger excludes it (unresolved DUPLICATE_OF is already excluded; the test then resolves it
    ``SUPERSEDED`` and re-checks). No police report (``incident_required=False``).

    Gold ledger = the SINGLE-copy total; ``excluded_doc_keys=("bills_dup",)``.
    """
    records_1 = build_text_pdf([v.page_text() for v in _GM2_VISITS])

    bill_rows = "\n".join(line.row_text() for line in _GM2_BILLS)
    bills_page = (
        "ITEMIZED STATEMENT OF CHARGES — Jane Sample\n"
        "Account: MVA-2026-0120    Statement date: 03/01/2026\n"
        "Date        Code    Description                                  Billed\n"
        f"{bill_rows}\n"
        "Please remit the total balance due within 30 days.\n"
    )
    bills_1 = build_text_pdf([bills_page])
    # EXACT byte copy — build_text_pdf is deterministic, so re-building the same page yields the
    # same bytes; we reuse the object to make the byte-identity unmistakable.
    bills_dup = bills_1

    gold_encounters = tuple(
        v.gold(anchor_doc="records_1", anchor_page=i + 1) for i, v in enumerate(_GM2_VISITS)
    )

    by_category: dict[str, int] = {}
    for line in _GM2_BILLS:
        cat = line.category.value
        by_category[cat] = by_category.get(cat, 0) + line.billed_cents
    grand = sum(line.billed_cents for line in _GM2_BILLS)

    return GoldMatter(
        key="gm2",
        documents={
            "records_1": (records_1, DocType.MEDICAL_RECORD.value),
            "bills_1": (bills_1, DocType.BILL.value),
            "bills_dup": (bills_dup, DocType.BILL.value),
        },
        encounters=gold_encounters,
        ledger_grand_billed_cents=grand,
        ledger_by_category_billed=by_category,
        excluded_doc_keys=("bills_dup",),
        incident_required=False,
    )


def _gm2_doc_scripts() -> dict[str, _DocScript]:
    """Per-doc scripted replies for GM-2.

    Both ``bills_1`` and ``bills_dup`` classify + extract identically (the dup is a real doc the
    pipeline still classifies and extracts; the ledger excludes it by dedup, not by skipping it).
    """
    record_entries = [v.extracted_json(anchor_page=i + 1) for i, v in enumerate(_GM2_VISITS)]
    bill_entries = [line.extracted_json(anchor_page=1) for line in _GM2_BILLS]
    return {
        "records_1": _DocScript(
            DocType.MEDICAL_RECORD, _classify("medical_record"), _encounter_batch(record_entries)
        ),
        "bills_1": _DocScript(DocType.BILL, _classify("bill"), _bill_batch(bill_entries)),
        "bills_dup": _DocScript(DocType.BILL, _classify("bill"), _bill_batch(bill_entries)),
    }


# --------------------------------------------------------------------------------------
# Scripted provider factory
# --------------------------------------------------------------------------------------

_DOC_SCRIPTS_BY_MATTER: dict[str, dict[str, _DocScript]] = {}


def _doc_scripts_for(gold: GoldMatter) -> dict[str, _DocScript]:
    """The per-doc scripted replies for ``gold`` (built once per matter key)."""
    if gold.key not in _DOC_SCRIPTS_BY_MATTER:
        if gold.key == "gm1":
            _DOC_SCRIPTS_BY_MATTER[gold.key] = _gm1_doc_scripts()
        elif gold.key == "gm2":
            _DOC_SCRIPTS_BY_MATTER[gold.key] = _gm2_doc_scripts()
        else:  # pragma: no cover - defensive
            raise ValueError(f"no scripted replies for gold matter {gold.key!r}")
    return _DOC_SCRIPTS_BY_MATTER[gold.key]


def scripted_provider_for(gold: GoldMatter, doc_key_order: list[str]) -> ScriptedProvider:
    """Build the exact FIFO :class:`ScriptedProvider` ``run_phase0`` will consume for ``gold``.

    ``doc_key_order`` is the fixture-doc keys in the ACTUAL ``(created_at, id)`` order the runner
    will process them (the caller reads the persisted docs back sorted and maps them to keys). For
    each doc the runner makes a classify call then one extractor call (each fixture doc is exactly
    one 8-page window), so the FIFO script is, in that order:
    ``[classify(doc_1), extractor(doc_1), classify(doc_2), extractor(doc_2), ...]``.

    The sync stage adds NO scripted calls in these fixtures: the GM-1 overlap visits merge by the
    deterministic exact-key path (no LLM tiebreak), and registry/ledger/chronology-without-
    narratives use no model. If the script is ever exhausted, the ScriptedProvider raises loudly at
    the provider boundary — a signal the call order assumed here has drifted from the runner.
    """
    scripts = _doc_scripts_for(gold)
    queue: list[CompletionResult] = []
    for doc_key in doc_key_order:
        doc_script = scripts[doc_key]
        queue.append(doc_script.classify_reply)
        queue.append(doc_script.extractor_reply)
    return ScriptedProvider(queue)


# Exported public surface.
__all__ = [
    "GoldEncounter",
    "GoldMatter",
    "build_gm1",
    "build_gm2",
    "scripted_provider_for",
]
