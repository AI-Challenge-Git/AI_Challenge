"""Add versioned incident-signal storage and processing state.

Revision ID: add_incident_signal_workflow
Revises: add_krx_symbol_master
Create Date: 2026-08-29
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "add_incident_signal_workflow"
down_revision: str | Sequence[str] | None = "add_krx_symbol_master"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class Vector(sa.types.UserDefinedType[list[float]]):
    cache_ok = True

    def get_col_spec(self, **_: Any) -> str:
        return "vector"


def upgrade() -> None:
    op.create_table(
        "clustering_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("min_unique_sessions", sa.Integer(), nullable=False),
        sa.Column("review_priority_threshold", sa.Integer(), nullable=False),
        sa.Column("similarity_threshold", sa.Float(), nullable=False),
        sa.Column("structured_rules_version", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("model_revision", sa.String(length=128), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("normalization", sa.String(length=16), nullable=False),
        sa.Column("input_format", sa.String(length=64), nullable=False),
        sa.Column("distance_metric", sa.String(length=16), nullable=False),
        sa.Column("taxonomy_version", sa.String(length=64), nullable=False),
        sa.Column("baseline_policy_version", sa.String(length=64), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('EXPERIMENTAL', 'APPROVED', 'RETIRED')",
            name=op.f("ck_clustering_policies_status_value"),
        ),
        sa.CheckConstraint(
            "window_seconds > 0",
            name=op.f("ck_clustering_policies_window_seconds_positive"),
        ),
        sa.CheckConstraint(
            "min_unique_sessions > 0",
            name=op.f("ck_clustering_policies_min_unique_sessions_positive"),
        ),
        sa.CheckConstraint(
            "review_priority_threshold >= min_unique_sessions",
            name=op.f("ck_clustering_policies_review_threshold_not_below_minimum"),
        ),
        sa.CheckConstraint(
            "similarity_threshold > 0 AND similarity_threshold <= 1",
            name=op.f("ck_clustering_policies_similarity_threshold_range"),
        ),
        sa.CheckConstraint(
            "embedding_dimension > 0",
            name=op.f("ck_clustering_policies_embedding_dimension_positive"),
        ),
        sa.CheckConstraint(
            "normalization IN ('L2', 'NONE')",
            name=op.f("ck_clustering_policies_normalization_value"),
        ),
        sa.CheckConstraint(
            "distance_metric = 'COSINE'",
            name=op.f("ck_clustering_policies_distance_metric_cosine"),
        ),
        sa.CheckConstraint(
            "structured_rules_version = 'hard-gate.v1'",
            name=op.f("ck_clustering_policies_structured_rules_version_value"),
        ),
        sa.CheckConstraint(
            "((status = 'APPROVED' AND approved_by IS NOT NULL AND approved_at IS NOT NULL) "
            "OR status <> 'APPROVED')",
            name=op.f("ck_clustering_policies_approval_metadata"),
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"],
            ["agent_accounts.id"],
            name=op.f("fk_clustering_policies_approved_by_agent_accounts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clustering_policies")),
        sa.UniqueConstraint("policy_version", name=op.f("uq_clustering_policies_policy_version")),
    )
    op.create_index(
        "uq_clustering_policies_active",
        "clustering_policies",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "technical_embeddings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("technical_symptom_id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("model_revision", sa.String(length=128), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("normalization", sa.String(length=16), nullable=False),
        sa.Column("input_format", sa.String(length=64), nullable=False),
        sa.Column("distance_metric", sa.String(length=16), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "embedding_dimension > 0",
            name=op.f("ck_technical_embeddings_embedding_dimension_positive"),
        ),
        sa.CheckConstraint(
            "normalization IN ('L2', 'NONE')",
            name=op.f("ck_technical_embeddings_normalization_value"),
        ),
        sa.CheckConstraint(
            "distance_metric = 'COSINE'",
            name=op.f("ck_technical_embeddings_distance_metric_cosine"),
        ),
        sa.CheckConstraint(
            "vector_dims(embedding) = embedding_dimension",
            name=op.f("ck_technical_embeddings_embedding_dimension_matches_vector"),
        ),
        sa.ForeignKeyConstraint(
            ["technical_symptom_id"],
            ["technical_symptoms.id"],
            name=op.f("fk_technical_embeddings_technical_symptom_id_technical_symptoms"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_technical_embeddings")),
        sa.UniqueConstraint(
            "technical_symptom_id",
            "model_id",
            "model_revision",
            "embedding_dimension",
            "normalization",
            "input_format",
            name=op.f("uq_technical_embeddings_technical_symptom_id"),
        ),
    )
    op.create_index(
        "ix_technical_embeddings_symptom_id",
        "technical_embeddings",
        ["technical_symptom_id"],
    )

    op.create_table(
        "signal_processing_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("technical_symptom_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("safe_error_code", sa.String(length=64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'FAILED', 'COMPLETED')",
            name=op.f("ck_signal_processing_jobs_status_value"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_signal_processing_jobs_attempt_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "safe_error_code IS NULL OR safe_error_code IN "
            "('EMBEDDING_UNAVAILABLE', 'INVALID_EMBEDDING', 'POLICY_MISMATCH', "
            "'EMBEDDING_INPUT_UNAVAILABLE')",
            name=op.f("ck_signal_processing_jobs_safe_error_code_value"),
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["clustering_policies.id"],
            name=op.f("fk_signal_processing_jobs_policy_id_clustering_policies"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name=op.f("fk_signal_processing_jobs_report_id_reports"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["technical_symptom_id"],
            ["technical_symptoms.id"],
            name=op.f("fk_signal_processing_jobs_technical_symptom_id_technical_symptoms"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signal_processing_jobs")),
        sa.UniqueConstraint("report_id", name=op.f("uq_signal_processing_jobs_report_id")),
        sa.UniqueConstraint(
            "technical_symptom_id",
            name=op.f("uq_signal_processing_jobs_technical_symptom_id"),
        ),
    )
    op.create_index(
        "ix_signal_processing_jobs_ready",
        "signal_processing_jobs",
        ["status", "next_attempt_at"],
    )

    op.create_table(
        "signal_clusters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("closure_reason", sa.String(length=40), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("feature_area", sa.String(length=64), nullable=False),
        sa.Column("reported_symptom_type", sa.String(length=64), nullable=False),
        sa.Column("submission_status_filter", sa.String(length=40), nullable=True),
        sa.Column("error_code_filter", sa.String(length=64), nullable=True),
        sa.Column("raw_report_count", sa.Integer(), nullable=False),
        sa.Column("reporting_unique_sessions", sa.Integer(), nullable=False),
        sa.Column("review_priority", sa.Boolean(), nullable=False),
        sa.Column("first_report_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_report_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("representative_symptom_id", sa.Uuid(), nullable=True),
        sa.Column("official_incident", sa.Boolean(), nullable=False),
        sa.Column("official_notice_url", sa.Text(), nullable=True),
        sa.Column("official_notice_linked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("official_notice_linked_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purge_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('CANDIDATE', 'SIGNAL_DETECTED', 'UNDER_REVIEW', 'CLOSED')",
            name=op.f("ck_signal_clusters_status_value"),
        ),
        sa.CheckConstraint(
            "closure_reason IS NULL OR closure_reason IN "
            "('WINDOW_EXPIRED', 'FALSE_POSITIVE', 'MERGED', "
            "'OFFICIAL_INCIDENT_RESOLVED', 'EVIDENCE_RECALCULATED')",
            name=op.f("ck_signal_clusters_closure_reason_value"),
        ),
        sa.CheckConstraint(
            "((status = 'CLOSED' AND closure_reason IS NOT NULL AND closed_at IS NOT NULL) OR "
            "(status <> 'CLOSED' AND closure_reason IS NULL AND closed_at IS NULL))",
            name=op.f("ck_signal_clusters_closed_metadata"),
        ),
        sa.CheckConstraint(
            "raw_report_count >= 0",
            name=op.f("ck_signal_clusters_raw_report_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "reporting_unique_sessions >= 0",
            name=op.f("ck_signal_clusters_reporting_unique_sessions_nonnegative"),
        ),
        sa.CheckConstraint(
            "reporting_unique_sessions <= raw_report_count",
            name=op.f("ck_signal_clusters_unique_sessions_not_above_raw_count"),
        ),
        sa.CheckConstraint(
            "((official_notice_url IS NULL AND official_notice_linked_at IS NULL AND "
            "official_notice_linked_by IS NULL) OR "
            "(official_notice_url IS NOT NULL AND official_notice_linked_at IS NOT NULL AND "
            "official_notice_linked_by IS NOT NULL))",
            name=op.f("ck_signal_clusters_official_notice_metadata"),
        ),
        sa.ForeignKeyConstraint(
            ["official_notice_linked_by"],
            ["agent_accounts.id"],
            name=op.f("fk_signal_clusters_official_notice_linked_by_agent_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["clustering_policies.id"],
            name=op.f("fk_signal_clusters_policy_id_clustering_policies"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["representative_symptom_id"],
            ["technical_symptoms.id"],
            name=op.f("fk_signal_clusters_representative_symptom_id_technical_symptoms"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signal_clusters")),
    )
    op.create_index(
        "ix_signal_clusters_dashboard",
        "signal_clusters",
        ["policy_id", "status", "last_report_at"],
    )
    op.create_index("ix_signal_clusters_purge_at", "signal_clusters", ["purge_at"])

    op.create_table(
        "signal_members",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("signal_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("technical_symptom_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_id", sa.Uuid(), nullable=False),
        sa.Column("similarity_at_join", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "similarity_at_join >= -1 AND similarity_at_join <= 1",
            name=op.f("ck_signal_members_similarity_at_join_range"),
        ),
        sa.ForeignKeyConstraint(
            ["embedding_id"],
            ["technical_embeddings.id"],
            name=op.f("fk_signal_members_embedding_id_technical_embeddings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name=op.f("fk_signal_members_report_id_reports"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signal_clusters.id"],
            name=op.f("fk_signal_members_signal_id_signal_clusters"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["technical_symptom_id"],
            ["technical_symptoms.id"],
            name=op.f("fk_signal_members_technical_symptom_id_technical_symptoms"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signal_members")),
        sa.UniqueConstraint("signal_id", "report_id", name=op.f("uq_signal_members_signal_id")),
    )
    op.create_index("ix_signal_members_report_id", "signal_members", ["report_id"])
    op.create_index("ix_signal_members_symptom_id", "signal_members", ["technical_symptom_id"])

    op.create_table(
        "signal_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("signal_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("before_status", sa.String(length=24), nullable=True),
        sa.Column("after_status", sa.String(length=24), nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("target_signal_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purge_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name=op.f("ck_signal_audit_events_action_format"),
        ),
        sa.CheckConstraint(
            "before_status IS NULL OR before_status IN "
            "('CANDIDATE', 'SIGNAL_DETECTED', 'UNDER_REVIEW', 'CLOSED')",
            name=op.f("ck_signal_audit_events_before_status_value"),
        ),
        sa.CheckConstraint(
            "after_status IS NULL OR after_status IN "
            "('CANDIDATE', 'SIGNAL_DETECTED', 'UNDER_REVIEW', 'CLOSED')",
            name=op.f("ck_signal_audit_events_after_status_value"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["agent_accounts.id"],
            name=op.f("fk_signal_audit_events_actor_id_agent_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signal_clusters.id"],
            name=op.f("fk_signal_audit_events_signal_id_signal_clusters"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signal_audit_events")),
    )
    op.create_index("ix_signal_audit_events_signal_id", "signal_audit_events", ["signal_id"])
    op.create_index("ix_signal_audit_events_purge_at", "signal_audit_events", ["purge_at"])


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS ("
        "SELECT 1 FROM clustering_policies UNION ALL "
        "SELECT 1 FROM technical_embeddings UNION ALL "
        "SELECT 1 FROM signal_processing_jobs UNION ALL "
        "SELECT 1 FROM signal_clusters UNION ALL "
        "SELECT 1 FROM signal_members UNION ALL "
        "SELECT 1 FROM signal_audit_events"
        ") THEN RAISE EXCEPTION 'cannot downgrade while incident-signal data exists; "
        "process or archive it explicitly first'; END IF; END $$"
    )
    op.drop_index("ix_signal_audit_events_purge_at", table_name="signal_audit_events")
    op.drop_index("ix_signal_audit_events_signal_id", table_name="signal_audit_events")
    op.drop_table("signal_audit_events")
    op.drop_index("ix_signal_members_symptom_id", table_name="signal_members")
    op.drop_index("ix_signal_members_report_id", table_name="signal_members")
    op.drop_table("signal_members")
    op.drop_index("ix_signal_clusters_purge_at", table_name="signal_clusters")
    op.drop_index("ix_signal_clusters_dashboard", table_name="signal_clusters")
    op.drop_table("signal_clusters")
    op.drop_index("ix_signal_processing_jobs_ready", table_name="signal_processing_jobs")
    op.drop_table("signal_processing_jobs")
    op.drop_index("ix_technical_embeddings_symptom_id", table_name="technical_embeddings")
    op.drop_table("technical_embeddings")
    op.drop_index("uq_clustering_policies_active", table_name="clustering_policies")
    op.drop_table("clustering_policies")
