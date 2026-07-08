# Flow 01 — Intake to Facts Review (G1)

- **Status:** DRAFT · **Date:** 2026-07-04
- **Actors:** Paralegal (matter creation, uploads), Attorney (G1 sign-off), Document
  workers (async), Orchestrator
- **Trigger:** Paralegal creates a new MVA matter and uploads the case file
- **Preconditions:** Firm + user provisioned; jurisdiction YAML rule-pack loaded for the
  matter's state; object store + BAA'd OCR/LLM endpoints reachable
- **Postconditions:** Corpus is built (classified, OCR'd, extracted, deduped); facts minted
  as `[[FACT_n]]`; matter in `facts_review` with a non-dismissible deadline banner awaiting
  attorney confirmation

## 1. Summary

A paralegal creates the matter; [jurisdiction_rules](../components/jurisdiction_rules.md)
synchronously returns `DeadlineCandidate[]` (SOL, notice-of-claim) so the deadline banner
is live before any document lands (invariant 4). The paralegal bulk-registers the case file
and streams bytes to presigned S3 targets via resumable upload sessions, then kicks Phase 0.
A budget-guarded Procrastinate job fans out per document through
[corpus_ingest](../components/corpus_ingest.md) (classify → text-layer/OCR → page store) and
[corpus_extraction](../components/corpus_extraction.md) (encounters, billing, incident facts,
anchor-validated, deduped). [fact_registry](../components/fact_registry.md) mints `[[FACT_n]]`
tokens; SSE `status` + `doc_state` stream progress. When the corpus is ready the
[orchestrator](../components/orchestrator_gates.md) transitions `corpus_processing →
facts_review` and emits `gate_ready {gate: facts_review}`, opening G1 for the attorney to
confirm incident facts, coverage, and — non-dismissibly — the deadlines.

## 2. Diagram

![Intake to facts review](../diagrams/flow_intake_to_facts.svg)

<details>
<summary>Mermaid source</summary>

```mermaid
sequenceDiagram
    autonumber
    participant PL as Paralegal (FE)
    participant API as FastAPI / view_models
    participant RULES as jurisdiction_rules
    participant OBJ as Object store (S3)
    participant OR as Orchestrator
    participant W as Document workers (async)
    participant ING as corpus_ingest
    participant OCR as OCR service (BAA)
    participant EXT as corpus_extraction
    participant REG as fact_registry
    participant AT as Attorney (FE)

    PL->>API: POST /api/matters {claim_type:mva, jurisdiction, incident_date, ...}
    API->>RULES: compute deadlines(state, claim_type, incident_date)
    RULES-->>API: DeadlineCandidate[] {kind, date, statute_ref, assumptions[]}
    API-->>PL: 201 Matter {gate_state: corpus_processing, sol_candidates[]}
    Note over PL: dashboard shows NON-DISMISSIBLE deadline banner

    PL->>API: POST /api/matters/{id}/documents/bulk {files[]}
    API->>OBJ: create presigned PUT + upload session per file
    API-->>PL: {upload_sessions[], presigned_urls[]}
    PL->>OBJ: resumable PUT bytes (retryable, TTL'd sessions)

    PL->>API: POST /api/matters/{id}/phase0/run  (SSE opens)
    API->>OR: enqueue phase0 (budget guard FIRST)
    OR->>OR: matter_budget precheck (invariant 12)
    OR->>W: Procrastinate job per CaseDocument

    loop per document
        W->>ING: classify (Haiku) -> doc_type
        ING-->>API: SSE doc_state {document_id, status: classified}
        W->>ING: per page: text-layer? else OCR fallback
        ING->>OCR: OCR page image (on fallback)
        OCR-->>ING: text + ocr_confidence
        ING->>REG: DocumentPage {text, text_source, ocr_confidence, image_ref}
        W->>EXT: extract encounters/billing/incident over page windows
        EXT->>EXT: anchor validation (>=1 PageAnchor per fact)
        EXT->>EXT: dedupe/merge (quarantine, never silent)
        EXT->>REG: mint [[FACT_n]] for extracted facts (verified|unverified)
        ING-->>API: SSE doc_state {document_id, status: extracted, pages_done}
        API-->>PL: SSE status {phase: phase0, step, counts}
    end

    OR->>REG: corpus ready? all docs terminal
    OR->>OR: transition corpus_processing -> facts_review
    OR-->>API: SSE gate_ready {gate: facts_review, payload_version}
    API-->>AT: G1 payload (incident facts, coverage, deadline confirmations)
    Note over AT: attorney confirms; blocks until deadlines confirmed
```

</details>

## 3. Step-by-step

| # | Component | Action | Boundary data | State / SSE |
|---|---|---|---|---|
| 1 | [api_and_wire](../components/api_and_wire.md) | Paralegal `POST /api/matters` | `{claim_type:"mva", jurisdiction, venue_county, incident_date, client_display_name}` | Matter created, `gate_state=corpus_processing` |
| 2 | [jurisdiction_rules](../components/jurisdiction_rules.md) | Synchronous deadline compute (rules-first lookup) | in: state × claim_type × incident_date → out: `DeadlineCandidate[] {kind: sol\|notice_of_claim, date, statute_ref, assumptions[]}` — **assumptions surfaced, not hidden** | none (synchronous in the create response) |
| 3 | [frontend_workbench](../components/frontend_workbench.md) | Render matter dashboard | `sol_candidates[]` in the create response view-model | Non-dismissible deadline banner (invariant 4) |
| 4 | [api_and_wire](../components/api_and_wire.md) | `POST /api/matters/{id}/documents/bulk` | in: `files[] {name, size, sha256?}` → out: `{upload_sessions[], presigned_urls[]}` | `CaseDocument` rows `status=uploaded` |
| 5 | [corpus_ingest](../components/corpus_ingest.md) | Resumable S3 PUT via upload sessions | file bytes → object-store keys; sessions TTL'd + resumable | none (direct FE→S3) |
| 6 | [orchestrator_gates](../components/orchestrator_gates.md) | `POST /api/matters/{id}/phase0/run` enqueues Procrastinate job **after budget precheck** (invariant 12) | budget cap vs projected spend | SSE channel opens; `status {phase: phase0}` |
| 7 | [corpus_ingest](../components/corpus_ingest.md) | Classify document (Haiku) | page sample → `doc_type ∈ {medical_record, bill, police_report, wage_doc, photo, insurance_corr, other}` | `status=classified`; SSE `doc_state` |
| 8 | [corpus_ingest](../components/corpus_ingest.md) | Per page: text-layer fast path, OCR fallback | out: `DocumentPage {page_no, text, text_source: text_layer\|ocr, ocr_confidence, image_ref}` — confidence stored | `status=ocr_done`; SSE `doc_state {pages_done}` |
| 9 | [corpus_extraction](../components/corpus_extraction.md) | Extract encounters / billing / incident over page windows (Sonnet) | out: `MedicalEncounter`, `BillingLine`, `IncidentFacts` — each carries `anchors[]`/`anchor` | writes to extraction tables |
| 10 | [corpus_extraction](../components/corpus_extraction.md) | Anchor validation | reject any fact with 0 `PageAnchor` (invariant 2) | fact bug → run error, not silent data |
| 11 | [corpus_extraction](../components/corpus_extraction.md) | Dedupe / merge encounters across pulls | `merged_from[]` provenance; ambiguous → **quarantine**, never silent merge | `dedup_status`; dedup queue |
| 12 | [fact_registry](../components/fact_registry.md) | Mint `[[FACT_n]]` for extracted facts | `FactToken {token_id, kind:"fact", value, display_form, anchors[], status, source:"extractor"}`; `registry_version` set | registry populated |
| 13 | [corpus_ingest](../components/corpus_ingest.md) | Emit per-document progress | `{document_id, status:"extracted", pages_done}` | SSE `doc_state` + `status {counts}` |
| 14 | [orchestrator_gates](../components/orchestrator_gates.md) | All docs terminal → transition | `corpus_processing → facts_review` | SSE `gate_ready {gate: facts_review, payload_version}` |
| 15 | [api_and_wire](../components/api_and_wire.md) | Serve G1 gate payload | `GET /api/matters/{id}/gates/current` → discriminated `facts_review` payload: incident facts, coverage table, deadline confirmations | attorney reviews |
| 16 | [frontend_workbench](../components/frontend_workbench.md) | G1 UI; attorney confirms | `POST /api/matters/{id}/gates/facts_review/submit` (see [flow_02](./flow_02_strategy_to_evidence_confirm.md)) — **blocks continue until deadlines confirmed** | G1 disposition (next flow) |

## 4. Failure & rework paths

| Failure | Detection point | Handling | User-visible effect |
|---|---|---|---|
| OCR vendor down | ING OCR call errors/timeouts (step 8) | Retry queue with backoff; degrade to **text-layer-only** for pages that have one, flag pages needing OCR | Document Center shows per-page "OCR pending" flags; extraction proceeds on available text |
| Password-protected / corrupt PDF | ING cannot open (step 7–8) | `CaseDocument.status=failed`; no partial extraction | Document Center surfaces the failed doc with reason; paralegal re-uploads a clean copy |
| Classifier low confidence | Haiku classification below threshold (step 7) | Route to classification review queue (v1.x); don't guess `doc_type` | Document Center review queue; paralegal confirms type |
| Duplicate upload | Dedupe detects overlap (step 11) | `dedup_status=duplicate_of\|partial_overlap`; quarantine, never silent merge (invariant 10) | Dedup queue with side-by-side; paralegal accepts/rejects |
| Abandoned upload session | Session TTL elapses (step 5) | TTL cleanup reaps stale sessions + orphaned parts | Stale upload disappears; paralegal restarts (resumable) |
| Budget cap hit mid-run | Budget guard trips during fan-out (step 6+) | **Run pauses** (not fails); emit `budget_warning {spent, cap}`; job is resumable after cap raise | Banner: spend at cap; "resume" once raised — no lost work |

## 5. Invariants exercised

1. **Inv 4 (deterministic, attorney-confirmed deadlines)** — steps 2–3, 16: rules compute
   `DeadlineCandidate[]` synchronously with assumptions listed; banner non-dismissible;
   G1 blocks until confirmed.
2. **Inv 2 (provenance or it doesn't ship)** — steps 9–10, 12: every extracted fact carries
   `≥1 PageAnchor`; unanchored = run error.
3. **Inv 10 (extractions / elections / derived stay separate; derived rebuildable)** —
   steps 11–12: dedupe quarantines rather than merging silently; extraction tables are
   distinct from human elections (arrive at G1).
4. **Inv 12 (metered + capped, on by default)** — step 6 and budget failure: budget
   precheck before enqueue; mid-run pause + `budget_warning`.
5. **Inv 14 (diagnostics for silent wrong output)** — steps 8–11: per-page `ocr_confidence`,
   `merged_from`, and phase0 run logs recorded for debugging before any fix.
6. **Inv 1 (gated copilot)** — step 14–16: corpus readiness does not auto-advance past the
   attorney; `facts_review` is a hard gate.

## 6. Decisions & open questions

**Decided 2026-07-04** (Codex readiness review; recorded in
[10_implementation_readiness.md](../10_implementation_readiness.md) §4):

- **Low-confidence classification never blocks Phase 0.** The document proceeds under its
  best-guess `doc_type` with a `classification_review` flag; facts minted from flagged
  documents enter the registry as `unverified` (already blocked at G3 by invariant 2's
  net); reclassification in the Document Center re-runs extraction for that document only.
  Never block the pipeline on a reversible label.
- **Non-AZ matter creation is refused, typed.** v1 accepts `jurisdiction = AZ` only
  ([07 §5](../07_captive_firm_model.md)); anything else returns an `unavailable`-class
  refusal with a typed reason — not an override, not a silent fallback.

Open:

- OCR degrade-to-text-layer: what confidence floor on a native text layer still warrants a
  forced OCR pass (scanned-into-PDF pages that carry a garbage text layer)? S1 measures
  garbage-text-layer incidence on FC-2 ([11 §2](../11_spike_briefs.md)) — set the floor
  from that data.
