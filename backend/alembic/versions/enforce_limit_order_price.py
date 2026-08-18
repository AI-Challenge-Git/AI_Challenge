"""Require a positive price for limit orders.

Revision ID: enforce_limit_order_price
Revises: report_lifecycle_security
Create Date: 2026-08-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "enforce_limit_order_price"
down_revision: str | Sequence[str] | None = "report_lifecycle_security"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        op.f("ck_consultation_cards_limit_order_requires_price"),
        "consultation_cards",
        "order_type <> 'LIMIT' OR price_krw IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_consultation_cards_limit_order_requires_price"),
        "consultation_cards",
        type_="check",
    )
