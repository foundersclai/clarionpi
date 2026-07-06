"""The deterministic section validator (inv 13) — code owns the mechanical verdict; caller retries.

:func:`validate_section` is pure, deterministic FORM checking over a drafted
``body_tokenized``: it returns the list of violation strings a section breaks, or ``[]`` when the
section is clean. It NEVER edits the body (that would be a code-side normalizer patching the
drafter's output — the exact thing inv 13 forbids); it only accepts or rejects. The single retry is
the *caller's* (``app.engine.brain2.generate``): validate → retry once on violations → re-validate →
surface. This module does not loop.

Every violation string is exact and testable — the checks, in order:

* **unregistered token** — a token-shaped substring the registry never minted
  (``registry.scan_unregistered``); a section citing a slot that does not exist is a fabricated
  reference (inv 5).
* **disallowed token** — a token whose bare id is not in the section's ``allowed_tokens``.
* **required token missing** — a token in ``required_tokens`` that the body does not contain.
* **oversize** — ``len(body.split()) > max_words``.
* **literal dollar amount** — a written ``$`` figure (regex ``\\$\\s?[\\d,]+(\\.\\d{2})?``). A
  dollar figure must be an ``[[AMT_n]]`` reference, never a literal (inv 3); this is mechanical
  pattern-matching on the FORM of the text, not a semantic normalization of a value.
* **token in a no-token section** — a body that carries ANY token when ``allowed_tokens`` is empty
  (the ``intro_and_representation`` case).

A token-free body when ``required_tokens`` is non-empty is covered by the required-missing checks
(each required token is reported absent) — there is no separate "empty of tokens" violation.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.engine.tokenizer.registry import TOKEN_RE, scan_unregistered
from app.models.orm import Matter
from app.models.schemas import PlannedSection

# A written dollar figure: ``$`` then digits/commas, optional cents. This matches the FORM of a
# literal amount in prose — it is not a value normalizer (inv 3/13): a dollar figure belongs in an
# ``[[AMT_n]]`` token, so any literal ``$…`` in the body is a mechanical violation.
_DOLLAR_RE = re.compile(r"\$\s?[\d,]+(\.\d{2})?")


def _bare_id(token: str) -> str:
    """The bare id inside a full token (``"[[FACT_7]]" -> "FACT_7"``)."""
    return token[2:-2]


def validate_section(
    db: Session, *, matter: Matter, planned: PlannedSection, body_tokenized: str
) -> list[str]:
    """Return the deterministic violations of ``body_tokenized`` vs ``planned`` (``[]`` = clean).

    See the module docstring for the full, ordered check list and the exact violation strings.
    Order of the returned list is: unregistered-token violations, then disallowed-token, then
    required-missing, then oversize, then literal-dollar, then no-token-section. The caller appends
    these to the retry prompt tail verbatim.
    """
    violations: list[str] = []
    allowed = set(planned.allowed_tokens)

    # Every distinct token in the body, first-seen order (bare-id compare against the allow-set).
    seen: list[str] = []
    seen_set: set[str] = set()
    for match in TOKEN_RE.finditer(body_tokenized):
        token = match.group(0)
        if token not in seen_set:
            seen_set.add(token)
            seen.append(token)

    # 1. Unregistered tokens — a slot the registry never minted (fabricated reference).
    unregistered = scan_unregistered(db, matter=matter, text=body_tokenized)
    for token in unregistered:
        violations.append(f"the section cites {token}, which does not resolve in the registry")

    # 2. Disallowed tokens — bare id not in allowed_tokens. (A no-token section reports this as
    # every token being disallowed; the dedicated no-token message below adds the why.)
    unregistered_set = set(unregistered)
    for token in seen:
        if token in unregistered_set:
            continue  # already reported as unregistered; don't double-count as disallowed
        if _bare_id(token) not in allowed:
            violations.append(
                f"the section uses {token}, which is not in this section's allowed tokens"
            )

    # 3. Required tokens missing — a required bare id whose full token is absent from the body.
    for bare in planned.required_tokens:
        full = f"[[{bare}]]"
        if full not in body_tokenized:
            violations.append(f"the section is missing the required token {full}")

    # 4. Oversize — word count over the section's ceiling.
    word_count = len(body_tokenized.split())
    if word_count > planned.max_words:
        violations.append(
            f"the section is {word_count} words, over the {planned.max_words}-word limit"
        )

    # 5. Literal dollar amount — a written $ figure (must be an [[AMT_n]] reference; inv 3).
    if _DOLLAR_RE.search(body_tokenized) is not None:
        violations.append(
            "the section contains a literal dollar amount; dollar figures must be [[AMT_n]] "
            "token references, never written out"
        )

    # 6. No-token section — allowed_tokens empty but the body carries a token.
    if not allowed and seen:
        violations.append(
            f"this section allows no tokens, but the body contains token(s): {', '.join(seen)}"
        )

    return violations
