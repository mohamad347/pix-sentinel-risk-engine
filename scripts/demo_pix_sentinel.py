"""Demonstração reproduzível dos contratos públicos do PIX SENTINEL."""

import argparse
import json
from copy import deepcopy
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def payload(external_id: str, amount: int, payer: str) -> dict:
    return {
        "id": external_id,
        "amount": amount,
        "transaction_date": "2026-10-18T09:00:00-03:00",
        "source_account": {
            "branch": "0001",
            "account_number": payer,
            "opening_date": "2024-01-10T10:00:00-03:00",
            "owner": {"document_number": f"demo-payer-{payer}"},
        },
        "destination_account": {
            "participant": "60701190",
            "branch": "3675",
            "account_number": "00010442",
            "opening_date": "2025-01-10T09:00:00-03:00",
            "owner": {"document_number": "demo-payee"},
        },
        "source": {
            "device_id": "demo-device",
            "channel": "app",
            "ip": "198.51.100.23",
        },
    }


def call(base_url: str, token: str, method: str, path: str, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        method=method,
        headers={"INTERNAL-TOKEN": token, "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except HTTPError as error:
        raw = error.read()
        return error.code, json.loads(raw) if raw else None


def show(label: str, result) -> None:
    status_code, body = result
    print(f"\n{label} — HTTP {status_code}")
    print(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:3000")
    parser.add_argument("--token", default="default_token")
    args = parser.parse_args()

    run = uuid4().hex[:12]

    approved = payload(f"demo-{run}-approved", 10_000, f"{run}-approved")
    approved_result = call(args.base_url, args.token, "POST", "/pix/transaction", approved)
    assert approved_result[0] == 200 and approved_result[1]["status"] == "approved"
    show("1. Pix simples", approved_result)

    high_value = payload(f"demo-{run}-2fa", 500_000, f"{run}-2fa")
    high_value["source_account"]["opening_date"] = "2026-10-13T10:00:00-03:00"
    high_value_result = call(args.base_url, args.token, "POST", "/pix/transaction", high_value)
    assert high_value_result[0] == 200
    assert high_value_result[1]["status"] == "two_factor_required"
    show("2. Pix de R$ 5.000 em conta de origem jovem", high_value_result)

    critical_results = []
    for sequence in range(1, 9):
        critical = payload(
            f"demo-{run}-critical-{sequence}",
            375_000,
            f"{run}-critical",
        )
        critical_results.append(
            call(args.base_url, args.token, "POST", "/pix/transaction", critical)
        )
    rejected_result = critical_results[-1]
    assert rejected_result[0] == 200 and rejected_result[1]["status"] == "rejected"
    show("3. Oitava tentativa da rajada crítica", rejected_result)

    external_id = approved["id"]
    get_result = call(
        args.base_url,
        args.token,
        "GET",
        f"/pix/transaction/{external_id}",
    )
    assert get_result == approved_result
    show("4. GET da decisão persistida", get_result)

    replay_result = call(args.base_url, args.token, "POST", "/pix/transaction", approved)
    assert replay_result == approved_result
    show("5. Replay idêntico", replay_result)

    divergent = deepcopy(approved)
    divergent["amount"] += 1
    conflict_result = call(args.base_url, args.token, "POST", "/pix/transaction", divergent)
    assert conflict_result[0] == 409 and conflict_result[1]["code"] == "QIT002004"
    show("6. Mesmo ID com conteúdo diferente", conflict_result)

    print("\nDemonstração concluída: respostas e invariantes esperados foram confirmados.")


if __name__ == "__main__":
    main()
