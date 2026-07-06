"""The AZ pack's letter_structure block + the loader's fail-loud accessor.

Drafting REQUIRES the demand-letter section skeleton; there is no code-side default. The AZ pack
carries the five-section skeleton (unverified — the section list is a legal-drafting judgment), and
a pack WITHOUT the block raises ``LetterStructureMissing`` from the accessor rather than
substituting a default set.
"""

from __future__ import annotations

import pytest

from app.models.enums import RuleVerifyStatus, TokenKind
from app.rules.errors import LetterStructureMissing
from app.rules.loader import RulePack, load_pack

# The AZ skeleton, in order — the wave's target section_ids.
_EXPECTED_SECTIONS = [
    "intro_and_representation",
    "liability",
    "injuries_and_treatment",
    "damages_and_specials",
    "demand_and_deadline",
]


def test_az_pack_parses_five_sections_in_order() -> None:
    sections = load_pack("AZ").letter_sections
    assert [s.section_id for s in sections] == _EXPECTED_SECTIONS


def test_az_pack_section_token_kinds_and_word_ceilings() -> None:
    by_id = {s.section_id: s for s in load_pack("AZ").letter_sections}

    # intro carries no required token kinds; liability/injuries require fact; damages/demand
    # require amount — the fact-token kinds coerce from the YAML strings into TokenKind.
    assert by_id["intro_and_representation"].required_token_kinds == []
    assert by_id["liability"].required_token_kinds == [TokenKind.FACT]
    assert by_id["injuries_and_treatment"].required_token_kinds == [TokenKind.FACT]
    assert by_id["damages_and_specials"].required_token_kinds == [TokenKind.AMOUNT]
    assert by_id["demand_and_deadline"].required_token_kinds == [TokenKind.AMOUNT]

    # soft word ceilings are ints.
    assert by_id["injuries_and_treatment"].max_words == 700
    assert by_id["demand_and_deadline"].max_words == 300
    assert all(isinstance(s.max_words, int) for s in by_id.values())


def test_az_letter_structure_is_unverified() -> None:
    pack = load_pack("AZ")
    assert pack.letter_structure is not None
    assert pack.letter_structure.verify_status is RuleVerifyStatus.UNVERIFIED
    assert "verify" in pack.letter_structure.source.lower()


def test_accessor_raises_when_block_absent() -> None:
    # A pack constructed WITHOUT the block: drafting fails loud (no default skeleton).
    bare = RulePack(pack="X", version="0.0.0")
    assert bare.letter_structure is None
    with pytest.raises(LetterStructureMissing) as excinfo:
        _ = bare.letter_sections
    assert excinfo.value.diagnostic_kind == "letter_structure_missing"
    assert excinfo.value.pack == "X"


def test_letter_section_rule_rejects_unknown_token_kind() -> None:
    from pydantic import ValidationError

    from app.rules.loader import LetterStructureRule

    with pytest.raises(ValidationError):
        LetterStructureRule(
            source="test",
            verify_status=RuleVerifyStatus.UNVERIFIED,
            sections=[
                {
                    "section_id": "x",
                    "purpose": "y",
                    "max_words": 100,
                    "required_token_kinds": ["not_a_kind"],
                }
            ],
        )


def test_letter_structure_rule_requires_at_least_one_section() -> None:
    from pydantic import ValidationError

    from app.rules.loader import LetterStructureRule

    with pytest.raises(ValidationError):
        LetterStructureRule(source="test", verify_status=RuleVerifyStatus.UNVERIFIED, sections=[])
