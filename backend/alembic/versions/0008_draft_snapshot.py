"""draft snapshot + memo (brain2 M5 Wave B1 placeholders)

Hand-written M5 Wave B1 migration. Adds the two Brain-2 columns the drafting wave writes,
mirroring ``app.models.orm`` exactly so the migration/model-drift test
(``tests/models/test_migration_baseline.py`` — reflects a DB built by 0001..0008 and asserts the
reflected schema equals ``Base.metadata``) stays green.

**Both tables are pre-M5 PLACEHOLDERS with NO production data** — no producer has written
``demand_drafts`` / ``draft_sections`` before this wave (Brain-2 lands here). A not-null ADD with a
server default is therefore a clean addition: there is nothing to backfill and no row to break.

Two columns are added:

1. ``demand_drafts.memo`` (Text, not-null, ``server_default=""``) — the strategy memo (Opus), an
   attorney-visible matter artifact shown at G2.5/G3, never sent to the carrier. "" is the
   degraded/not-yet-generated value.

2. ``draft_sections.prompt_snapshot`` (JSON, not-null, ``server_default="{}"``) — the
   ``DrafterPromptSnapshot`` ({input_hash, rules_blocks, matter_directives, final_hard_constraints})
   the section was drafted under; the judge-symmetry lock the compliance wave re-hashes. "{}" is the
   not-yet-drafted value.

Both are plain ``op.add_column`` — a not-null ADD with a server default needs no table rebuild on
either SQLite (tests/offline) or Postgres (deploy).

Revision ID: 0008_draft_snapshot
Revises: 0007_drafting_compliance
Create Date: 2026-07-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_draft_snapshot"
down_revision: str | None = "0007_drafting_compliance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- demand_drafts: the strategy memo artifact (attorney-visible; never on the wire) --
    op.add_column(
        "demand_drafts",
        sa.Column("memo", sa.Text(), nullable=False, server_default=""),
    )
    # -- draft_sections: the DrafterPromptSnapshot (judge-symmetry lock) -------------------
    op.add_column(
        "draft_sections",
        sa.Column("prompt_snapshot", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("draft_sections", "prompt_snapshot")
    op.drop_column("demand_drafts", "memo")
