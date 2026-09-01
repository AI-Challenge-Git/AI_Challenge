from pathlib import Path


def test_api_image_runs_non_root_without_per_instance_migration() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")
    compose = (Path(__file__).parents[2] / "compose.yaml").read_text(encoding="utf-8")

    assert "USER app" in dockerfile
    assert "alembic upgrade head" not in dockerfile
    assert "exec uvicorn app.main:app" in dockerfile
    assert "alembic upgrade head && exec uvicorn" in compose
    assert "AI_ADAPTER: ${AI_ADAPTER:-openai}" in compose
    assert "OPENAI_API_KEY: ${OPENAI_API_KEY:-}" in compose
    assert "SIGNAL_EMBEDDING_MODEL_REVISION: ${SIGNAL_EMBEDDING_MODEL_REVISION:-}" in compose
    assert "ANALYSIS_PENDING_STALE_SECONDS: ${ANALYSIS_PENDING_STALE_SECONDS:-180}" in compose
    assert "REPORT_ANALYZE_LIMIT: ${REPORT_ANALYZE_LIMIT:-5}" in compose
    assert "REPORT_ANALYZE_WINDOW_SECONDS: ${REPORT_ANALYZE_WINDOW_SECONDS:-60}" in compose
    assert "SIGNAL_WORKER_MAX_ATTEMPTS: ${SIGNAL_WORKER_MAX_ATTEMPTS:-5}" in compose
    assert "SIGNAL_WORKER_POLL_SECONDS: ${SIGNAL_WORKER_POLL_SECONDS:-5}" in compose
    assert '"scripts.process_signal_jobs", "--forever"' in compose
    assert "attachment_data:/app/data/attachments" in compose
    assert "chown -R app:app /app/data" in dockerfile
    assert "COPY scripts ./scripts" in dockerfile
    assert "${PORT:-8000}" in dockerfile
