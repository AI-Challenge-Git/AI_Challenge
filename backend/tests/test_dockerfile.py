from pathlib import Path


def test_api_image_runs_non_root_without_per_instance_migration() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")
    compose = (Path(__file__).parents[2] / "compose.yaml").read_text(encoding="utf-8")

    assert "USER app" in dockerfile
    assert "alembic upgrade head" not in dockerfile
    assert 'CMD ["uvicorn", "app.main:app"' in dockerfile
    assert "alembic upgrade head && exec uvicorn" in compose
    assert "AI_ADAPTER: ${AI_ADAPTER:-fake}" in compose
    assert "OPENAI_API_KEY: ${OPENAI_API_KEY:-}" in compose
    assert "attachment_data:/app/data/attachments" in compose
    assert "chown -R app:app /app/data" in dockerfile
    assert "COPY scripts ./scripts" in dockerfile
