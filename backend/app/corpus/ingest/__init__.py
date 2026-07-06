"""M1 ingest — upload sessions, classification, page pipeline, dedup, Phase-0 run.

Sole author of ``DocumentPage`` (the anchor target everything downstream resolves to).
Never imports ``app.engine`` — ingest is strictly upstream of analysis (module contract:
``docs/module_contracts/app.corpus.ingest.md``).
"""
