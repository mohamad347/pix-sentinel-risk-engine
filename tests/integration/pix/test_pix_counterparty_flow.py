import os
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url

from models import PixAnalysis, PixTransaction
from tests.utils import PayloadGenerator, RequestGenerator


DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATABASE_NAME = make_url(DATABASE_URL).database if DATABASE_URL else ""
INTEGRATION_ENABLED = os.environ.get("PIX_INTEGRATION_TESTS") == "1"
SAFE_TEST_DATABASE = bool(DATABASE_NAME and "test" in DATABASE_NAME.lower())

pytestmark = pytest.mark.skipif(
    not (INTEGRATION_ENABLED and SAFE_TEST_DATABASE),
    reason="requires PIX_INTEGRATION_TESTS=1 and a DATABASE_URL whose database name contains 'test'",
)


def _payload(external_id: str, payer: str, payee: str) -> dict:
    payload = PayloadGenerator.create_pix_transaction_payload(external_id=external_id)
    payload["source_account"]["owner"]["document_number"] = f"DOC-{payer}"
    payload["source_account"]["branch"] = "0001"
    payload["source_account"]["account_number"] = f"ACCOUNT-{payer}"
    payload["destination_account"]["participant"] = f"PARTICIPANT-{payee}"
    payload["destination_account"]["branch"] = f"BRANCH-{payee}"
    payload["destination_account"]["account_number"] = f"ACCOUNT-{payee}"
    payload["destination_account"]["owner"]["document_number"] = f"DOC-{payee}"
    return payload


def _evidence(*external_ids: str) -> dict[str, dict]:
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
    return dict(rows)


def test_counterparty_context_distinguishes_insufficient_observed_and_not_observed():
    payer = str(uuid4())
    first = _payload(f"pix-counterparty-first-{uuid4()}", payer, "A")
    repeated = _payload(f"pix-counterparty-repeat-{uuid4()}", payer, "A")
    different = _payload(f"pix-counterparty-different-{uuid4()}", payer, "B")

    for payload in (first, repeated, different):
        assert RequestGenerator.POST_pix_transaction(payload)[0] == 200

    evidence = _evidence(first["id"], repeated["id"], different["id"])
    assert evidence[first["id"]]["counterparty"]["observation"] == "insufficient_history"
    assert evidence[repeated["id"]]["counterparty"]["observation"] == "observed"
    assert evidence[repeated["id"]]["counterparty"]["prior_attempt_count"] == 1
    assert evidence[different["id"]]["counterparty"]["observation"] == "not_observed"
    assert evidence[different["id"]]["counterparty"]["prior_attempt_count"] == 0


def test_same_counterparty_is_scoped_to_each_payer_and_replay_is_immutable():
    payee = str(uuid4())
    first = _payload(f"pix-counterparty-payer-a-{uuid4()}", "A-" + str(uuid4()), payee)
    other_payer = _payload(f"pix-counterparty-payer-b-{uuid4()}", "B-" + str(uuid4()), payee)

    first_status, first_response = RequestGenerator.POST_pix_transaction(first)
    before_replay = deepcopy(_evidence(first["id"])[first["id"]])
    replay_status, replay_response = RequestGenerator.POST_pix_transaction(first)
    assert RequestGenerator.POST_pix_transaction(other_payer)[0] == 200
    evidence = _evidence(first["id"], other_payer["id"])

    assert first_status == replay_status == 200
    assert replay_response == first_response
    assert evidence[first["id"]] == before_replay
    assert evidence[other_payer["id"]]["counterparty"]["observation"] == "insufficient_history"
