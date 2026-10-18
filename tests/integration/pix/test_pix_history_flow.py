import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, update
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


def _payload(external_id: str, payer_suffix: str, amount: int) -> dict:
    payload = PayloadGenerator.create_pix_transaction_payload(external_id=external_id)
    payload["amount"] = amount
    payload["source_account"]["owner"]["document_number"] = f"DOC-{payer_suffix}"
    payload["source_account"]["branch"] = f"BR-{payer_suffix}"
    payload["source_account"]["account_number"] = f"ACC-{payer_suffix}"
    return payload


def _persisted_evidence(*external_ids: str) -> dict[str, dict]:
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
    return {external_id: evidence for external_id, evidence in rows}


def test_first_and_second_attempt_persist_expected_history_evidence():
    payer = str(uuid4())
    first_id = f"pix-history-first-{uuid4()}"
    second_id = f"pix-history-second-{uuid4()}"

    first_status, first_public = RequestGenerator.POST_pix_transaction(
        _payload(first_id, payer, 101)
    )
    second_status, second_public = RequestGenerator.POST_pix_transaction(
        _payload(second_id, payer, 202)
    )
    evidence = _persisted_evidence(first_id, second_id)

    assert first_status == second_status == 200
    assert "evidence_snapshot" not in first_public
    assert "evidence_snapshot" not in second_public
    assert evidence[first_id]["prior_attempt_count"] == 0
    assert evidence[first_id]["is_first_observed_attempt"] is True
    assert evidence[first_id]["counterparty"]["observation"] == "insufficient_history"
    assert evidence[first_id]["windows"]["5m"] == {
        "attempt_count": 1,
        "amount_cents": 101,
    }
    assert evidence[second_id]["prior_attempt_count"] == 1
    assert evidence[second_id]["is_first_observed_attempt"] is False
    assert evidence[second_id]["counterparty"] == {
        "schema_version": "pix-counterparty-v1",
        "prior_attempt_count": 1,
        "observation": "observed",
        "is_previously_observed": True,
        "is_unobserved_with_payer_history": False,
    }
    assert evidence[second_id]["windows"]["5m"] == {
        "attempt_count": 2,
        "amount_cents": 303,
    }
    assert evidence[second_id]["windows"]["60m"] == {
        "attempt_count": 2,
        "amount_cents": 303,
    }


def test_same_document_on_different_source_accounts_has_separate_history():
    owner = f"DOC-{uuid4()}"
    first = _payload(f"pix-account-a-{uuid4()}", str(uuid4()), 50)
    second = deepcopy(first)
    second["id"] = f"pix-account-b-{uuid4()}"
    first["source_account"]["owner"]["document_number"] = owner
    second["source_account"]["owner"]["document_number"] = owner
    second["source_account"]["account_number"] = "OTHER-ACCOUNT"

    assert RequestGenerator.POST_pix_transaction(first)[0] == 200
    assert RequestGenerator.POST_pix_transaction(second)[0] == 200
    evidence = _persisted_evidence(first["id"], second["id"])

    assert evidence[first["id"]]["prior_attempt_count"] == 0
    assert evidence[second["id"]]["prior_attempt_count"] == 0


def test_replay_does_not_change_evidence_or_inflate_next_attempt():
    payer = str(uuid4())
    first = _payload(f"pix-history-replay-{uuid4()}", payer, 700)
    second = _payload(f"pix-history-after-replay-{uuid4()}", payer, 300)

    first_status, first_response = RequestGenerator.POST_pix_transaction(first)
    original_evidence = _persisted_evidence(first["id"])[first["id"]]
    replay_status, replay_response = RequestGenerator.POST_pix_transaction(first)
    assert RequestGenerator.POST_pix_transaction(second)[0] == 200
    evidence = _persisted_evidence(first["id"], second["id"])

    assert first_status == replay_status == 200
    assert replay_response == first_response
    assert evidence[first["id"]] == original_evidence
    assert original_evidence["counterparty"]["prior_attempt_count"] == 0
    assert evidence[second["id"]]["windows"]["5m"] == {
        "attempt_count": 2,
        "amount_cents": 1000,
    }


def test_invalid_request_is_not_observed_in_later_history():
    payer = str(uuid4())
    invalid = _payload(f"pix-invalid-history-{uuid4()}", payer, 100)
    invalid["amount"] = "100"
    valid = _payload(f"pix-valid-after-invalid-{uuid4()}", payer, 100)

    assert RequestGenerator.POST_pix_transaction(invalid)[0] == 422
    assert RequestGenerator.POST_pix_transaction(valid)[0] == 200
    evidence = _persisted_evidence(valid["id"])[valid["id"]]

    assert evidence["prior_attempt_count"] == 0
    assert evidence["windows"]["5m"]["attempt_count"] == 1


def test_all_persisted_decision_statuses_remain_observed_attempts():
    payer = str(uuid4())
    attempts = [
        _payload(f"pix-status-{index}-{uuid4()}", payer, 100)
        for index in range(4)
    ]
    for payload in attempts[:3]:
        assert RequestGenerator.POST_pix_transaction(payload)[0] == 200

    engine = create_engine(DATABASE_URL)
    try:
        with engine.begin() as connection:
            transaction_ids = select(PixTransaction.id).where(
                PixTransaction.external_id.in_([attempts[1]["id"]])
            )
            connection.execute(
                update(PixAnalysis)
                .where(PixAnalysis.transaction_id.in_(transaction_ids))
                .values(status="two_factor_required")
            )
            transaction_ids = select(PixTransaction.id).where(
                PixTransaction.external_id.in_([attempts[2]["id"]])
            )
            connection.execute(
                update(PixAnalysis)
                .where(PixAnalysis.transaction_id.in_(transaction_ids))
                .values(status="rejected")
            )
    finally:
        engine.dispose()

    assert RequestGenerator.POST_pix_transaction(attempts[3])[0] == 200
    evidence = _persisted_evidence(attempts[3]["id"])[attempts[3]["id"]]

    assert evidence["prior_attempt_count"] == 3
    assert evidence["windows"]["5m"] == {
        "attempt_count": 4,
        "amount_cents": 400,
    }


def test_window_boundaries_are_open_at_start_and_closed_at_current_attempt():
    payer = str(uuid4())
    admitted_at = datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)
    engine = create_engine(DATABASE_URL)
    session_factory = sessionmaker(bind=engine, autoflush=False)
    session = session_factory()
    try:
        offsets_and_amounts = [
            (timedelta(minutes=60), 40),
            (timedelta(minutes=60) - timedelta(microseconds=1), 50),
            (timedelta(minutes=5), 10),
            (timedelta(minutes=5) - timedelta(microseconds=1), 20),
            (timedelta(0), 30),
        ]
        transactions = []
        for index, (offset, amount) in enumerate(offsets_and_amounts):
            transaction = PixTransaction(
                external_id=f"pix-boundary-{uuid4()}",
                request_fingerprint="0" * 64,
                canonicalization_version="pix-official-v1",
                request_payload={},
                amount_cents=amount,
                transaction_at=admitted_at,
                received_at=admitted_at - offset,
                payer_document=f"DOC-{payer}",
                payer_branch="0001",
                payer_account_number="00001",
                payee_participant="participant",
                payee_branch="0001",
                payee_account_number=f"{index}",
                payee_document="payee",
                source_device_id="device",
            )
            session.add(transaction)
            transactions.append(transaction)
        session.flush()

        history = PixTransactionRepository(
            type("Context", (), {"db_session": session})()
        ).get_payer_history(transactions[-1], (5, 60))

        assert history["prior_attempt_count"] == 4
        assert history["windows"][5] == {"attempt_count": 2, "amount_cents": 50}
        assert history["windows"][60] == {"attempt_count": 4, "amount_cents": 110}
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def test_distinct_concurrent_pix_for_same_payer_are_serialized():
    class CountingPolicy(PixBootstrapPolicy):
        calls = 0

        def evaluate(self, transaction_data, evidence_snapshot):
            type(self).calls += 1
            return super().evaluate(transaction_data, evidence_snapshot)

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

    def create(payload, barrier, session_factory):
        session = session_factory()
        try:
            controller = PixTransactionController.__new__(PixTransactionController)
            controller.session = session
            controller.pix_transaction_repository = SynchronizedRepository(session, barrier)
            controller.policy = CountingPolicy("test")
            return controller.create(payload)
        finally:
            session.close()

    payer = str(uuid4())
    first = _payload(f"pix-payer-race-a-{uuid4()}", payer, 125)
    second = _payload(f"pix-payer-race-b-{uuid4()}", payer, 125)
    barrier = Barrier(2)
    engine = create_engine(DATABASE_URL)
    session_factory = sessionmaker(bind=engine, autoflush=False)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(create, payload, barrier, session_factory)
                for payload in (first, second)
            ]
            responses = [future.result() for future in futures]
    finally:
        engine.dispose()

    evidence = _persisted_evidence(first["id"], second["id"])
    ordered = sorted(evidence.values(), key=lambda item: item["prior_attempt_count"])
    assert len(responses) == 2
    assert CountingPolicy.calls == 2
    assert [item["prior_attempt_count"] for item in ordered] == [0, 1]
    assert [item["windows"]["5m"]["attempt_count"] for item in ordered] == [1, 2]
    assert [item["windows"]["5m"]["amount_cents"] for item in ordered] == [125, 250]
    assert [item["counterparty"]["observation"] for item in ordered] == [
        "insufficient_history",
        "observed",
    ]
