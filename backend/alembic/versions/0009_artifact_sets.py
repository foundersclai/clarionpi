"""artifact_sets — immutable demand-package builds (M5 Wave B2 package builder)

Hand-written M5 Wave B2 migration. Creates the single new table this wave owns, mirroring
``app.models.orm.ArtifactSet`` exactly so the migration/model-drift test
(``tests/models/test_migration_baseline.py`` — reflects a DB built by 0001..0009 and asserts the
reflected schema equals ``Base.metadata``) stays green.

One brand-new table, ``artifact_sets``: one immutable build of the demand package, keyed by
``(matter_id, draft_version, registry_version)`` — a rebuild after drift is a NEW set, never an
overwrite (package_builder §3). Because the table did not exist before this wave, this is a plain
``create_table`` (no batch rebuild, no backfill) — portable on both SQLite (tests/offline) and
Postgres (deploy). The ``artifacts`` JSON carries ``[{kind, object_key, sha256, byte_count}]``
(one entry per built artifact); ``built_by`` is a nullable FK to the acting user; ``created_at``
uses the server ``now()`` default (it is not part of the artifact bytes, which pin their own
metadata timestamps, so a wall-clock row timestamp is fine).

The ``(matter_id, draft_version, registry_version)`` UniqueConstraint is created inline with the
table (SQLite creates named constraints fine at table-creation time — the batch-rebuild dance is
only needed for ADD CONSTRAINT on an existing table). The matter_id / draft_id indexes match the
ORM ``index=True`` columns.

Revision ID: 0009_artifact_sets
Revises: 0008_draft_snapshot
Create Date: 2026-07-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_artifact_sets"
down_revision: str | None = "0008_draft_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifact_sets",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("firm_id", sa.Uuid(), nullable=False),
        sa.Column(
            "matter_id",
            sa.Uuid(),
            sa.ForeignKey("matters.id", name="fk_artifact_sets_matter_id"),
            nullable=False,
        ),
        sa.Column(
            "draft_id",
            sa.Uuid(),
            sa.ForeignKey("demand_drafts.id", name="fk_artifact_sets_draft_id"),
            nullable=False,
        ),
        sa.Column("draft_version", sa.Integer(), nullable=False),
        sa.Column("registry_version", sa.Integer(), nullable=False),
        sa.Column("artifacts", sa.JSON(), nullable=False),
        sa.Column(
            "built_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", name="fk_artifact_sets_built_by"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "matter_id",
            "draft_version",
            "registry_version",
            name="uq_artifact_set_matter_versions",
        ),
    )
    op.create_index("ix_artifact_sets_firm_id", "artifact_sets", ["firm_id"])
    op.create_index("ix_artifact_sets_matter_id", "artifact_sets", ["matter_id"])
    op.create_index("ix_artifact_sets_draft_id", "artifact_sets", ["draft_id"])


def downgrade() -> None:
    op.drop_index("ix_artifact_sets_draft_id", table_name="artifact_sets")
    op.drop_index("ix_artifact_sets_matter_id", table_name="artifact_sets")
    op.drop_index("ix_artifact_sets_firm_id", table_name="artifact_sets")
    op.drop_table("artifact_sets")
