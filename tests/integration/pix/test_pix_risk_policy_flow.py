import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from controllers.pix_bootstrap_policy import PixBootstrapPolicy
from controllers.pix_transaction_controller import PixTransactionController
from models import PixAnalysis, PixTransaction
from repositories import PixTransactionRepository
from tests.utils import PayloadGenerator, RequestGenerator


DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATABASE_NAME = make_url(DATABASE_URL).database if DATABASE_URL else ""
INTEGRATION_ENABLED = os.environ.get("PIX_INTEGRATION_TESTS") == "1"
SAFE_TEST_DATABASE = bool(DATABASE_NAME and "test" in DATABASE_NAME.lower())

pytestmark = pytest.mark.skipif(
    not (INTEGRATION_ENABLED and SAFE_TEST_DATABASE),
    reason="requires PIX_INTEGRATION_TESTS=1 and a DATABASE_URL whose database name contains 'test'",
)


def _payload(external_id: str, payer: str, amount: int) -> dict:
    payload = PayloadGenerator.create_pix_transaction_payload(external_id=external_id)
    payload["amount"] = amount
    payload["source_account"]["owner"]["document_number"] = f"SECRET-DOC-{payer}"
    payload["source_account"]["branch"] = "0001"
    payload["source_account"]["account_number"] = f"ACCOUNT-{payer}"
    return payload


def _assert_post_and_get(payload: dict, expected_status: str, expected_reason: str) -> dict:
    post_status, post_response = RequestGenerator.POST_pix_transaction(payload)
    get_status, get_response = RequestGenerator.GET_pix_transaction(payload["id"])

    assert post_status == get_status == 200
    assert post_response == get_response
    assert post_response["status"] == expected_status
    assert post_response["metadata"]["policy_version"] == "mvp-risk-experimental-v1"
    assert post_response["metadata"]["reason_code"] == expected_reason
    assert set(post_response["metadata"]) == {
        "schema_version",
        "policy_version",
        "reason_code",
        "reason_description",
    }
    assert payload["source_account"]["owner"]["document_number"] not in str(post_response)
    assert "evidence_snapshot" not in post_response
    return post_response


def _evidence_for(external_id: str) -> dict:
    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as connection:
            return connection.execute(
                select(PixAnalysis.evidence_snapshot)
                .join(PixTransaction, PixAnalysis.transaction_id == PixTransaction.id)
                .where(PixTransaction.external_id == external_id)
            ).scalar_one()
    finally:
        engine.dispose()


def test_http_post_and_get_produce_approved_decision():
    payer = str(uuid4())
    payload = _payload(f"pix-policy-approved-{uuid4()}", payer, 100_00)

    _assert_post_and_get(
        payload,
        "approved",
        "RISK_WITHIN_EXPERIMENTAL_POLICY",
    )


def test_http_post_and_get_produce_two_factor_recommendation_without_executing_it():
    payer = str(uuid4())
    payload = _payload(f"pix-policy-2fa-{uuid4()}", payer, 500_000)

    response = _assert_post_and_get(
        payload,
        "two_factor_required",
        "ADDITIONAL_VERIFICATION_RECOMMENDED",
    )

    assert "challenge" not in response["metadata"]
    assert "authentication" not in response["metadata"]


def test_http_post_and_get_produce_rejected_decision_at_combined_boundary():
    payer = str(uuid4())
    responses = []
    for sequence in range(8):
        payload = _payload(
            f"pix-policy-rejected-{sequence}-{uuid4()}",
            payer,
            375_000,
        )
        responses.append(RequestGenerator.POST_pix_transaction(payload))

    final_payload = payload
    http_status, response = responses[-1]
    get_status, get_response = RequestGenerator.GET_pix_transaction(final_payload["id"])

    assert http_status == get_status == 200
    assert response == get_response
    assert response["status"] == "rejected"
    assert response["metadata"]["reason_code"] == "SUSPICIOUS_BURST_AND_VOLUME"
    assert response["metadata"]["policy_version"] == "mvp-risk-experimental-v1"
    evidence = _evidence_for(final_payload["id"])
    assert evidence["policy"]["triggered_signals"][-2:] == [
        "CRITICAL_ATTEMPT_COUNT_5M",
        "CRITICAL_ATTEMPT_TOTAL_5M",
    ]


def test_replay_preserves_legacy_bootstrap_decision_after_active_policy_changes():
    external_id = f"pix-legacy-bootstrap-{uuid4()}"
    payload = _payload(external_id, str(uuid4()), 3_000_000)
    engine = create_engine(DATABASE_URL)
    session_factory = sessionmaker(bind=engine, autoflush=False)
    session = session_factory()
    try:
        controller = PixTransactionController.__new__(PixTransactionController)
        controller.session = session
        controller.pix_transaction_repository = PixTransactionRepository(
            type("Context", (), {"db_session": session})()
        )
        controller.policy = PixBootstrapPolicy("test")
        original = controller.create(payload)
    finally:
        session.close()

    replay_status, replay = RequestGenerator.POST_pix_transaction(payload)

    with engine.connect() as connection:
        counts = connection.execute(
            select(
                func.count(func.distinct(PixTransaction.id)),
                func.count(func.distinct(PixAnalysis.analysis_key)),
            )
            .select_from(PixTransaction)
            .join(PixAnalysis, PixAnalysis.transaction_id == PixTransaction.id)
            .where(PixTransaction.external_id == external_id)
        ).one()
    engine.dispose()

    assert replay_status == 200
    assert replay == original
    assert replay["status"] == "approved"
    assert replay["metadata"]["policy_version"] == "bootstrap-dev-v1"
    assert tuple(counts) == (1, 1)
