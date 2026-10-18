import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
import requests
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from models import PixAnalysis, PixTransaction
from tests.utils import PayloadGenerator, RequestGenerator
from utils.pix_history import payer_advisory_lock_key


DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATABASE_NAME = make_url(DATABASE_URL).database if DATABASE_URL else ""
INTEGRATION_ENABLED = os.environ.get("PIX_INTEGRATION_TESTS") == "1"
SAFE_TEST_DATABASE = bool(DATABASE_NAME and "test" in DATABASE_NAME.lower())

pytestmark = pytest.mark.skipif(
    not (INTEGRATION_ENABLED and SAFE_TEST_DATABASE),
    reason="requires PIX_INTEGRATION_TESTS=1 and a DATABASE_URL whose database name contains 'test'",
)


def _post_together(barrier: Barrier, payload: dict):
    barrier.wait()
    return RequestGenerator.POST_pix_transaction(payload)


def test_excessive_payer_lock_wait_fails_technically_without_partial_persistence():
    external_id = f"pix-lock-timeout-{uuid4()}"
    payload = PayloadGenerator.create_pix_transaction_payload(external_id)
    source = payload["source_account"]
    lock_key = payer_advisory_lock_key(
        source["owner"]["document_number"],
        source["branch"],
        source["account_number"],
    )
    engine = create_engine(DATABASE_URL)
    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )
        started_at = time.perf_counter()
        response = requests.post(
            f"http://{os.environ['SERVER_LOCALHOST']}:{os.environ['API_PORT']}/pix/transaction",
            json=payload,
            headers={"INTERNAL-TOKEN": os.environ["INTERNAL_TOKEN"]},
            timeout=5,
        )
        elapsed_seconds = time.perf_counter() - started_at

        assert response.status_code == 500
        assert response.json()["code"] == "QIT000500"
        assert 0.7 <= elapsed_seconds < 4
    finally:
        transaction.rollback()
        connection.close()

    try:
        with engine.connect() as verification:
            count = verification.execute(
                select(func.count(PixTransaction.id)).where(
                    PixTransaction.external_id == external_id
                )
            ).scalar_one()
        assert count == 0
    finally:
        engine.dispose()


def test_distinct_payers_can_be_processed_concurrently_without_shared_history():
    payloads = [
        PayloadGenerator.create_pix_transaction_payload(
            f"pix-distinct-payer-concurrent-{uuid4()}"
        )
        for _ in range(4)
    ]
    barrier = Barrier(len(payloads))

    with ThreadPoolExecutor(max_workers=len(payloads)) as executor:
        results = list(
            executor.map(lambda payload: _post_together(barrier, payload), payloads)
        )

    assert [status for status, _ in results] == [200] * len(payloads)

    external_ids = [payload["id"] for payload in payloads]
    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                select(PixTransaction.external_id, PixAnalysis.evidence_snapshot)
                .join(PixAnalysis, PixAnalysis.transaction_id == PixTransaction.id)
                .where(PixTransaction.external_id.in_(external_ids))
            ).all()
    finally:
        engine.dispose()

    assert len(rows) == len(payloads)
    assert all(evidence["prior_attempt_count"] == 0 for _, evidence in rows)
    assert all(
        evidence["counterparty"]["observation"] == "insufficient_history"
        for _, evidence in rows
    )
