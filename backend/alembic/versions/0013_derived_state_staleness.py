"""derived_state_staleness — the invalidation cursor + plan stale marker (BUS-05)

Hand-written migration. Two nullable columns:

- ``matters.invalidation_applied_registry_version`` — the durable recovery cursor: the
  highest registry version whose downstream-staleness consequences have been APPLIED.
  DELIBERATELY NOT BACKFILLED: stamping every matter with its current ``registry_version``
  would grandfather matters already left stale by the pre-fix behavior. NULL means
  "not yet reconciled" — the orchestrator's reconciliation path
  (``app.engine.orchestrator.registry_bump.reconcile_matter_cursor``) evaluates the matter's
  derived state on next touch, applies any missed invalidation, and only then sets the
  cursor (ADR-0012).
- ``strategy_plans.invalidated_by_registry_version`` — set when a registry bump made the
  plan stale; ``approved`` survives as historical evidence, but an invalidated approval is
  never reusable.

Plain nullable ADD COLUMNs, portable on SQLite and Postgres.

Revision ID: 0013_derived_state_staleness
Revises: 0012_rule_pack_pins
Create Date: 2026-07-11

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_derived_state_staleness"
down_revision: str | None = "0012_rule_pack_pins"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "matters",
        sa.Column("invalidation_applied_registry_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "strategy_plans",
        sa.Column("invalidated_by_registry_version", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("strategy_plans") as batch:
        batch.drop_column("invalidated_by_registry_version")
    with op.batch_alter_table("matters") as batch:
        batch.drop_column("invalidation_applied_registry_version")
