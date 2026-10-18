import os
from datetime import datetime, timezone


os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://unit:unit@localhost:5432/unit_test_not_connected",
)

from repositories import PixTransactionRepository
from utils.pix_history import payer_advisory_lock_key


class FakeResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one(self):
        return self.value


class FakeSession:
    def __init__(self, result=None):
        self.result = result
        self.calls = []

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters))
        return FakeResult(self.result)


def repository_with(session):
    context = type("Context", (), {"db_session": session})()
    return PixTransactionRepository(context)


def test_advisory_lock_uses_stable_signed_payer_key():
    session = FakeSession()
    repository_with(session).acquire_payer_lock("001", "0001", "00009")

    assert session.calls == [
        ("SET LOCAL lock_timeout = '1000ms'", None),
        (
            "SELECT pg_advisory_xact_lock(:lock_key)",
            {"lock_key": payer_advisory_lock_key("001", "0001", "00009")},
        )
    ]


def test_admission_time_uses_clock_timestamp_instead_of_transaction_start_time():
    database_time = datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)
    session = FakeSession(database_time)

    assert repository_with(session).get_database_clock() == database_time
    assert session.calls == [("SELECT clock_timestamp()", None)]
