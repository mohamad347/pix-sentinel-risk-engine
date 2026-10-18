import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from controllers.pix_bootstrap_policy import PixBootstrapPolicy
from controllers.pix_transaction_controller import PixTransactionController
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


def test_replay_preserves_decision_and_does_not_create_more_rows():
    external_id = f"pix-replay-{uuid4()}"
    payload = PayloadGenerator.create_pix_transaction_payload(external_id=external_id)

    first_status, first_response = RequestGenerator.POST_pix_transaction(payload)
    replay_status, replay_response = RequestGenerator.POST_pix_transaction(payload)

    assert first_status == replay_status == 200
    assert replay_response == first_response

    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as connection:
            counts = connection.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT t.id), COUNT(DISTINCT a.analysis_key)
                    FROM pix_transactions t
                    JOIN pix_analyses a ON a.transaction_id = t.id
                    WHERE t.external_id = :external_id
                    """
                ),
                {"external_id": external_id},
            ).one()
    finally:
        engine.dispose()

    assert tuple(counts) == (1, 1)


def test_same_id_with_different_payload_returns_safe_409_and_keeps_original():
    external_id = f"pix-conflict-{uuid4()}"
    original = PayloadGenerator.create_pix_transaction_payload(external_id=external_id)
    conflicting = deepcopy(original)
    conflicting["source_account"]["owner"]["document_number"] = "SECRET-DIFFERENT-PAYER"

    first_status, first_response = RequestGenerator.POST_pix_transaction(original)
    conflict_status, conflict_response = RequestGenerator.POST_pix_transaction(conflicting)
    get_status, get_response = RequestGenerator.GET_pix_transaction(external_id)

    assert first_status == 200
    assert conflict_status == 409
    assert conflict_response["code"] == "QIT002004"
    assert "SECRET-DIFFERENT-PAYER" not in str(conflict_response)
    assert get_status == 200
    assert get_response == first_response


def _post_together(barrier: Barrier, payload: dict):
    barrier.wait()
    return RequestGenerator.POST_pix_transaction(payload)


def test_concurrent_identical_requests_converge_to_one_persisted_decision():
    external_id = f"pix-race-same-{uuid4()}"
    payload = PayloadGenerator.create_pix_transaction_payload(external_id=external_id)
    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_post_together, barrier, deepcopy(payload)) for _ in range(2)]
        results = [future.result() for future in futures]

    assert [status for status, _ in results] == [200, 200]
    assert results[0][1] == results[1][1]


def test_postgresql_unique_collision_is_recovered_after_both_prechecks_miss():
    class CountingPolicy(PixBootstrapPolicy):
        calls = 0

        def evaluate(self, transaction_data):
            type(self).calls += 1
            return super().evaluate(transaction_data)

    class SynchronizedRepository(PixTransactionRepository):
        def __init__(self, session, barrier):
            self.session = session
            self.barrier = barrier
            self.first_lookup = True

        def get_transaction_by_external_id(self, external_id):
            transaction = super().get_transaction_by_external_id(external_id)
            if self.first_lookup:
                self.first_lookup = False
                assert transaction is None
                self.barrier.wait()
            return transaction

    def create_with_separate_session(payload, barrier, session_factory):
        session = session_factory()
        try:
            controller = PixTransactionController.__new__(PixTransactionController)
            controller.session = session
            controller.pix_transaction_repository = SynchronizedRepository(session, barrier)
            controller.policy = CountingPolicy("test")
            return controller.create(payload)
        finally:
            session.close()

    external_id = f"pix-forced-race-{uuid4()}"
    payload = PayloadGenerator.create_pix_transaction_payload(external_id=external_id)
    barrier = Barrier(2)
    engine = create_engine(DATABASE_URL)
    session_factory = sessionmaker(bind=engine, autoflush=False)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    create_with_separate_session,
                    deepcopy(payload),
                    barrier,
                    session_factory,
                )
                for _ in range(2)
            ]
            responses = [future.result() for future in futures]
    finally:
        engine.dispose()

    assert responses[0] == responses[1]
    assert CountingPolicy.calls == 1


def test_concurrent_divergent_requests_have_one_winner_and_one_conflict():
    external_id = f"pix-race-different-{uuid4()}"
    first = PayloadGenerator.create_pix_transaction_payload(external_id=external_id)
    second = deepcopy(first)
    second["source_account"]["owner"]["document_number"] = "DIFFERENT-PAYER"
    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_post_together, barrier, first),
            executor.submit(_post_together, barrier, second),
        ]
        results = [future.result() for future in futures]

    assert sorted(status for status, _ in results) == [200, 409]
    winner = next(body for status, body in results if status == 200)
    conflict = next(body for status, body in results if status == 409)
    assert conflict["code"] == "QIT002004"

    get_status, persisted = RequestGenerator.GET_pix_transaction(external_id)
    assert get_status == 200
    assert persisted == winner
