"""WD-4 — operator demo kit: safety guards for the presenter materials.

Pure/offline. A materials slice has one machine-checkable surface — the S17 safety invariants:

* **BM-01** the disclosure carries every required element (the slide/verbal substitute for S13
  product-enforced labeling),
* **BM-02** the feedback form collects no client/case information (nor attendee PII),
* **BM-03** no kit document overclaims endorsement/approval, and the disclosure distinguishes
  *demonstration* vs *intended attorney approval* vs *actual no-lawyer review*.

Prose quality (and the disclosure's legal accuracy) is a human control — attorney sign-off
before the workshop; these tests fix the safety floor only.
"""

from __future__ import annotations

from pathlib import Path

_KIT_DIR = Path(__file__).resolve().parents[3] / "workshop" / "demo_kit"
_DISCLOSURE = _KIT_DIR / "disclosure.md"
_FEEDBACK = _KIT_DIR / "feedback_form.md"

# Required disclosure elements — stable key phrases, matched case-insensitively.
_REQUIRED_DISCLOSURE = [
    "demonstration",
    "pre-production",
    "synthetic",
    "no real phi",
    "not legal advice",
    "no attorney-client relationship",
    "not attorney-reviewed",
    "s13",
]

# The three review states the disclosure must keep distinct.
_REVIEW_STATE_MARKERS = [
    "demonstration",  # a demonstration...
    "intended attorney",  # ...vs the intended attorney-approval step...
    "no lawyer has reviewed",  # ...vs actual no-lawyer review
]

# Client/case fields — and attendee PII — a feedback form must never collect.
_FORBIDDEN_PII_FIELDS = [
    "date of birth",
    "dob",
    "social security",
    "ssn",
    "claim number",
    "claim #",
    "case number",
    "medical record",
    "diagnosis",
    "patient name",
    "client name",
    "home address",
    "phone number",
]

# Overclaims no kit document may assert.
_FORBIDDEN_OVERCLAIM = [
    "attorney-approved",
    "attorney approved",
    "bar-endorsed",
    "endorsed by the state bar",
    "guaranteed outcome",
    "guarantee results",
    "cofounder",
    "co-founder equity",
    "equity stake",
]


def _norm(text: str) -> str:
    """Whitespace-normalized + lowercased — markdown wraps prose across lines, so substring
    checks must be whitespace-insensitive (this also catches a wrapped forbidden phrase)."""
    return " ".join(text.split()).lower()


def _kit_docs() -> dict[str, str]:
    docs = {p.name: _norm(p.read_text(encoding="utf-8")) for p in sorted(_KIT_DIR.glob("*.md"))}
    assert docs, f"demo kit is missing at {_KIT_DIR}"
    return docs


def test_disclosure_has_all_required_elements() -> None:
    text = _norm(_DISCLOSURE.read_text(encoding="utf-8"))
    for element in _REQUIRED_DISCLOSURE:
        assert element in text, f"disclosure missing required element: {element!r}"


def test_feedback_form_collects_no_client_information() -> None:
    text = _norm(_FEEDBACK.read_text(encoding="utf-8"))
    for field in _FORBIDDEN_PII_FIELDS:
        assert field not in text, f"feedback form must not collect {field!r}"


def test_kit_makes_no_endorsement_or_approval_overclaim() -> None:
    docs = _kit_docs()
    for name, text in docs.items():
        for claim in _FORBIDDEN_OVERCLAIM:
            assert claim not in text, f"{name} makes a forbidden overclaim: {claim!r}"
    # The disclosure must keep the three review states distinct.
    disclosure = docs.get("disclosure.md", "")
    for marker in _REVIEW_STATE_MARKERS:
        assert marker in disclosure, f"disclosure missing review-state marker: {marker!r}"
