"""The fact registry — the tokenizer spine (fact_registry / system_contract §2, 5, 10, 11).

One **per-matter namespace** of typed facts (``[[FACT_n]]``, ``[[AMT_n]]``, ``[[CITE_n]]``,
``[[EX_n]]``). This package is the **only minter of tokens** and the single resolution
authority — *token → display form* for Brain-2 prompts (fabrication-safe) and *token → value +
anchors* for the renderer / provenance viewer / compliance panel.

The public surface lives in :mod:`app.engine.tokenizer.registry`:

* grammar — :func:`~app.engine.tokenizer.registry.token_str`,
  :func:`~app.engine.tokenizer.registry.parse_token`,
  :data:`~app.engine.tokenizer.registry.TOKEN_RE`,
  :data:`~app.engine.tokenizer.registry.SENTINEL`;
* versioning — :func:`~app.engine.tokenizer.registry.current_version`,
  :func:`~app.engine.tokenizer.registry.bump_version`;
* minting — :func:`~app.engine.tokenizer.registry.sync_extracted_facts`,
  :func:`~app.engine.tokenizer.registry.mint_amounts`,
  :func:`~app.engine.tokenizer.registry.mint_attorney_fact`;
* resolution — :func:`~app.engine.tokenizer.registry.resolve_for_prompt`,
  :func:`~app.engine.tokenizer.registry.resolve_for_render`,
  :func:`~app.engine.tokenizer.registry.resolve_text_for_wire`,
  :func:`~app.engine.tokenizer.registry.scan_unregistered`.

Invariants held: every token carries anchors and render resolution runs anchor integrity [2];
prompt resolution exposes only ``display_form`` [5]; the registry is derived, versioned state
with stable-forever token ids [10]; an orphan renders as a sentinel plus a loud log, never the
raw token [11].
"""

from __future__ import annotations
