"""Extractor prompt builders + prompt-version registry (corpus_extraction §4).

Three pure string builders, one per extractable document kind, each instructing the model to:

* extract only what is *present* in the excerpt (no inference of unseen values), and
* cite ``anchor_pages`` / ``anchor_page`` using ONLY the absolute ``[PAGE n]`` numbers visible in
  the excerpt — the constraint the runner then validates against the window span
  (anti-fabrication, inv 2).

:data:`PROMPT_VERSIONS` pins the prompt version per kind. Bumping a version is the sanctioned way
to force a re-extraction of every window (the ``ExtractionRun`` idempotency key includes
``prompt_version``, so a bump makes prior runs no longer match — S2 prompt iteration by design).
"""

from __future__ import annotations

from app.corpus.extraction.windows import Window
from app.models.enums import LedgerCategory

# Per-kind prompt version. Bump a value to re-extract every window under that kind (the run
# idempotency key is (document_id, window_id, prompt_version)).
PROMPT_VERSIONS: dict[str, str] = {
    "medical": "med_v1",
    "bill": "bill_v1",
    "police": "pol_v1",
}

# The retry suffix, mirrored from the classifier house pattern: appended verbatim when a first
# reply failed to parse, demanding a bare JSON object.
JSON_ONLY_SUFFIX = "\n\nReturn ONLY the JSON object — no prose, no code fences, no explanation."

_ANCHOR_RULE = (
    "Cite pages using ONLY the absolute [PAGE n] numbers shown in the excerpt above. Never cite "
    "a page number you cannot see in this excerpt. Extract only facts that are actually present "
    "in the text — do not infer, guess, or carry over values you were not shown."
)


def _ledger_category_values() -> str:
    """Comma-joined closed ``LedgerCategory`` value list for the bill prompt's enum instruction."""
    return ", ".join(cat.value for cat in LedgerCategory)


def medical_prompt(window: Window) -> str:
    """Build the medical-records extractor prompt for one window.

    Asks for one entry per distinct clinical encounter, each with a per-top-level-field
    ``field_confidence`` (0..1), and a non-empty ``anchor_pages`` list of absolute page numbers.
    """
    return (
        "You are extracting structured clinical encounters from an excerpt of a personal-injury "
        "medical record. Identify each DISTINCT clinical encounter (one visit / date-of-service "
        "with one provider) present in the excerpt.\n\n"
        "Document excerpt (absolute page numbers in [PAGE n] headers):\n"
        "---\n"
        f"{window.text}\n"
        "---\n\n"
        f"{_ANCHOR_RULE}\n\n"
        "Return exactly one JSON object and nothing else, of this shape:\n"
        '{"encounters": [\n'
        "  {\n"
        '    "date_of_service": "YYYY-MM-DD",\n'
        '    "provider": "<treating provider name>",\n'
        '    "facility": "<facility name, or empty string>",\n'
        '    "encounter_type": "<e.g. office visit, ER, imaging, PT, surgery>",\n'
        '    "complaints": ["<patient complaint>", ...],\n'
        '    "findings": ["<clinical finding>", ...],\n'
        '    "diagnoses": ["<diagnosis>", ...],\n'
        '    "procedures": ["<procedure performed>", ...],\n'
        '    "work_status": "<work status/restrictions, or null>",\n'
        '    "anchor_pages": [<absolute page number>, ...],\n'
        '    "field_confidence": {"provider": 0.0-1.0, "date_of_service": 0.0-1.0, ...}\n'
        "  }\n"
        "]}\n\n"
        "Each encounter MUST include at least one page in anchor_pages. field_confidence maps a "
        "top-level field name to your confidence (0..1) that you read it correctly. If the "
        'excerpt contains no clinical encounter, return {"encounters": []}.'
    )


def bill_prompt(window: Window) -> str:
    """Build the billing-line extractor prompt for one window.

    Money amounts are returned as strings EXACTLY as printed (the money engine normalizes them to
    cents downstream); ``category`` must be one of the closed :class:`LedgerCategory` values;
    ``anchor_page`` is a single absolute page number.
    """
    return (
        "You are extracting billing lines from an excerpt of a personal-injury medical bill or "
        "statement. Identify each charge line present in the excerpt.\n\n"
        "Document excerpt (absolute page numbers in [PAGE n] headers):\n"
        "---\n"
        f"{window.text}\n"
        "---\n\n"
        f"{_ANCHOR_RULE}\n\n"
        "Return dollar amounts as STRINGS exactly as printed (keep the '$' and thousands commas "
        'if present, e.g. "$1,234.56"). Do not compute or reconcile totals — read only what is '
        "printed.\n"
        f"category MUST be exactly one of: {_ledger_category_values()}.\n\n"
        "Return exactly one JSON object and nothing else, of this shape:\n"
        '{"lines": [\n'
        "  {\n"
        '    "provider": "<billing provider name>",\n'
        '    "date_of_service": "YYYY-MM-DD",\n'
        '    "code": "<CPT/procedure code, or null>",\n'
        '    "billed": "<amount billed, as printed>",\n'
        '    "adjusted": "<adjustment amount, as printed, or null>",\n'
        '    "paid": "<amount paid, as printed, or null>",\n'
        '    "outstanding": "<balance due, as printed, or null>",\n'
        '    "category": "<one of the category values above>",\n'
        '    "anchor_page": <absolute page number>\n'
        "  }\n"
        "]}\n\n"
        'billed is required on every line. Use null (not "") for any amount not printed. Each '
        "line MUST cite a single anchor_page. If the excerpt contains no billing line, return "
        '{"lines": []}.'
    )


def police_prompt(window: Window) -> str:
    """Build the incident (police-report) extractor prompt for one window.

    Returns a single incident object with a non-empty ``anchor_pages`` list; ``parties`` is a list
    of ``{name, role}`` maps.
    """
    return (
        "You are extracting incident facts from an excerpt of a police / crash / incident report "
        "for a personal-injury matter. Summarize the incident facts present in this excerpt.\n\n"
        "Document excerpt (absolute page numbers in [PAGE n] headers):\n"
        "---\n"
        f"{window.text}\n"
        "---\n\n"
        f"{_ANCHOR_RULE}\n\n"
        "Return exactly one JSON object and nothing else, of this shape:\n"
        "{\n"
        '  "location": "<incident location, or empty string>",\n'
        '  "incident_narrative": "<factual narrative of what happened, or empty string>",\n'
        '  "parties": [{"name": "<party name>", "role": "<driver/passenger/witness/officer>"}],\n'
        '  "citations_issued": ["<citation/violation issued>", ...],\n'
        '  "anchor_pages": [<absolute page number>, ...]\n'
        "}\n\n"
        "anchor_pages MUST contain at least one page. Extract only what the excerpt states; use "
        "empty string / empty list for anything not present."
    )
