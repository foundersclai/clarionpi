"""The section renderer (inv 11) — tokenized body -> rendered preview + char-offset spans.

:func:`render_section` resolves a validated ``DraftSection.body_tokenized`` into a
``rendered_preview`` (tokens replaced by their registry display forms) and mints the
``RenderedSpan`` list — one span per token, carrying ``[start, end)`` OFFSETS INTO THE RENDERED
TEXT and the BARE token id. The spans feed M6 provenance click-through: a click on a rendered run
resolves back to the token that produced it.

Two structural rules carry inv 11:

* **Offsets are into the RENDERED text, recomputed as we splice.** As each token's display form is
  substituted, the running output length shifts; the span's ``start``/``end`` are computed against
  the output built so far, so they index the final ``rendered_preview`` exactly (never the
  tokenized source offsets).

* **Nothing token-shaped survives, and an orphan still gets a span.** An orphan token (no registry
  row) resolves to the registry :data:`~app.engine.tokenizer.registry.SENTINEL` — which is
  deliberately NOT token-shaped — and STILL gets a span (its bare ``token_id`` preserved) so G3 can
  show the break. As a final safety net the function asserts the rendered text contains no token
  (the registry guarantees the sentinel is non-token-shaped; a display form that itself carried a
  token would be a data bug and raises).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.engine.tokenizer.registry import TOKEN_RE, resolve_for_prompt
from app.models.orm import DraftSection, Matter
from app.models.schemas import RenderedSpan


def _bare_id(token: str) -> str:
    """The bare id inside a full token (``"[[AMT_1]]" -> "AMT_1"``)."""
    return token[2:-2]


def render_section(db: Session, *, matter: Matter, section: DraftSection) -> DraftSection:
    """Render ``section.body_tokenized`` into ``rendered_preview`` + ``spans`` and persist.

    Walks every :data:`~app.engine.tokenizer.registry.TOKEN_RE` match over the tokenized body IN
    ORDER, replacing each token with its :func:`~app.engine.tokenizer.registry.resolve_for_prompt`
    display form (the registry SENTINEL for an orphan) and recording a :class:`RenderedSpan`
    (``span_id = f"{section.section_id}:{i}"``, ``start``/``end`` into the RENDERED text, bare
    ``token_id``). Persists ``rendered_preview`` and ``spans`` (as plain dicts) on the row and
    returns it.

    Raises ``ValueError`` if any token survives into the rendered text — the registry guarantees the
    sentinel is not token-shaped, so a survivor means a ``display_form`` carried a token (a data
    bug), not a normal orphan.
    """
    body = section.body_tokenized
    out_parts: list[str] = []
    spans: list[RenderedSpan] = []
    cursor = 0  # index into the SOURCE (tokenized) body
    out_len = 0  # running length of the RENDERED text built so far
    span_index = 0

    for match in TOKEN_RE.finditer(body):
        # Literal text before this token passes through unchanged; it advances the rendered length.
        literal = body[cursor : match.start()]
        if literal:
            out_parts.append(literal)
            out_len += len(literal)

        token = match.group(0)
        display = resolve_for_prompt(db, matter=matter, token=token)
        start = out_len
        out_parts.append(display)
        out_len += len(display)
        spans.append(
            RenderedSpan(
                span_id=f"{section.section_id}:{span_index}",
                start=start,
                end=out_len,
                token_id=_bare_id(token),
            )
        )
        span_index += 1
        cursor = match.end()

    # Trailing literal after the last token.
    trailing = body[cursor:]
    if trailing:
        out_parts.append(trailing)
        out_len += len(trailing)

    rendered = "".join(out_parts)
    # Final safety net (inv 11): nothing token-shaped may survive rendering. The registry sentinel
    # is non-token-shaped, so a survivor is a data bug (a display_form carrying a token), not an
    # orphan — raise rather than let it reach the wire.
    if TOKEN_RE.search(rendered) is not None:
        raise ValueError(
            "token survived section rendering — a display_form contains a token-shaped string"
        )

    section.rendered_preview = rendered
    section.spans = [s.model_dump() for s in spans]
    db.add(section)
    return section
