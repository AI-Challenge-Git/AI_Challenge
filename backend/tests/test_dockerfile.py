from pathlib import Path


def test_api_image_uses_non_root_user_and_execs_uvicorn() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")

    assert "USER app" in dockerfile
    assert "alembic upgrade head && exec uvicorn" in dockerfile
