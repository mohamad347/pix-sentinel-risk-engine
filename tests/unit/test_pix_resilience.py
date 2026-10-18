import asyncio
import json
from unittest.mock import Mock

from sqlalchemy.exc import OperationalError

import database
from app import create_app
from constants import INTERNAL_TOKEN
from tests.utils import PayloadGenerator


class UnavailableSession:
    def __init__(self) -> None:
        self.rollback_called = False
        self.close_called = False

    def query(self, *args, **kwargs):
        raise OperationalError("SELECT", {}, Exception("database unavailable"))

    def rollback(self) -> None:
        self.rollback_called = True

    def close(self) -> None:
        self.close_called = True


async def _post_asgi(application, path: str, payload: dict, headers: dict):
    request_messages = [
        {
            "type": "http.request",
            "body": json.dumps(payload).encode("utf-8"),
            "more_body": False,
        }
    ]
    response_messages = []

    async def receive():
        if request_messages:
            return request_messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        response_messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (name.lower().encode("ascii"), value.encode("utf-8"))
            for name, value in headers.items()
        ],
        "client": ("test", 1234),
        "server": ("test", 80),
    }
    try:
        await application(scope, receive, send)
    except OperationalError:
        # Starlette's ServerErrorMiddleware emits the configured 500 response
        # and then re-raises so a real ASGI server can log the exception.
        pass
    start = next(message for message in response_messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in response_messages
        if message["type"] == "http.response.body"
    )
    return start["status"], body


def test_database_unavailability_returns_safe_technical_error(monkeypatch):
    session = UnavailableSession()
    monkeypatch.setattr(database, "SessionLocal", Mock(return_value=session))
    payload = PayloadGenerator.create_pix_transaction_payload("pix-database-unavailable")
    secret_document = payload["source_account"]["owner"]["document_number"]

    status, body = asyncio.run(
        _post_asgi(
            create_app(),
            "/pix/transaction",
            payload,
            {
                "content-type": "application/json",
                "INTERNAL-TOKEN": INTERNAL_TOKEN,
            },
        )
    )

    assert status == 500
    assert json.loads(body) == {
        "title": "Internal Error",
        "description": "An internal error has occurred and its being investigated.",
        "translation": "Um erro interno aconteceu e está sendo investigado.",
        "code": "QIT000500",
    }
    assert secret_document not in body.decode("utf-8")
    assert session.rollback_called is True
    assert session.close_called is True
