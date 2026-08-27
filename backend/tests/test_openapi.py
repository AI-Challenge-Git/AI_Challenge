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


def test_agent_vertical_slice_is_present_in_openapi() -> None:
    schema = app.openapi()
    paths = schema["paths"]

    assert "/api/auth/login" in paths
    assert "/api/agent/consultation-cards" in paths
    assert "/api/consultation-cards/lookup" in paths
    assert "/api/consultation-cards/verifications" in paths
    verification = schema["components"]["schemas"]["AgentVerificationRequest"]
    assert verification["additionalProperties"] is False
    assert "order_succeeded" not in verification["properties"]
    assert verification["properties"]["symbol_code"]["anyOf"][0]["pattern"] == ("^[0-9A-Z]{6}$")
    consultation = schema["components"]["schemas"]["ConsultationConfirmation"]
    assert consultation["properties"]["symbol_code"]["anyOf"][0]["pattern"] == ("^[0-9A-Z]{6}$")
    assert set(schema["components"]["schemas"]["AgentRole"]["enum"]) == {
        "AGENT",
        "OPERATOR",
    }
