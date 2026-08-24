import json
from pathlib import Path

from app.main import app


def test_committed_openapi_is_current() -> None:
    snapshot = Path(__file__).parents[1] / "openapi.json"
    assert json.loads(snapshot.read_text(encoding="utf-8")) == app.openapi()


def test_multipart_contract_requires_redaction_confirmation() -> None:
    multipart = app.openapi()["paths"]["/api/reports/analyze"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["schema"]

    assert "screenshot_redacted_confirmed" in multipart["required"]
    assert multipart["properties"]["screenshot_redacted_confirmed"] == {
        "type": "boolean",
        "const": True,
        "description": "The user confirms that sensitive image content was redacted.",
    }
