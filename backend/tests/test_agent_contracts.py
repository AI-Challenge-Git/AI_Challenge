from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas import AgentVerificationRequest, ConsultationCardLookupRequest
from app.security import hash_password, make_opaque_token, opaque_token_digest, verify_password


def test_password_hash_uses_argon2_and_never_equals_plaintext() -> None:
    password_hash = hash_password("synthetic-password")

    assert password_hash.startswith("$argon2")
    assert "synthetic-password" not in password_hash
    assert verify_password("synthetic-password", password_hash)
    assert not verify_password("wrong-password", password_hash)
    assert not verify_password("synthetic-password", None)


def test_agent_token_is_256_bit_opaque_and_key_separated() -> None:
    token = make_opaque_token()

    assert len(token) == 43
    assert opaque_token_digest(token, b"a" * 32) != opaque_token_digest(token, b"b" * 32)
    assert token.encode() != opaque_token_digest(token, b"a" * 32)


def test_card_selector_requires_exactly_one_identifier() -> None:
    reference = "KBSOS-" + "A" * 26
    card_id = uuid4()

    assert (
        ConsultationCardLookupRequest(reference_number=f"  {reference.lower()}  ").reference_number
        == reference
    )
    assert ConsultationCardLookupRequest(card_id=card_id).card_id == card_id
    with pytest.raises(ValidationError):
        ConsultationCardLookupRequest()
    with pytest.raises(ValidationError):
        ConsultationCardLookupRequest(reference_number=reference, card_id=card_id)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "action": "BUY",
            "symbol_name": "합성종목",
            "symbol_code": "000001",
            "quantity": 1,
            "order_type": "LIMIT",
            "price_krw": None,
        },
        {
            "action": "SELL",
            "symbol_name": "합성종목",
            "symbol_code": "000001",
            "quantity": 1,
            "order_type": "MARKET",
            "price_krw": 10_000,
        },
        {
            "action": "SELL",
            "symbol_name": "합성종목",
            "symbol_code": "000001",
            "quantity": True,
            "order_type": "MARKET",
            "price_krw": None,
        },
    ],
)
def test_verification_rejects_invalid_limit_market_and_non_strict_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        AgentVerificationRequest.model_validate(
            {
                "card_id": str(uuid4()),
                **payload,
                "submission_status": "CUSTOMER_REPORTED_SUBMITTED",
                "order_history_checked": True,
                "client_request_id": str(uuid4()),
            }
        )


def test_verification_accepts_buy_sell_and_rejects_order_result_field() -> None:
    for action in ("BUY", "SELL"):
        request = AgentVerificationRequest.model_validate(
            {
                "card_id": str(uuid4()),
                "action": action,
                "symbol_name": "  합성종목  ",
                "symbol_code": "000001",
                "quantity": 1,
                "order_type": "MARKET",
                "price_krw": None,
                "submission_status": "CUSTOMER_REPORTED_SUBMITTED",
                "order_history_checked": True,
                "client_request_id": str(uuid4()),
            }
        )
        assert request.action.value == action
        assert request.symbol_name == "합성종목"

    with pytest.raises(ValidationError):
        AgentVerificationRequest.model_validate(
            {
                "card_id": str(uuid4()),
                "action": "BUY",
                "symbol_name": "합성종목",
                "symbol_code": "000001",
                "quantity": 1,
                "order_type": "MARKET",
                "price_krw": None,
                "submission_status": "CUSTOMER_REPORTED_SUBMITTED",
                "order_history_checked": True,
                "order_succeeded": True,
                "client_request_id": str(uuid4()),
            }
        )
