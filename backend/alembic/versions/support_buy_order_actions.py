"""Allow BUY consultation-card actions.

Revision ID: support_buy_order_actions
Revises: add_private_attachments
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "support_buy_order_actions"
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
        "action IN ('BUY', 'SELL', 'UNKNOWN')",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM consultation_cards WHERE action = 'BUY') THEN
                RAISE EXCEPTION
                    'cannot downgrade while consultation_cards contains BUY rows';
            END IF;
        END
        $$
        """
    )
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
