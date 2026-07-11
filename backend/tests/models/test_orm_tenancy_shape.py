"""Structural invariants over the ORM metadata — tenancy, money, and audit shape.

These assert design invariants directly against ``Base.metadata`` so a future column that
violates them fails the build (04 §2 + AGENTS boundaries).
"""

from __future__ import annotations

import sqlalchemy as sa

from app.models.orm import Base

# The legitimate non-integer numeric columns: confidence / similarity scores, not currency.
# Each carries a justification comment in orm.py at its column definition.
_ALLOWED_FLOAT_COLUMNS = {
    ("document_pages", "ocr_confidence"),
    ("page_texts", "ocr_confidence"),
    ("case_documents", "classification_confidence"),
    ("dedup_decisions", "shingle_overlap"),
}


# Deliberate, ADR-referenced exemptions from the tenancy invariant. auth_throttle_buckets
# is PRE-AUTH state: login happens before tenancy is established, so bucket rows have no
# firm to scope to (ADR-0010 — pre-auth security tables). Nothing else may join this set
# without its own ADR.
_TENANCY_EXEMPT_TABLES = {"auth_throttle_buckets"}


def test_every_table_except_firms_is_firm_scoped() -> None:
    for table in Base.metadata.sorted_tables:
        if table.name == "firms":
            assert "firm_id" not in table.columns, "firms is the tenant root; it has no firm_id"
            continue
        if table.name in _TENANCY_EXEMPT_TABLES:
            assert "firm_id" not in table.columns, (
                f"{table.name} is ADR-exempt as pre-auth state; it must NOT quietly grow a "
                "firm_id without revisiting the exemption"
            )
            continue
        assert "firm_id" in table.columns, f"{table.name} is missing firm_id"
        col = table.columns["firm_id"]
        assert col.nullable is False, f"{table.name}.firm_id must be non-null"
        assert col.index is True, f"{table.name}.firm_id must be indexed"


def test_no_float_or_numeric_columns_except_ocr_confidence() -> None:
    offenders: list[str] = []
    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            if isinstance(col.type, sa.Float | sa.Numeric):
                if (table.name, col.name) in _ALLOWED_FLOAT_COLUMNS:
                    continue
                offenders.append(f"{table.name}.{col.name} ({col.type})")
    assert not offenders, f"Float/Numeric columns are banned (money is integer cents): {offenders}"


def test_every_cents_column_is_integer() -> None:
    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            if col.name.endswith("_cents"):
                assert isinstance(col.type, sa.Integer), (
                    f"{table.name}.{col.name} is a money column and must be Integer cents, "
                    f"got {col.type}"
                )


def test_at_least_one_cents_column_exists() -> None:
    # Guards the test above from vacuously passing if the naming convention ever drifts.
    cents_cols = [
        f"{t.name}.{c.name}"
        for t in Base.metadata.sorted_tables
        for c in t.columns
        if c.name.endswith("_cents")
    ]
    assert cents_cols, "expected at least one *_cents money column in the model"


def test_audit_events_is_append_only_no_updated_at() -> None:
    audit = Base.metadata.tables["audit_events"]
    assert "created_at" in audit.columns
    assert "updated_at" not in audit.columns, "audit_events is append-only by design"


def test_cross_firm_canonical_email_duplicate_is_rejected() -> None:
    """One canonical email = one login principal (ADR-0010): the global unique constraint
    refuses a duplicate even across firms, and the ORM hook derives the canonical form."""
    import uuid as _uuid

    import pytest as _pytest
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import sessionmaker as _sessionmaker

    from app.core.config import Settings as _Settings
    from app.core.db import create_all_for_tests, create_db_engine
    from app.models.orm import Firm as _Firm
    from app.models.orm import User as _User

    engine = create_db_engine(
        _Settings(
            app_env="test",
            database_url="sqlite+pysqlite:///:memory:",
            matter_budget_default_cents=2500,
        )
    )
    create_all_for_tests(engine)
    db = _sessionmaker(bind=engine)()
    try:
        firm_a = _Firm(id=_uuid.uuid4(), name="A")
        firm_b = _Firm(id=_uuid.uuid4(), name="B")
        db.add_all([firm_a, firm_b])
        db.flush()
        first = _User(
            firm_id=firm_a.id, email="Shared@Example.com", display_name="a", role="attorney"
        )
        db.add(first)
        db.flush()
        assert first.normalized_email == "shared@example.com"  # ORM hook derived it
        db.add(
            _User(
                firm_id=firm_b.id,
                email="  SHARED@example.COM ",
                display_name="b",
                role="attorney",
            )
        )
        with _pytest.raises(IntegrityError):
            db.flush()
    finally:
        db.close()
        engine.dispose()
