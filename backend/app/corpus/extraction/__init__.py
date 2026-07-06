"""Corpus extraction (M2 Wave B1): windows, extractors, anchor validation, merge.

Turns ``DocumentPage`` rows into typed, page-anchored facts — ``MedicalEncounter``,
``BillingLine``, and per-matter ``IncidentFacts`` — with every emitted row carrying ≥1 anchor
**validated against the exact prompt window it came from** (system_contract inv 2, the
anti-fabrication rule: an anchor citing a page the model never saw is rejected before it
persists).

This package does **no arithmetic** (dollar strings normalize through
:func:`app.money.types.dollars_str_to_cents`), **no token minting** (``app.engine.tokenizer``
owns that), and imports nothing from ``app.engine`` — the boundary the module contract records.
Every model call travels the metered door (:class:`app.core.llm_telemetry.MeteredLLMClient`,
inv 12).
"""

from __future__ import annotations

from app.corpus.extraction.merge import MergeOutcome, merge_encounters
from app.corpus.extraction.runner import ExtractionOutcome, extract_document
from app.corpus.extraction.windows import Window, build_windows

__all__ = [
    "Window",
    "build_windows",
    "ExtractionOutcome",
    "extract_document",
    "MergeOutcome",
    "merge_encounters",
]
