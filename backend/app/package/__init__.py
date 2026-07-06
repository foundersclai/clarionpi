"""The demand-package builder — manifest read-model (M4) + the four artifact builds (M5 Wave B2).

Turns approved matter state into the attorney-facing deliverable set with page-level provenance:

* :mod:`app.package.manifest` — the M4 draft binder manifest read-model (exhibit picks + PHI
  disposition + EX-token mint + the M5 build-gate ``blocking`` preview);
* :mod:`app.package.artifacts` — the pure ``letter.docx`` + ``chronology.xlsx`` byte builders;
* :mod:`app.package.binder` — the exhibit ``binder.pdf`` (continuous Bates, index page, bookmarks);
* :mod:`app.package.provenance` — the ``provenance_report.pdf`` (the invariant-2 audit trail);
* :mod:`app.package.build` — the :class:`~app.models.orm.ArtifactSet` orchestration that composes
  the above, stores each artifact, and records the immutable, version-keyed set.

Module invariants: **[11]** zero tokens in any artifact (a post-render ``TOKEN_RE`` scan fails the
build); **[10]** artifacts derivable purely from approved state with deterministic bytes (no
wall-clock in the bytes); **[2]** the provenance report traces every rendered fact to a live source.
"""
