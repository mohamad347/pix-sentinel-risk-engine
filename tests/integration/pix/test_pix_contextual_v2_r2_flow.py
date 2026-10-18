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
R2_ENABLED = os.environ.get("PIX_RISK_POLICY") == "mvp-risk-contextual-v2-candidate-r2"

pytestmark = pytest.mark.skipif(
    not (INTEGRATION_ENABLED and SAFE_TEST_DATABASE and R2_ENABLED),
    reason="requires isolated PostgreSQL/API with the explicit V2 R2 selector",
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
                select(
                    PixAnalysis.evidence_snapshot,
                    PixAnalysis.policy_version,
                    PixAnalysis.status,
                )
                .join(PixTransaction, PixAnalysis.transaction_id == PixTransaction.id)
                .where(PixTransaction.external_id == external_id)
            ).one()
    finally:
        engine.dispose()


def _age_transactions(external_ids: list[str]) -> None:
    engine = create_engine(DATABASE_URL)
    now = datetime.now(timezone.utc)
    try:
        with engine.begin() as connection:
            for sequence, external_id in enumerate(external_ids):
                connection.execute(
                    update(PixTransaction)
                    .where(PixTransaction.external_id == external_id)
                    .values(received_at=now - timedelta(hours=72 - sequence * 12))
                )
    finally:
        engine.dispose()


def test_r2_c04_http_replay_get_conflict_and_version_are_stable():
    external_id = f"pix-r2-c04-{uuid4()}"
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
    assert post["status"] == "two_factor_required"
    assert post["metadata"]["policy_version"] == "mvp-risk-contextual-v2-candidate-r2"
    assert conflict_status == 409
    assert conflict["code"] == "QIT002004"
    assert row.policy_version == "mvp-risk-contextual-v2-candidate-r2"
    assert row.evidence_snapshot["policy"]["risk_score"] == 25


def test_r2_c05b_familiar_critical_burst_rejects_in_postgresql():
    payer = str(uuid4())
    final_id = None
    final_response = None
    for _ in range(8):
        final_id = f"pix-r2-c05b-{uuid4()}"
        request = _payload(final_id, payer, 375_000, payee="known", device="known-device")
        status_code, final_response = RequestGenerator.POST_pix_transaction(request)
        assert status_code == 200

    evidence = _stored(final_id).evidence_snapshot

    assert final_response["status"] == "rejected"
    assert evidence["counterparty"]["observation"] == "observed"
    assert evidence["contextual"]["device"]["observation"] == "observed"
    assert evidence["policy"]["families"]["exposure_velocity"] == 60
    assert evidence["policy"]["risk_score"] == 60


def test_r2_profile_excludes_non_approved_attempts_but_velocity_keeps_them():
    payer = str(uuid4())
    approved_ids = []
    non_approved_ids = []
    for _ in range(5):
        external_id = f"pix-r2-profile-approved-{uuid4()}"
        approved_ids.append(external_id)
        response = RequestGenerator.POST_pix_transaction(
            _payload(external_id, payer, 10_000, payee="known", device="known-device")
        )
        assert response[1]["status"] == "approved"
        _age_transactions(approved_ids)

    for sequence in range(6):
        external_id = f"pix-r2-profile-risky-{uuid4()}"
        non_approved_ids.append(external_id)
        response = RequestGenerator.POST_pix_transaction(
            _payload(
                external_id,
                payer,
                5_000_000,
                payee=f"risky-{sequence}",
                device="risky-device",
            )
        )
        assert response[1]["status"] != "approved"

    _age_transactions(non_approved_ids)

    current_id = f"pix-r2-profile-current-{uuid4()}"
    status_code, response = RequestGenerator.POST_pix_transaction(
        _payload(current_id, payer, 400_000, payee="new", device="new-device")
    )
    evidence = _stored(current_id).evidence_snapshot

    assert status_code == 200
    assert response["status"] == "two_factor_required"
    assert evidence["prior_attempt_count"] == 11
    assert evidence["windows"]["60m"]["attempt_count"] == 1
    assert evidence["contextual"]["profile"] == {
        "observation": "observed",
        "prior_attempt_count": 5,
        "history_span_seconds": 172_800,
        "median_amount_cents": 10_000,
    }
    assert evidence["policy"]["families"] == {
        "exposure_velocity": 0,
        "profile": 15,
        "counterparty_diversity": 5,
        "device": 10,
        "geolocation": 0,
    }
