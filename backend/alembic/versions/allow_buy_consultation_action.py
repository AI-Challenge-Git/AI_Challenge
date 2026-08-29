"""Allow BUY actions on consultation cards.

Revision ID: allow_buy_consultation_action
Revises: add_private_attachments
Create Date: 2026-08-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "allow_buy_consultation_action"
down_revision: str | Sequence[str] | None = "add_private_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_consultation_cards_action_value"),
        "consultation_cards",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_consultation_cards_action_value"),
        "consultation_cards",
        "action IN ('SELL', 'BUY', 'UNKNOWN')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_consultation_cards_action_value"),
        "consultation_cards",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_consultation_cards_action_value"),
        "consultation_cards",
        "action IN ('SELL', 'UNKNOWN')",
    )
