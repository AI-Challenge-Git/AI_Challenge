from pathlib import Path

from sqlalchemy import CHAR, BigInteger, CheckConstraint, DateTime, LargeBinary

from app.db import Base
from app.models import PolicySnapshot


def test_core_metadata_contains_required_business_tables() -> None:
    assert PolicySnapshot.__tablename__ == "policy_snapshots"
    assert {
        "policy_snapshots",
        "reports",
        "report_analyses",
        "technical_symptoms",
        "consultation_cards",
        "attachments",
        "idempotency_records",
        "audit_logs",
        "object_deletion_jobs",
    } <= Base.metadata.tables.keys()


def test_sensitive_and_technical_data_boundaries_are_structural() -> None:
    tables = Base.metadata.tables
    all_columns = {column.name for table in tables.values() for column in table.columns}
    report_columns = set(tables["reports"].columns.keys())
    technical_columns = set(tables["technical_symptoms"].columns.keys())
    consultation_columns = set(tables["consultation_cards"].columns.keys())
    attachment_columns = set(tables["attachments"].columns.keys())
    idempotency_columns = set(tables["idempotency_records"].columns.keys())
    deletion_job_columns = set(tables["object_deletion_jobs"].columns.keys())

    assert {"raw_text", "original_text", "session_token", "reference_number"}.isdisjoint(
        all_columns
    )
    assert "session_digest" in report_columns
    assert {"symbol_name", "symbol_code", "quantity", "price_krw", "action"}.isdisjoint(
        technical_columns
    )
    assert {"symptom", "error_code", "issue_type"}.isdisjoint(consultation_columns)
    assert "content" not in attachment_columns
    assert {"object_key", "content_sha256", "byte_size"} <= attachment_columns
    assert {
        "principal_digest",
        "operation",
        "client_request_id",
        "payload_sha256",
        "safe_failure_code",
        "processing_status",
        "completed_at",
        "purge_at",
    } <= idempotency_columns
    assert {
        "raw_text",
        "masked_text",
        "attachment_url",
        "object_key",
        "token",
        "reference_number",
    }.isdisjoint(idempotency_columns)
    assert {"object_key", "status", "attempt_count", "next_attempt_at"} <= deletion_job_columns
    assert not any(
        "vector" in str(column.type).lower()
        for table in tables.values()
        for column in table.columns
    )


def test_core_types_and_server_times_match_the_decision() -> None:
    tables = Base.metadata.tables
    session_digest = tables["reports"].c.session_digest.type
    content_sha256 = tables["policy_snapshots"].c.content_sha256.type
    quantity = tables["consultation_cards"].c.quantity.type
    price = tables["consultation_cards"].c.price_krw.type

    assert isinstance(session_digest, LargeBinary)
    assert session_digest.length == 32
    assert isinstance(content_sha256, CHAR)
    assert content_sha256.length == 64
    assert isinstance(quantity, BigInteger)
    assert isinstance(price, BigInteger)

    for table_name, column_name in (
        ("reports", "received_at"),
        ("reports", "confirmed_at"),
        ("reports", "purge_at"),
        ("technical_symptoms", "reported_occurred_at"),
        ("consultation_cards", "attempted_at"),
    ):
        column_type = tables[table_name].c[column_name].type
        assert isinstance(column_type, DateTime)
        assert column_type.timezone is True


def test_constraints_and_indexes_have_deterministic_names() -> None:
    metadata = Base.metadata
    names = {
        constraint.name for table in metadata.tables.values() for constraint in table.constraints
    }
    names.update(index.name for table in metadata.tables.values() for index in table.indexes)

    assert None not in names
    assert "uq_reports_session_digest" in names
    assert "ck_reports_confirmed_at_matches_status" in names
    assert "uq_report_analyses_report_id" in names
    assert "ck_consultation_cards_market_order_without_price" in names
    assert "ix_reports_session_received" in names
    assert "ix_reports_purge_at" in names
    assert "ix_object_deletion_jobs_ready" in names


def test_consultation_card_constraint_accepts_all_order_actions() -> None:
    constraint = next(
        item
        for item in Base.metadata.tables["consultation_cards"].constraints
        if item.name == "ck_consultation_cards_action_value"
    )
    assert isinstance(constraint, CheckConstraint)

    sql = str(constraint.sqltext)
    assert all(f"'{action}'" in sql for action in ("BUY", "SELL", "UNKNOWN"))


def test_core_migration_follows_0001_and_excludes_future_tables() -> None:
    migration = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0002_core_report_flow.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision: str | Sequence[str] | None = "0001"' in migration
    for table_name in (
        "policy_snapshots",
        "reports",
        "report_analyses",
        "technical_symptoms",
        "consultation_cards",
    ):
        assert f'        "{table_name}",' in migration
    for future_name in (
        "reference_hash",
        "expires_at",
        "technical_embeddings",
        "signal_clusters",
        "audit_logs",
    ):
        assert future_name not in migration
