import json
from pathlib import Path

from app.main import app


def test_committed_openapi_is_current() -> None:
    snapshot = Path(__file__).parents[1] / "openapi.json"
    assert json.loads(snapshot.read_text(encoding="utf-8")) == app.openapi()
