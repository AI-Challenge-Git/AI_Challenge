from pathlib import Path


def test_api_image_runs_non_root_without_per_instance_migration() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")
    compose = (Path(__file__).parents[2] / "compose.yaml").read_text(encoding="utf-8")

    assert "USER app" in dockerfile
    assert "alembic upgrade head" not in dockerfile
    assert 'CMD ["uvicorn", "app.main:app"' in dockerfile
    assert "alembic upgrade head && exec uvicorn" in compose
