import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.engine import make_url

from models import PixAnalysis, PixTransaction
from tests.utils import PayloadGenerator, RequestGenerator


DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATABASE_NAME = make_url(DATABASE_URL).database if DATABASE_URL else ""
INTEGRATION_ENABLED = os.environ.get("PIX_INTEGRATION_TESTS") == "1"
SAFE_TEST_DATABASE = bool(DATABASE_NAME and "test" in DATABASE_NAME.lower())
V2_ENABLED = os.environ.get("PIX_RISK_POLICY") == "mvp-risk-contextual-v2-candidate"

pytestmark = pytest.mark.skipif(
    not (INTEGRATION_ENABLED and SAFE_TEST_DATABASE and V2_ENABLED),
    reason="requires isolated PostgreSQL/API with the explicit V2 candidate selector",
)


def _payload(external_id: str, payer: str, amount: int, *, payee: str, device: str) -> dict:
    payload = PayloadGenerator.create_pix_transaction_payload(external_id=external_id)
    payload["amount"] = amount
    payload["source_account"]["owner"]["document_number"] = f"DOC-{payer}"
    payload["source_account"]["account_number"] = f"ACCOUNT-{payer}"
    payload["source"]["device_id"] = device
    payload["destination_account"]["participant"] = f"PART-{payee}"
    payload["destination_account"]["branch"] = f"BR-{payee}"
    payload["destination_account"]["account_number"] = f"ACC-{payee}"
    payload["destination_account"]["owner"]["document_number"] = f"DOC-{payee}"
    return payload


def _stored(external_id: str):
    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as connection:
            return connection.execute(
                select(PixAnalysis.evidence_snapshot, PixAnalysis.policy_version)
                .join(PixTransaction, PixAnalysis.transaction_id == PixTransaction.id)
                .where(PixTransaction.external_id == external_id)
            ).one()
    finally:
        engine.dispose()


def test_v2_http_score_is_internal_and_replay_get_and_conflict_remain_stable():
    external_id = f"pix-v2-cold-{uuid4()}"
    request = _payload(external_id, str(uuid4()), 500_000, payee="A", device="device-A")

    post_status, post = RequestGenerator.POST_pix_transaction(request)
    get_status, persisted = RequestGenerator.GET_pix_transaction(external_id)
    replay_status, replay = RequestGenerator.POST_pix_transaction(request)
    divergent = deepcopy(request)
    divergent["amount"] += 1
    conflict_status, conflict = RequestGenerator.POST_pix_transaction(divergent)
    row = _stored(external_id)

    assert post_status == get_status == replay_status == 200
    assert post == persisted == replay
    assert post["status"] == "approved"
    assert post["metadata"]["policy_version"] == "mvp-risk-contextual-v2-candidate"
    assert "risk_score" not in post["metadata"]
    assert "evidence_snapshot" not in post
    assert conflict_status == 409
    assert conflict["code"] == "QIT002004"
    assert row.policy_version == "mvp-risk-contextual-v2-candidate"
    assert row.evidence_snapshot["policy"]["risk_score"] == 15
    assert row.evidence_snapshot["contextual"]["geolocation"]["observation"] == "insufficient_history"


def test_v2_real_history_uses_profile_counterparty_and_device_context():
    payer = str(uuid4())
    prior_ids = []
    for sequence in range(5):
        external_id = f"pix-v2-profile-prior-{uuid4()}"
        prior_ids.append(external_id)
        request = _payload(external_id, payer, 50_000, payee="known", device="known-device")
        assert RequestGenerator.POST_pix_transaction(request)[0] == 200

    # Controlled fixture: establish the configured 24-hour breadth without waiting.
    engine = create_engine(DATABASE_URL)
    try:
        with engine.begin() as connection:
            for sequence, external_id in enumerate(prior_ids):
                connection.execute(
                    update(PixTransaction)
                    .where(PixTransaction.external_id == external_id)
                    .values(
                        received_at=datetime.now(timezone.utc)
                        - timedelta(hours=48 - sequence * 6)
                    )
                )
    finally:
        engine.dispose()

    current_id = f"pix-v2-profile-current-{uuid4()}"
    current = _payload(
        current_id,
        payer,
        500_000,
        payee="new",
        device="new-device",
    )
    status_code, response = RequestGenerator.POST_pix_transaction(current)
    evidence = _stored(current_id).evidence_snapshot

    assert status_code == 200
    assert response["status"] == "two_factor_required"
    assert evidence["contextual"]["profile"]["median_amount_cents"] == 50_000
    assert evidence["contextual"]["device"]["observation"] == "newly_observed"
    assert evidence["counterparty"]["observation"] == "not_observed"
    assert evidence["policy"]["families"] == {
        "exposure_velocity": 15,
        "profile": 25,
        "counterparty_diversity": 5,
        "device": 10,
        "geolocation": 0,
    }
    assert evidence["policy"]["risk_score"] == 55


def test_v2_critical_burst_with_context_reaches_rejected_in_postgresql():
    payer = str(uuid4())
    responses = []
    for sequence in range(1, 9):
        external_id = f"pix-v2-critical-{uuid4()}"
        request = _payload(
            external_id,
            payer,
            375_000,
            payee=f"payee-{sequence}",
            device="new-device" if sequence == 8 else "known-device",
        )
        status_code, response = RequestGenerator.POST_pix_transaction(request)
        assert status_code == 200
        responses.append((external_id, response))

    last_id, last_response = responses[-1]
    evidence = _stored(last_id).evidence_snapshot

    assert last_response["status"] == "rejected"
    assert last_response["metadata"]["reason_code"] == "CONTEXTUAL_CRITICAL_COMBINATION"
    assert evidence["windows"]["5m"] == {
        "attempt_count": 8,
        "amount_cents": 3_000_000,
    }
    assert evidence["contextual"]["diversity"]["60m"]["distinct_counterparty_count"] == 8
    assert evidence["policy"]["risk_score"] == 75
    assert evidence["policy"]["critical_burst_safeguard"] is True
