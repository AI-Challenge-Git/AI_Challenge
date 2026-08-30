"""Allow rate limiting the public signal dashboard.

Revision ID: signal_dashboard_rate_limit
Revises: index_1024_signal_embeddings
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "signal_dashboard_rate_limit"
down_revision: str | Sequence[str] | None = "index_1024_signal_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_rate_limit_buckets_scope_value"


def upgrade() -> None:
    op.drop_constraint(op.f(_CONSTRAINT), "rate_limit_buckets", type_="check")
    op.create_check_constraint(
        op.f(_CONSTRAINT),
        "rate_limit_buckets",
        "scope IN ('AGENT_LOGIN_FAILURE', 'AGENT_CARD_LOOKUP', 'SIGNAL_DASHBOARD')",
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM rate_limit_buckets WHERE scope = 'SIGNAL_DASHBOARD'"))
    op.drop_constraint(op.f(_CONSTRAINT), "rate_limit_buckets", type_="check")
    op.create_check_constraint(
        op.f(_CONSTRAINT),
        "rate_limit_buckets",
        "scope IN ('AGENT_LOGIN_FAILURE', 'AGENT_CARD_LOOKUP')",
    )
