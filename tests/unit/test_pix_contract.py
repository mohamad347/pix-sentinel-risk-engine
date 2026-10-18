import os
import json

import pytest
from sqlalchemy.dialects import postgresql
from fastapi import Request
from fastapi.exceptions import RequestValidationError


os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://unit:unit@localhost:5432/unit_test_not_connected",
)
os.environ.setdefault("INTERNAL_TOKEN", "unit-test-token")

from errors import InvalidPixPayload  # noqa: E402
from models import PixAnalysis, PixTransaction  # noqa: E402
from resources import PixTransactionResource  # noqa: E402
from app import create_app  # noqa: E402


def test_pix_schema_errors_use_422_exception_without_echoing_input():
    sensitive_value = "sensitive-document-value"
    payload = {
        "id": "pix-invalid",
        "amount": 100,
        "transaction_date": "2026-10-17T22:15:00-03:00",
        "source_account": {
            "branch": "0001",
            "account_number": "123",
            "opening_date": "2025-03-02T10:00:00-03:00",
            "owner": {"document_number": sensitive_value},
        },
    }

    with pytest.raises(InvalidPixPayload) as captured:
        PixTransactionResource().on_post(payload=payload)

    assert captured.value.http_status == 422
    assert sensitive_value not in captured.value.description
    assert sensitive_value not in captured.value.translation


def test_fastapi_body_errors_are_422_only_for_pix_endpoint():
    application = create_app()
    handler = application.exception_handlers[RequestValidationError]
    exception = RequestValidationError(
        [{"loc": ("body", 0), "msg": "invalid JSON", "type": "json_invalid"}]
    )

    pix_request = Request({"type": "http", "method": "POST", "path": "/pix/transaction", "headers": []})
    generic_request = Request({"type": "http", "method": "POST", "path": "/other-endpoint", "headers": []})
    pix_response = handler(pix_request, exception)
    generic_response = handler(generic_request, exception)

    assert pix_response.status_code == 422
    assert json.loads(pix_response.body)["code"] == "QIT002001"
    assert generic_response.status_code == 400
    assert json.loads(generic_response.body)["code"] == "QIT000001"


def test_orm_maps_public_metadata_column_to_safe_python_attribute():
    metadata_column = PixAnalysis.__table__.columns["metadata"]

    assert metadata_column.name == "metadata"
    assert PixAnalysis.decision_metadata.property.columns == [metadata_column]
    assert "metadata" not in PixAnalysis.__mapper__.attrs


def test_orm_contains_required_uniqueness_constraints():
    pix_constraints = {tuple(constraint.columns.keys()) for constraint in PixTransaction.__table__.constraints}
    analysis_constraints = {tuple(constraint.columns.keys()) for constraint in PixAnalysis.__table__.constraints}

    assert ("external_id",) in pix_constraints
    assert ("transaction_id",) in analysis_constraints


def test_models_compile_for_postgresql():
    transaction_ddl = str(
        __import__("sqlalchemy").schema.CreateTable(PixTransaction.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    analysis_ddl = str(
        __import__("sqlalchemy").schema.CreateTable(PixAnalysis.__table__).compile(
            dialect=postgresql.dialect()
        )
    )

    assert "JSONB" in transaction_ddl
    assert "UUID" in analysis_ddl
    assert "FOREIGN KEY(transaction_id) REFERENCES pix_transactions (id)" in analysis_ddl
