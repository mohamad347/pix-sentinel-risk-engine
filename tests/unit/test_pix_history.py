from datetime import datetime, timezone

from utils.pix_history import (
    EVIDENCE_SCHEMA_VERSION,
    HISTORY_WINDOWS_MINUTES,
    build_evidence_snapshot,
    payer_advisory_lock_key,
)


def test_payer_lock_key_is_stable_and_uses_the_complete_account_identity():
    identity = ("00123456789", "0001", "00000123")
    key = payer_advisory_lock_key(*identity)

    assert -(2**63) <= key < 2**63
    assert key == payer_advisory_lock_key(*identity)
    assert key != payer_advisory_lock_key("00123456789", "0002", "00000123")
    assert key != payer_advisory_lock_key("00123456789", "0001", "00000124")
    assert key != payer_advisory_lock_key("10123456789", "0001", "00000123")


def test_lock_key_encoding_does_not_confuse_component_boundaries():
    assert payer_advisory_lock_key("a", "bc", "d") != payer_advisory_lock_key("ab", "c", "d")


def test_builds_small_versioned_evidence_without_payer_identifiers():
    admitted_at = datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)
    evidence = build_evidence_snapshot(
        admitted_at=admitted_at,
        prior_attempt_count=1,
        prior_counterparty_attempt_count=1,
        windows={
            5: {"attempt_count": 2, "amount_cents": 2501},
            60: {"attempt_count": 3, "amount_cents": 5001},
        },
    )

    assert EVIDENCE_SCHEMA_VERSION == "pix-history-v1"
    assert HISTORY_WINDOWS_MINUTES == (5, 60)
    assert evidence == {
        "schema_version": "pix-history-v1",
        "admitted_at": "2026-10-18T15:00:00.000000Z",
        "prior_attempt_count": 1,
        "is_first_observed_attempt": False,
        "counterparty": {
            "schema_version": "pix-counterparty-v1",
            "prior_attempt_count": 1,
            "observation": "observed",
            "is_previously_observed": True,
            "is_unobserved_with_payer_history": False,
        },
        "windows": {
            "5m": {"attempt_count": 2, "amount_cents": 2501},
            "60m": {"attempt_count": 3, "amount_cents": 5001},
        },
    }
    assert "document" not in str(evidence).lower()


def test_first_observed_attempt_is_derived_from_explicit_prior_count():
    evidence = build_evidence_snapshot(
        admitted_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
        prior_attempt_count=0,
        prior_counterparty_attempt_count=0,
        windows={5: {"attempt_count": 1, "amount_cents": 99}},
    )

    assert evidence["is_first_observed_attempt"] is True
    assert evidence["counterparty"] == {
        "schema_version": "pix-counterparty-v1",
        "prior_attempt_count": 0,
        "observation": "insufficient_history",
        "is_previously_observed": None,
        "is_unobserved_with_payer_history": None,
    }


def test_marks_unobserved_counterparty_only_when_payer_has_prior_history():
    evidence = build_evidence_snapshot(
        admitted_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
        prior_attempt_count=2,
        prior_counterparty_attempt_count=0,
        windows={5: {"attempt_count": 1, "amount_cents": 99}},
    )

    assert evidence["counterparty"] == {
        "schema_version": "pix-counterparty-v1",
        "prior_attempt_count": 0,
        "observation": "not_observed",
        "is_previously_observed": False,
        "is_unobserved_with_payer_history": True,
    }
