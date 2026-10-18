import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.utils import PayloadGenerator, RequestGenerator


DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATABASE_NAME = make_url(DATABASE_URL).database if DATABASE_URL else ""
INTEGRATION_ENABLED = os.environ.get("PIX_INTEGRATION_TESTS") == "1"
SAFE_TEST_DATABASE = bool(DATABASE_NAME and "test" in DATABASE_NAME.lower())

pytestmark = pytest.mark.skipif(
    not (INTEGRATION_ENABLED and SAFE_TEST_DATABASE),
    reason="requires PIX_INTEGRATION_TESTS=1 and a DATABASE_URL whose database name contains 'test'",
)


def test_post_persists_both_rows_and_get_returns_original_decision():
    external_id = f"pix-integration-{uuid4()}"
    payload = PayloadGenerator.create_pix_transaction_payload(external_id=external_id)

    status, post_response = RequestGenerator.POST_pix_transaction(payload)

    assert status == 200
    assert set(post_response) == {"key", "status", "metadata"}
    assert post_response["status"] == "approved"

    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as connection:
            persisted = connection.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT t.id) AS transaction_count,
                           COUNT(DISTINCT a.analysis_key) AS analysis_count
                    FROM pix_transactions t
                    JOIN pix_analyses a ON a.transaction_id = t.id
                    WHERE t.external_id = :external_id
                    """
                ),
                {"external_id": external_id},
            ).one()
    finally:
        engine.dispose()

    assert persisted.transaction_count == 1
    assert persisted.analysis_count == 1

    status, get_response = RequestGenerator.GET_pix_transaction(external_id)
    assert status == 200
    assert get_response == post_response


def test_get_unknown_pix_returns_404():
    status, response = RequestGenerator.GET_pix_transaction(f"pix-missing-{uuid4()}")

    assert status == 404
    assert response["code"] == "QIT002002"
