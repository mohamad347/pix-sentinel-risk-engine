import copy
import os

import pytest


os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://unit:unit@localhost:5432/unit_test_not_connected",
)

from controllers.pix_transaction_controller import validate_and_extract_pix_data  # noqa: E402
from errors import InvalidPixPayload  # noqa: E402
from utils.pix_canonicalization import (  # noqa: E402
    CANONICALIZATION_VERSION,
    canonicalize_pix_payload,
    pix_request_fingerprint,
)
from tests.unit.test_pix_transaction_controller import valid_pix_payload  # noqa: E402


def test_canonicalization_has_an_explicit_version():
    assert CANONICALIZATION_VERSION == "pix-official-v1"


def test_property_order_does_not_change_fingerprint():
    payload = valid_pix_payload()
    reordered = {key: payload[key] for key in reversed(payload)}
    reordered["source_account"] = {
        key: payload["source_account"][key]
        for key in reversed(payload["source_account"])
    }

    assert pix_request_fingerprint(payload) == pix_request_fingerprint(reordered)


def test_equivalent_offsets_and_fractional_precision_have_the_same_identity():
    first = valid_pix_payload()
    second = copy.deepcopy(first)
    first["transaction_date"] = "2026-10-17T22:15:00.100000-03:00"
    second["transaction_date"] = "2026-10-18T01:15:00.1Z"

    assert pix_request_fingerprint(first) == pix_request_fingerprint(second)


def test_unofficial_extra_fields_do_not_change_fingerprint():
    first = valid_pix_payload()
    second = copy.deepcopy(first)
    second["client_extension"] = {"trace": "different"}
    second["source_account"]["owner"]["display_name"] = "ignored"
    second["source"]["sdk_version"] = "99"

    assert pix_request_fingerprint(first) == pix_request_fingerprint(second)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(amount=payload["amount"] + 1),
        lambda payload: payload["source_account"].update(branch="0002"),
        lambda payload: payload["source_account"]["owner"].update(document_number="001"),
        lambda payload: payload["destination_account"].update(participant="999"),
        lambda payload: payload["source"].update(ip="203.0.113.10"),
        lambda payload: payload["source"]["geolocation"].update(latitude=-22.0),
    ],
)
def test_each_official_field_change_changes_fingerprint(mutate):
    first = valid_pix_payload()
    second = copy.deepcopy(first)
    mutate(second)

    assert pix_request_fingerprint(first) != pix_request_fingerprint(second)


def test_absent_and_null_geolocation_are_equivalent_under_the_current_contract():
    absent = valid_pix_payload(include_geolocation=False)
    explicit_null = copy.deepcopy(absent)
    explicit_null["source"]["geolocation"] = None

    assert pix_request_fingerprint(absent) == pix_request_fingerprint(explicit_null)


def test_identifiers_keep_leading_zeroes_and_case():
    payload = valid_pix_payload()
    canonical = canonicalize_pix_payload(payload)

    assert canonical["source_account"]["branch"] == "0001"
    assert canonical["source_account"]["owner"]["document_number"] == "00ABC12345000199"

    changed_case = copy.deepcopy(payload)
    changed_case["source_account"]["owner"]["document_number"] = "00abc12345000199"
    assert pix_request_fingerprint(payload) != pix_request_fingerprint(changed_case)


def test_timestamp_precision_beyond_python_datetime_is_rejected_instead_of_truncated():
    payload = valid_pix_payload()
    payload["transaction_date"] = "2026-10-17T22:15:00.1234567-03:00"

    with pytest.raises(InvalidPixPayload):
        validate_and_extract_pix_data(payload)
