import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import make_url

from models import PixAnalysis, PixTransaction
from tests.utils import PayloadGenerator, RequestGenerator


DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATABASE_NAME = make_url(DATABASE_URL).database if DATABASE_URL else ""
INTEGRATION_ENABLED = os.environ.get("PIX_INTEGRATION_TESTS") == "1"
SAFE_TEST_DATABASE = bool(DATABASE_NAME and "test" in DATABASE_NAME.lower())
V3_ENABLED = os.environ.get(
    "PIX_RISK_POLICY", "mvp-risk-adaptive-v3-r6"
) == "mvp-risk-adaptive-v3-r6"

pytestmark = pytest.mark.skipif(
    not (INTEGRATION_ENABLED and SAFE_TEST_DATABASE and V3_ENABLED),
    reason="requires isolated PostgreSQL/API with the adaptive V3 selector",
)


def _payload(
    external_id: str,
    payer: str,
    amount: int,
    *,
    payee: str,
    device: str,
    ip: str = "198.51.100.23",
    channel: str = "app",
    payer_document: str | None = None,
    payee_document: str | None = None,
) -> dict:
    payload = PayloadGenerator.create_pix_transaction_payload(external_id=external_id)
    payload["amount"] = amount
    payload["source_account"]["owner"]["document_number"] = (
        payer_document or f"DOC-{payer}"
    )
    payload["source_account"]["account_number"] = f"ACCOUNT-{payer}"
    payload["source"]["device_id"] = device
    payload["source"]["ip"] = ip
    payload["source"]["channel"] = channel
    payload["destination_account"]["participant"] = f"PART-{payee}"
    payload["destination_account"]["branch"] = f"BR-{payee}"
    payload["destination_account"]["account_number"] = f"ACC-{payee}"
    payload["destination_account"]["owner"]["document_number"] = (
        payee_document or f"DOC-{payee}"
    )
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


def _age(external_id: str, days: int) -> None:
    engine = create_engine(DATABASE_URL)
    try:
        with engine.begin() as connection:
            connection.execute(
                update(PixTransaction)
                .where(PixTransaction.external_id == external_id)
                .values(received_at=datetime.now(timezone.utc) - timedelta(days=days))
            )
    finally:
        engine.dispose()


def _set_received_at(external_id: str, received_at: datetime) -> None:
    engine = create_engine(DATABASE_URL)
    try:
        with engine.begin() as connection:
            connection.execute(
                update(PixTransaction)
                .where(PixTransaction.external_id == external_id)
                .values(received_at=received_at)
            )
    finally:
        engine.dispose()


def _set_analysis_status(external_id: str, status: str) -> None:
    engine = create_engine(DATABASE_URL)
    try:
        with engine.begin() as connection:
            transaction_id = connection.execute(
                select(PixTransaction.id).where(
                    PixTransaction.external_id == external_id
                )
            ).scalar_one()
            connection.execute(
                update(PixAnalysis)
                .where(PixAnalysis.transaction_id == transaction_id)
                .values(status=status)
            )
    finally:
        engine.dispose()


def test_v3_query_indexes_exist_on_clean_postgresql_schema():
    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as connection:
            names = set(
                connection.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = 'public' AND tablename = 'pix_transactions'"
                    )
                ).scalars()
            )
    finally:
        engine.dispose()

    assert {
        "ix_pix_transactions_payer_received_at",
        "ix_pix_transactions_payee_received_at",
        "ix_pix_transactions_device_received_at",
        "ix_pix_transactions_source_ip_received_at",
    } <= names


def test_v3_postgresql_multisignal_snapshot_and_profile_are_prior_only():
    payer = str(uuid4())
    for sequence in range(5):
        external_id = f"pix-v3-profile-{uuid4()}"
        status_code, response = RequestGenerator.POST_pix_transaction(
            _payload(
                external_id,
                payer,
                10_000,
                payee="known",
                device="known-device",
                payer_document="12345678909",
            )
        )
        assert status_code == 200
        assert response["status"] == "approved"
        _age(external_id, 5 - sequence)

    current_id = f"pix-v3-takeover-{uuid4()}"
    status_code, response = RequestGenerator.POST_pix_transaction(
        _payload(
            current_id,
            payer,
            100_000,
            payee="new",
            device="new-device",
            ip="203.0.113.77",
            channel="web",
            payer_document="12345678909",
        )
    )
    evidence = _stored(current_id).evidence_snapshot

    assert status_code == 200
    assert response["status"] == "two_factor_required"
    assert response["metadata"]["challenge"] == {
        "type": "facial_liveness",
        "level": "enhanced",
        "status": "recommended",
    }
    get_status, persisted = RequestGenerator.GET_pix_transaction(current_id)
    assert get_status == 200
    assert persisted == response
    assert evidence["contextual"]["profile"]["prior_attempt_count"] == 5
    assert evidence["contextual"]["profile"]["median_amount_cents"] == 10_000
    assert evidence["contextual"]["profile"]["p90_amount_cents"] == 10_000
    assert evidence["contextual"]["access"]["device_observation"] == "newly_observed"
    assert evidence["contextual"]["access"]["ip_observation"] == "newly_observed"
    assert evidence["contextual"]["access"]["channel_observation"] == "changed"
    assert evidence["policy"]["families"]["profile"] == 15
    assert evidence["policy"]["families"]["relationship"] == 5
    assert evidence["policy"]["families"]["access"] == 20
    assert _stored(current_id).policy_version == "mvp-risk-adaptive-v3-r6"


def test_v3_recipient_fanin_includes_current_attempt_at_exact_boundary():
    payee = str(uuid4())
    template = None
    for _ in range(7):
        external_id = f"pix-v3-fanin-seed-{uuid4()}"
        template = _payload(
            external_id,
            str(uuid4()),
            10_000,
            payee=payee,
            device=f"device-{uuid4()}",
        )
        assert RequestGenerator.POST_pix_transaction(template)[0] == 200

    current_id = f"pix-v3-fanin-current-{uuid4()}"
    current = deepcopy(template)
    current["id"] = current_id
    current["amount"] = 400_000
    current["source_account"]["owner"]["document_number"] = f"DOC-{uuid4()}"
    current["source_account"]["account_number"] = f"ACCOUNT-{uuid4()}"
    current["destination_account"]["opening_date"] = "2026-10-17T12:00:00-03:00"
    status_code, response = RequestGenerator.POST_pix_transaction(current)
    evidence = _stored(current_id).evidence_snapshot

    assert status_code == 200
    assert response["status"] == "two_factor_required"
    assert response["metadata"]["challenge"] == {
        "type": "push_confirmation",
        "level": "standard",
        "status": "recommended",
    }
    assert evidence["contextual"]["beneficiary"]["distinct_payer_count_60m"] == 8
    assert evidence["contextual"]["beneficiary"]["attempt_count_60m"] == 8
    assert evidence["contextual"]["access"]["ip_distinct_payer_count_60m"] >= 8
    assert evidence["policy"]["families"]["beneficiary_network"] == 20
    assert evidence["policy"]["families"]["access"] == 8
    assert "RECIPIENT_FAN_IN_60M" in evidence["policy"]["triggered_signals"]
    assert "YOUNG_DESTINATION_ACCOUNT" in evidence["policy"]["triggered_signals"]
    assert "IP_SHARED_ACROSS_PAYERS_60M" in evidence["policy"]["triggered_signals"]


def test_v3_impossible_geo_velocity_and_new_access_reject_end_to_end():
    payer = str(uuid4())
    for sequence in range(3):
        baseline_id = f"pix-v3-geo-baseline-{uuid4()}"
        baseline = _payload(
            baseline_id,
            payer,
            10_000,
            payee="known",
            device="known-device",
        )
        baseline["source"]["geolocation"] = {
            "latitude": -23.5505,
            "longitude": -46.6333,
        }
        assert RequestGenerator.POST_pix_transaction(baseline)[1]["status"] == "approved"
        _set_received_at(
            baseline_id,
            datetime.now(timezone.utc) - timedelta(minutes=32 - sequence),
        )

    current_id = f"pix-v3-geo-current-{uuid4()}"
    current = _payload(
        current_id,
        payer,
        10_000,
        payee="new",
        device="new-device",
        ip="203.0.113.77",
    )
    current["source"]["geolocation"] = {
        "latitude": 40.7128,
        "longitude": -74.0060,
    }
    status_code, response = RequestGenerator.POST_pix_transaction(current)
    evidence = _stored(current_id).evidence_snapshot

    assert status_code == 200
    assert response["status"] == "rejected"
    assert response["metadata"]["reason_code"] == "ADAPTIVE_IMPOSSIBLE_TRAVEL"
    assert evidence["contextual"]["temporal"]["geolocation"]["speed_kmh"] > 1_000
    assert "IMPOSSIBLE_GEO_VELOCITY" in evidence["policy"]["triggered_signals"]
    assert "CRITICAL_GEO_ACCESS_MISMATCH" in evidence["policy"][
        "rejection_safeguards"
    ]


def test_v3_rejected_location_does_not_contaminate_next_geo_baseline():
    payer = str(uuid4())
    rejected_id = f"pix-v3-geo-rejected-{uuid4()}"
    rejected = _payload(
        rejected_id,
        payer,
        10_000,
        payee="known",
        device="known-device",
    )
    rejected["source"]["geolocation"] = {
        "latitude": -23.5505,
        "longitude": -46.6333,
    }
    assert RequestGenerator.POST_pix_transaction(rejected)[0] == 200
    _set_analysis_status(rejected_id, "rejected")
    _set_received_at(rejected_id, datetime.now(timezone.utc) - timedelta(minutes=10))

    current_id = f"pix-v3-geo-after-rejected-{uuid4()}"
    current = _payload(
        current_id,
        payer,
        10_000,
        payee="known",
        device="known-device",
    )
    current["source"]["geolocation"] = {
        "latitude": 40.7128,
        "longitude": -74.0060,
    }
    status_code, response = RequestGenerator.POST_pix_transaction(current)
    evidence = _stored(current_id).evidence_snapshot

    assert status_code == 200
    assert response["status"] == "approved"
    assert evidence["contextual"]["temporal"]["geolocation"] == {
        "observation": "insufficient_history"
    }


def test_v3_pf_pj_segmentation_and_deterministic_cold_start_end_to_end():
    pf_id = f"pix-v3-pf-value-{uuid4()}"
    pf_request = _payload(
        pf_id,
        str(uuid4()),
        600_000,
        payee=str(uuid4()),
        device=f"device-{uuid4()}",
        payer_document="12345678909",
        payee_document="98765432100",
    )
    pf_status, pf_response = RequestGenerator.POST_pix_transaction(pf_request)

    pj_id = f"pix-v3-pj-value-{uuid4()}"
    pj_request = _payload(
        pj_id,
        str(uuid4()),
        600_000,
        payee=str(uuid4()),
        device=f"device-{uuid4()}",
        payer_document="00ABC123450001",
        payee_document="98765432100",
    )
    pj_status, pj_response = RequestGenerator.POST_pix_transaction(pj_request)

    cold_id = f"pix-v3-cold-start-{uuid4()}"
    cold_request = _payload(
        cold_id,
        str(uuid4()),
        300_000,
        payee=str(uuid4()),
        device=f"device-{uuid4()}",
        payer_document="11122233344",
        payee_document="55566677788",
    )
    cold_request["source_account"]["opening_date"] = (
        datetime.now(timezone.utc) - timedelta(days=5)
    ).isoformat()
    cold_status, cold_response = RequestGenerator.POST_pix_transaction(cold_request)
    cold_evidence = _stored(cold_id).evidence_snapshot

    assert pf_status == pj_status == cold_status == 200
    assert pf_response["status"] == "two_factor_required"
    assert pj_response["status"] == "approved"
    assert cold_response["status"] == "two_factor_required"
    assert cold_response["metadata"]["reason_code"] == (
        "ADAPTIVE_COLD_START_VERIFICATION"
    )
    assert cold_evidence["contextual"]["account_context"][
        "source_account_age_days"
    ] in {4, 5}
    assert cold_evidence["policy"]["cold_start_verification_safeguard"] is True
    assert "FIRST_OBSERVED_YOUNG_SOURCE_ACCOUNT" in cold_evidence["policy"][
        "triggered_signals"
    ]


def test_v3_nighttime_profile_uses_america_sao_paulo_in_postgresql():
    payer = str(uuid4())
    for sequence in range(5):
        external_id = f"pix-v3-daytime-history-{uuid4()}"
        request = _payload(
            external_id,
            payer,
            10_000,
            payee="known-night-profile",
            device="known-night-device",
        )
        # 19:15 in Brazil is 22:15 UTC. It must not be counted as nighttime
        # by the historical PostgreSQL aggregation.
        request["transaction_date"] = "2026-10-17T19:15:00-03:00"
        assert RequestGenerator.POST_pix_transaction(request)[1]["status"] == "approved"
        _age(external_id, 5 - sequence)

    current_id = f"pix-v3-nighttime-current-{uuid4()}"
    current = _payload(
        current_id,
        payer,
        10_000,
        payee="known-night-profile",
        device="known-night-device",
    )
    current["transaction_date"] = "2026-10-18T02:00:00-03:00"
    status_code, response = RequestGenerator.POST_pix_transaction(current)
    evidence = _stored(current_id).evidence_snapshot

    assert status_code == 200
    assert response["status"] == "approved"
    assert evidence["contextual"]["temporal"]["nighttime_observation"] == "unusual"
    assert "UNUSUAL_NIGHTTIME" in evidence["policy"]["triggered_signals"]


def test_v3_behavioral_outlier_to_new_recipient_requires_2fa_end_to_end():
    payer = str(uuid4())
    for sequence in range(5):
        external_id = f"pix-v3-app-profile-{uuid4()}"
        status_code, response = RequestGenerator.POST_pix_transaction(
            _payload(
                external_id,
                payer,
                10_000,
                payee="habitual-app-recipient",
                device="habitual-app-device",
                payer_document="12345678909",
            )
        )
        assert status_code == 200
        assert response["status"] == "approved"
        _age(external_id, 5 - sequence)

    current_id = f"pix-v3-app-current-{uuid4()}"
    status_code, response = RequestGenerator.POST_pix_transaction(
        _payload(
            current_id,
            payer,
            90_000,
            payee=f"new-app-recipient-{uuid4()}",
            device="habitual-app-device",
            payer_document="12345678909",
        )
    )
    evidence = _stored(current_id).evidence_snapshot
    get_status, persisted = RequestGenerator.GET_pix_transaction(current_id)

    assert status_code == get_status == 200
    assert persisted == response
    assert response["status"] == "two_factor_required"
    assert response["metadata"]["reason_code"] == (
        "ADAPTIVE_CRITICAL_PROFILE_VERIFICATION"
    )
    assert {
        "ABOVE_PROFILE_P90",
        "CRITICAL_MEDIAN_DEVIATION",
        "NEW_COUNTERPARTY",
    } <= set(response["metadata"]["risk_factors"])
    assert evidence["policy"]["risk_score"] >= 20
    assert evidence["policy"]["critical_profile_verification_safeguard"] is True


def test_v3_first_seen_young_recipient_requires_2fa_end_to_end():
    payer = str(uuid4())
    for sequence in range(3):
        external_id = f"pix-v3-mule-history-{uuid4()}"
        status_code, response = RequestGenerator.POST_pix_transaction(
            _payload(
                external_id,
                payer,
                10_000,
                payee="known-mule-control",
                device="known-mule-device",
                payer_document="12345678909",
            )
        )
        assert status_code == 200
        assert response["status"] == "approved"
        _age(external_id, 4 - sequence)

    current_id = f"pix-v3-mule-current-{uuid4()}"
    current = _payload(
        current_id,
        payer,
        300_000,
        payee=f"young-mule-{uuid4()}",
        device="known-mule-device",
        payer_document="12345678909",
        payee_document="98765432100",
    )
    current["destination_account"]["opening_date"] = (
        datetime.now(timezone.utc) - timedelta(days=2)
    ).isoformat()
    status_code, response = RequestGenerator.POST_pix_transaction(current)
    evidence = _stored(current_id).evidence_snapshot

    assert status_code == 200
    assert response["status"] == "two_factor_required"
    assert response["metadata"]["reason_code"] == "ADAPTIVE_EARLY_MULE_VERIFICATION"
    assert {
        "NEW_COUNTERPARTY",
        "RECIPIENT_FIRST_SYSTEM_OBSERVATION",
        "YOUNG_DESTINATION_ACCOUNT",
    } <= set(response["metadata"]["risk_factors"])
    assert evidence["policy"]["risk_score"] >= 15
    assert evidence["policy"]["early_mule_verification_safeguard"] is True


def test_v3_shared_device_is_pf_friction_but_pj_control_remains_approved():
    shared_device = f"shared-device-{uuid4()}"
    payee = f"shared-device-payee-{uuid4()}"

    for document in ("11122233344", "22233344455"):
        status_code, response = RequestGenerator.POST_pix_transaction(
            _payload(
                f"pix-v3-shared-seed-{uuid4()}",
                str(uuid4()),
                10_000,
                payee=payee,
                device=shared_device,
                payer_document=document,
            )
        )
        assert status_code == 200
        assert response["status"] == "approved"

    pf_id = f"pix-v3-shared-pf-{uuid4()}"
    pf_status, pf_response = RequestGenerator.POST_pix_transaction(
        _payload(
            pf_id,
            str(uuid4()),
            10_000,
            payee=payee,
            device=shared_device,
            payer_document="33344455566",
        )
    )
    pf_evidence = _stored(pf_id).evidence_snapshot

    pj_id = f"pix-v3-shared-pj-{uuid4()}"
    pj_status, pj_response = RequestGenerator.POST_pix_transaction(
        _payload(
            pj_id,
            str(uuid4()),
            10_000,
            payee=payee,
            device=shared_device,
            payer_document="00123456000199",
        )
    )
    pj_evidence = _stored(pj_id).evidence_snapshot

    assert pf_status == pj_status == 200
    assert pf_response["status"] == "two_factor_required"
    assert pf_response["metadata"]["reason_code"] == (
        "ADAPTIVE_SHARED_DEVICE_VERIFICATION"
    )
    assert pf_evidence["policy"]["families"]["access"] == 15
    assert pf_evidence["policy"]["shared_device_verification_safeguard"] is True
    assert pj_response["status"] == "approved"
    assert pj_evidence["policy"]["families"]["access"] == 15
    assert pj_evidence["policy"]["shared_device_verification_safeguard"] is False
