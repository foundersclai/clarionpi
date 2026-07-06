"""Validator + renderer tests (M5 Wave B1) — the deterministic gate and the render/span mint.

Self-contained in-memory engine + firm/matter. Tokens are minted via the real registry
(``mint_attorney_fact`` / ``mint_amounts``) so validation and rendering run against production token
shapes and display forms. Synthetic data only — no PHI.

Coverage: every validator violation string (unregistered, disallowed, required-missing, oversize,
literal-dollar, no-token-section) + a clean pass; the renderer's EXACT char offsets over a
multi-token body, the orphan -> sentinel + span-kept path, no token surviving the rendered text, and
span persistence.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.db import create_all_for_tests, create_db_engine, create_session_factory
from app.engine.brain2.renderer import render_section
from app.engine.brain2.validator import validate_section
from app.engine.tokenizer import registry
from app.engine.tokenizer.registry import SENTINEL, TOKEN_RE
from app.models.enums import GateState
from app.models.orm import DemandDraft, DraftSection, Firm, Matter, User
from app.models.schemas import AmountFact, PlannedSection

# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


@pytest.fixture
def engine() -> Engine:
    eng = create_db_engine(
        Settings(
            app_env="test",
            database_url="sqlite+pysqlite:///:memory:",
            matter_budget_default_cents=100000,
        )
    )
    create_all_for_tests(eng)
    return eng


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def firm(db: Session) -> Firm:
    f = Firm(id=uuid.uuid4(), name="Test Firm")
    db.add(f)
    db.flush()
    return f


@pytest.fixture
def user(db: Session, firm: Firm) -> User:
    u = User(
        id=uuid.uuid4(),
        firm_id=firm.id,
        email="attorney@firm.test",
        display_name="Test Attorney",
        role="attorney",
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def matter(db: Session, firm: Firm) -> Matter:
    m = Matter(
        id=uuid.uuid4(),
        firm_id=firm.id,
        client_display_name="Jane Doe",
        claim_type="mva",
        incident_date=dt.date(2026, 1, 10),
        jurisdiction="AZ",
        gate_state=GateState.DRAFTING.value,
        registry_version=0,
        sol_candidates=[],
    )
    db.add(m)
    db.commit()
    return m


def _mint_fact(db: Session, matter: Matter, user: User, display: str) -> str:
    row = registry.mint_attorney_fact(
        db, matter=matter, user=user, display_form=display, value={"note": display}
    )
    db.refresh(matter)
    return row.token_id


def _mint_amt(db: Session, matter: Matter, key: str, cents: int, display: str) -> str:
    registry.mint_amounts(
        db,
        matter=matter,
        amounts=[
            AmountFact(
                key=key,
                value_cents=cents,
                display_form=display,
                ledger_ref={"line_ids": [], "category": None, "column": "billed"},
                ledger_hash=f"hash-{key}",
            )
        ],
    )
    db.refresh(matter)
    from sqlalchemy import select

    from app.models.orm import FactToken

    row = db.execute(
        select(FactToken).where(
            FactToken.matter_id == matter.id, FactToken.source_ref == f"amt:{key}"
        )
    ).scalar_one()
    return row.token_id


def _planned(
    *, allowed: list[str], required: list[str], max_words: int = 100, section_id: str = "sec"
) -> PlannedSection:
    return PlannedSection(
        section_id=section_id,
        purpose="test",
        allowed_tokens=allowed,
        required_tokens=required,
        max_words=max_words,
    )


def _make_section(db: Session, matter: Matter, *, section_id: str, body: str) -> DraftSection:
    draft = DemandDraft(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        matter_id=matter.id,
        version=1,
        registry_version=matter.registry_version,
        strategy_plan_version=1,
        status="drafting",
    )
    db.add(draft)
    db.flush()
    section = DraftSection(
        id=uuid.uuid4(),
        firm_id=matter.firm_id,
        draft_id=draft.id,
        section_id=section_id,
        purpose="test",
        body_tokenized=body,
        registry_version=matter.registry_version,
        validation="retry_pending",
        sort_order=0,
    )
    db.add(section)
    db.flush()
    return section


# --------------------------------------------------------------------------------------
# Validator — a clean pass, then each violation string
# --------------------------------------------------------------------------------------


def test_clean_section_passes(db: Session, matter: Matter, user: User) -> None:
    fact = _mint_fact(db, matter, user, "the incident")
    planned = _planned(allowed=[fact], required=[fact])
    body = f"The claim arises from [[{fact}]] and its aftermath."
    assert validate_section(db, matter=matter, planned=planned, body_tokenized=body) == []


def test_unregistered_token(db: Session, matter: Matter, user: User) -> None:
    fact = _mint_fact(db, matter, user, "the incident")
    planned = _planned(allowed=[fact, "FACT_999"], required=[])
    body = "This cites [[FACT_999]], which was never minted."
    violations = validate_section(db, matter=matter, planned=planned, body_tokenized=body)
    assert "the section cites [[FACT_999]], which does not resolve in the registry" in violations


def test_disallowed_token(db: Session, matter: Matter, user: User) -> None:
    fact_a = _mint_fact(db, matter, user, "fact A")
    fact_b = _mint_fact(db, matter, user, "fact B")
    planned = _planned(allowed=[fact_a], required=[])  # fact_b NOT allowed
    body = f"Uses [[{fact_b}]] which is registered but not allowed here."
    violations = validate_section(db, matter=matter, planned=planned, body_tokenized=body)
    assert (
        f"the section uses [[{fact_b}]], which is not in this section's allowed tokens"
        in violations
    )


def test_required_token_missing(db: Session, matter: Matter, user: User) -> None:
    fact = _mint_fact(db, matter, user, "the incident")
    planned = _planned(allowed=[fact], required=[fact])
    body = "This body omits the required token entirely."
    violations = validate_section(db, matter=matter, planned=planned, body_tokenized=body)
    assert f"the section is missing the required token [[{fact}]]" in violations


def test_oversize(db: Session, matter: Matter, user: User) -> None:
    fact = _mint_fact(db, matter, user, "the incident")
    planned = _planned(allowed=[fact], required=[], max_words=5)
    body = f"one two three four five six [[{fact}]]"  # 7 words
    violations = validate_section(db, matter=matter, planned=planned, body_tokenized=body)
    assert "the section is 7 words, over the 5-word limit" in violations


def test_literal_dollar_amount(db: Session, matter: Matter, user: User) -> None:
    amt = _mint_amt(db, matter, "specials.grand.billed", 150000, "$1,500.00 in billed specials")
    planned = _planned(allowed=[amt], required=[])
    body = f"Total specials are [[{amt}]] which amounts to $1,500.00 exactly."
    violations = validate_section(db, matter=matter, planned=planned, body_tokenized=body)
    assert any("literal dollar amount" in v for v in violations)


def test_literal_dollar_variants_caught(db: Session, matter: Matter, user: User) -> None:
    planned = _planned(allowed=[], required=[], section_id="intro_and_representation")
    for body in ("$500", "$1,234", "$ 99.00", "$1,234,567.89"):
        violations = validate_section(db, matter=matter, planned=planned, body_tokenized=body)
        assert any("literal dollar amount" in v for v in violations), body


def test_no_token_section_rejects_any_token(db: Session, matter: Matter, user: User) -> None:
    fact = _mint_fact(db, matter, user, "the incident")
    planned = _planned(allowed=[], required=[], section_id="intro_and_representation")
    body = f"Intro should have no tokens but has [[{fact}]]."
    violations = validate_section(db, matter=matter, planned=planned, body_tokenized=body)
    assert any("this section allows no tokens" in v for v in violations)


def test_no_token_section_clean_passes(db: Session, matter: Matter) -> None:
    planned = _planned(allowed=[], required=[], section_id="intro_and_representation")
    body = "We represent the claimant in this matter and write to present the demand."
    assert validate_section(db, matter=matter, planned=planned, body_tokenized=body) == []


# --------------------------------------------------------------------------------------
# Renderer — exact offsets, orphan sentinel + span, no surviving token, persistence
# --------------------------------------------------------------------------------------


def test_render_offsets_exact_multi_token(db: Session, matter: Matter, user: User) -> None:
    fact = _mint_fact(db, matter, user, "the initial visit")  # display "the initial visit"
    amt = _mint_amt(db, matter, "specials.grand.billed", 150000, "$1,500.00")
    body = f"After [[{fact}]], specials reached [[{amt}]] total."
    section = _make_section(db, matter, section_id="damages_and_specials", body=body)

    render_section(db, matter=matter, section=section)
    rendered = section.rendered_preview
    assert rendered == "After the initial visit, specials reached $1,500.00 total."

    # Two spans, offsets index the RENDERED text exactly.
    spans = section.spans
    assert len(spans) == 2
    fact_span, amt_span = spans[0], spans[1]
    assert fact_span["token_id"] == fact
    assert rendered[fact_span["start"] : fact_span["end"]] == "the initial visit"
    assert amt_span["token_id"] == amt
    assert rendered[amt_span["start"] : amt_span["end"]] == "$1,500.00"
    # span_id namespacing.
    assert fact_span["span_id"] == "damages_and_specials:0"
    assert amt_span["span_id"] == "damages_and_specials:1"
    # No token survives the rendered text.
    assert TOKEN_RE.search(rendered) is None


def test_render_adjacent_tokens_offsets(db: Session, matter: Matter, user: User) -> None:
    a = _mint_fact(db, matter, user, "AAA")
    b = _mint_fact(db, matter, user, "BBB")
    body = f"[[{a}]][[{b}]]"  # adjacent, no literal between
    section = _make_section(db, matter, section_id="sec", body=body)
    render_section(db, matter=matter, section=section)
    assert section.rendered_preview == "AAABBB"
    spans = section.spans
    assert (spans[0]["start"], spans[0]["end"]) == (0, 3)
    assert (spans[1]["start"], spans[1]["end"]) == (3, 6)


def test_render_orphan_uses_sentinel_and_keeps_span(db: Session, matter: Matter) -> None:
    # An orphan token (never minted) renders the SENTINEL and STILL gets a span (id preserved).
    body = "Refers to [[FACT_404]] which does not resolve."
    section = _make_section(db, matter, section_id="liability", body=body)
    render_section(db, matter=matter, section=section)
    rendered = section.rendered_preview
    assert SENTINEL in rendered
    assert TOKEN_RE.search(rendered) is None  # sentinel is not token-shaped
    spans = section.spans
    assert len(spans) == 1
    assert spans[0]["token_id"] == "FACT_404"
    assert rendered[spans[0]["start"] : spans[0]["end"]] == SENTINEL


def test_render_persists_spans_and_preview(db: Session, matter: Matter, user: User) -> None:
    fact = _mint_fact(db, matter, user, "the visit")
    section = _make_section(db, matter, section_id="sec", body=f"See [[{fact}]].")
    render_section(db, matter=matter, section=section)
    db.commit()
    # Reload from the DB — the preview + spans persisted.
    reloaded = db.get(DraftSection, section.id)
    assert reloaded is not None
    assert reloaded.rendered_preview == "See the visit."
    assert reloaded.spans[0]["token_id"] == fact
