import os
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError


os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://unit:unit@localhost:5432/unit_test_not_connected",
)
os.environ.setdefault("INTERNAL_TOKEN", "unit-test-token")

from controllers.pix_transaction_controller import (  # noqa: E402
    PixTransactionController,
    validate_and_extract_pix_data,
)
from errors import InvalidPixPayload  # noqa: E402


def valid_pix_payload(include_geolocation=True):
    payload = {
        "id": "pix-unit-001",
        "amount": 150000,
        "transaction_date": "2026-10-17T22:15:00-03:00",
        "source_account": {
            "branch": "0001",
            "account_number": "00088213",
            "opening_date": "2025-03-02T10:00:00-03:00",
            "owner": {"document_number": "00ABC12345000199"},
        },
        "destination_account": {
            "participant": "060701190",
            "branch": "03675",
            "account_number": "00010442",
            "opening_date": "2026-10-10T09:00:00-03:00",
            "owner": {"document_number": "000.000.001-91"},
        },
        "source": {
            "device_id": "dvc-unit",
            "channel": "app",
            "ip": "198.51.100.23",
        },
    }
    if include_geolocation:
        payload["source"]["geolocation"] = {"latitude": -23.56, "longitude": -46.65}
    return payload


class FakeSession:
    def __init__(self, fail_commit=False):
        self.fail_commit = fail_commit
        self.events = []

    def commit(self):
        self.events.append("commit")
        if self.fail_commit:
            raise RuntimeError("commit failed")

    def rollback(self):
        self.events.append("rollback")


class FakeRepository:
    def __init__(self, fail_analysis=False, fail_external_id_insert=False):
        self.fail_analysis = fail_analysis
        self.fail_external_id_insert = fail_external_id_insert
        self.events = []
        self.transaction = None
        self.analysis = None
        self.existing_analysis = None

    def create_transaction(self, **data):
        self.events.append("create_transaction")
        if self.fail_external_id_insert:
            original = type(
                "UniqueViolation",
                (),
                {"diag": type("Diag", (), {"constraint_name": "pix_transactions_external_id_key"})()},
            )()
            raise IntegrityError("insert", {}, original)
        self.transaction = type("Transaction", (), {"id": 42, **data})()
        return self.transaction

    def create_analysis(self, transaction, **data):
        self.events.append("create_analysis")
        if self.fail_analysis:
            raise RuntimeError("analysis insert failed")
        self.analysis = type("Analysis", (), {"analysis_key": uuid4(), **data})()
        return self.analysis

    def get_analysis_by_external_id(self, _external_id):
        return self.existing_analysis

    def get_transaction_by_external_id(self, _external_id):
        if self.existing_analysis is None:
            return None
        return type(
            "Transaction",
            (),
            {
                "request_fingerprint": getattr(self.existing_analysis, "request_fingerprint", "legacy"),
                "request_payload": getattr(self.existing_analysis, "request_payload", valid_pix_payload()),
                "analysis": self.existing_analysis,
            },
        )()


def controller_with(session, repository, policy):
    controller = PixTransactionController.__new__(PixTransactionController)
    controller.session = session
    controller.pix_transaction_repository = repository
    controller.policy = policy
    return controller


class FakeBootstrapPolicy:
    def __init__(self):
        self.calls = 0

    def evaluate(self, _payload):
        self.calls += 1
        return {
            "status": "approved",
            "metadata": {
                "policy_version": "bootstrap-dev-v1",
                "reason_code": "BOOTSTRAP_NO_RISK_ASSESSMENT",
                "reason_description": "No antifraud risk assessment was performed.",
            },
            "evidence_snapshot": {},
            "policy_version": "bootstrap-dev-v1",
        }


bootstrap_policy = FakeBootstrapPolicy()


def test_create_persists_transaction_and_analysis_before_commit():
    session = FakeSession()
    repository = FakeRepository()
    controller = controller_with(session, repository, bootstrap_policy)

    response = controller.create(valid_pix_payload())

    assert repository.events == ["create_transaction", "create_analysis"]
    assert session.events == ["commit"]
    assert set(response) == {"key", "status", "metadata"}
    assert response["status"] == "approved"
    assert response["metadata"]["policy_version"] == "bootstrap-dev-v1"
    assert repository.transaction.payer_branch == "0001"
    assert repository.transaction.payer_account_number == "00088213"
    assert repository.transaction.payer_document == "00ABC12345000199"
    assert isinstance(repository.transaction.transaction_at, datetime)
    assert repository.transaction.transaction_at.utcoffset() is not None


def test_identical_replay_returns_exact_snapshot_without_writes_or_policy_call():
    payload = valid_pix_payload()
    persisted_key = uuid4()
    repository = FakeRepository()
    repository.existing_analysis = type(
        "Analysis",
        (),
        {
            "analysis_key": persisted_key,
            "status": "approved",
            "decision_metadata": {"policy_version": "original-policy"},
            "request_payload": payload,
        },
    )()
    policy = FakeBootstrapPolicy()
    session = FakeSession()
    controller = controller_with(session, repository, policy)

    response = controller.create(payload)

    assert response == {
        "key": str(persisted_key),
        "status": "approved",
        "metadata": {"policy_version": "original-policy"},
    }
    assert repository.events == []
    assert session.events == []
    assert policy.calls == 0


def test_same_external_id_with_different_official_content_raises_409():
    from errors import PixTransactionConflict

    original = valid_pix_payload()
    replay = valid_pix_payload()
    replay["amount"] += 1
    repository = FakeRepository()
    repository.existing_analysis = type(
        "Analysis",
        (),
        {
            "analysis_key": uuid4(),
            "status": "approved",
            "decision_metadata": {},
            "request_payload": original,
        },
    )()
    policy = FakeBootstrapPolicy()
    controller = controller_with(FakeSession(), repository, policy)

    with pytest.raises(PixTransactionConflict) as captured:
        controller.create(replay)

    assert captured.value.http_status == 409
    assert captured.value.code == "QIT002004"
    assert policy.calls == 0


def test_external_id_race_rolls_back_and_returns_winner_without_recalculating():
    class RaceRepository(FakeRepository):
        def __init__(self):
            super().__init__(fail_external_id_insert=True)
            self.lookups = 0

        def get_transaction_by_external_id(self, external_id):
            self.lookups += 1
            if self.lookups == 1:
                return None
            return super().get_transaction_by_external_id(external_id)

    payload = valid_pix_payload()
    winner_key = uuid4()
    repository = RaceRepository()
    repository.existing_analysis = type(
        "Analysis",
        (),
        {
            "analysis_key": winner_key,
            "status": "approved",
            "decision_metadata": {"policy_version": "winner"},
            "request_payload": payload,
        },
    )()
    policy = FakeBootstrapPolicy()
    session = FakeSession()
    controller = controller_with(session, repository, policy)

    response = controller.create(payload)

    assert response["key"] == str(winner_key)
    assert session.events == ["rollback"]
    assert policy.calls == 0


def test_non_external_id_integrity_error_is_not_treated_as_replay():
    class OtherConstraintRepository(FakeRepository):
        def create_transaction(self, **data):
            original = type(
                "CheckViolation",
                (),
                {"diag": type("Diag", (), {"constraint_name": "ck_pix_transactions_positive_amount"})()},
            )()
            raise IntegrityError("insert", {}, original)

    session = FakeSession()
    controller = controller_with(session, OtherConstraintRepository(), FakeBootstrapPolicy())

    with pytest.raises(IntegrityError):
        controller.create(valid_pix_payload())

    assert session.events == ["rollback"]


@pytest.mark.parametrize("amount", [1500.0, True, "1500", 0, -1])
def test_rejects_invalid_amount_without_writing(amount):
    payload = valid_pix_payload()
    payload["amount"] = amount
    repository = FakeRepository()
    controller = controller_with(FakeSession(), repository, bootstrap_policy)

    with pytest.raises(InvalidPixPayload):
        controller.create(payload)

    assert repository.events == []


@pytest.mark.parametrize(
    "field_path,value",
    [
        (("transaction_date",), "2026-10-17T22:15:00"),
        (("transaction_date",), "not-a-date"),
        (("source_account", "opening_date"), "2025-03-02T10:00:00"),
        (("destination_account", "opening_date"), "2026-02-30T09:00:00-03:00"),
    ],
)
def test_rejects_invalid_or_timezone_naive_dates(field_path, value):
    payload = valid_pix_payload()
    target = payload
    for part in field_path[:-1]:
        target = target[part]
    target[field_path[-1]] = value

    with pytest.raises(InvalidPixPayload):
        validate_and_extract_pix_data(payload)


def test_geolocation_is_optional_and_original_payload_is_preserved():
    payload = valid_pix_payload(include_geolocation=False)
    payload["client_extension"] = {"trace": "synthetic"}
    session = FakeSession()
    repository = FakeRepository()
    controller = controller_with(session, repository, bootstrap_policy)

    controller.create(payload)

    assert repository.transaction.request_payload == payload
    assert "geolocation" not in repository.transaction.request_payload["source"]


@pytest.mark.parametrize("external_id", [" pix-unit-001", "pix-unit-001 ", "pix/unit-001"])
def test_rejects_external_id_that_cannot_be_used_by_get_route(external_id):
    payload = valid_pix_payload()
    payload["id"] = external_id
    repository = FakeRepository()
    controller = controller_with(FakeSession(), repository, bootstrap_policy)

    with pytest.raises(InvalidPixPayload):
        controller.create(payload)

    assert repository.events == []


def test_rolls_back_when_analysis_creation_fails():
    session = FakeSession()
    repository = FakeRepository(fail_analysis=True)
    controller = controller_with(session, repository, bootstrap_policy)

    with pytest.raises(RuntimeError, match="analysis insert failed"):
        controller.create(valid_pix_payload())

    assert session.events == ["rollback"]


def test_does_not_return_success_when_commit_fails():
    session = FakeSession(fail_commit=True)
    controller = controller_with(session, FakeRepository(), bootstrap_policy)

    with pytest.raises(RuntimeError, match="commit failed"):
        controller.create(valid_pix_payload())

    assert session.events == ["commit", "rollback"]


def test_get_returns_the_persisted_snapshot_without_running_policy():
    repository = FakeRepository()
    repository.existing_analysis = type(
        "Analysis",
        (),
        {
            "analysis_key": uuid4(),
            "status": "approved",
            "decision_metadata": {"policy_version": "persisted-version"},
        },
    )()
    controller = controller_with(FakeSession(), repository, bootstrap_policy)

    response = controller.get_by_external_id("pix-unit-001")

    assert response["metadata"] == {"policy_version": "persisted-version"}


def test_get_unknown_pix_raises_not_found():
    from errors import PixAnalysisNotFound

    controller = controller_with(FakeSession(), FakeRepository(), bootstrap_policy)

    with pytest.raises(PixAnalysisNotFound) as captured:
        controller.get_by_external_id("pix-missing")

    assert captured.value.http_status == 404
