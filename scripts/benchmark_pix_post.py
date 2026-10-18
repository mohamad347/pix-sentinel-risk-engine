"""Benchmark local pequeno do POST com payloads e pagadores sintéticos."""

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen
from uuid import uuid4


def payload(run: str, sequence: int) -> dict:
    return {
        "id": f"bench-{run}-{sequence}",
        "amount": 10_000,
        "transaction_date": "2026-10-18T09:00:00-03:00",
        "source_account": {
            "branch": "0001",
            "account_number": f"bench-account-{run}-{sequence}",
            "opening_date": "2024-01-10T10:00:00-03:00",
            "owner": {"document_number": f"bench-payer-{run}-{sequence}"},
        },
        "destination_account": {
            "participant": "60701190",
            "branch": "3675",
            "account_number": "bench-payee",
            "opening_date": "2025-01-10T09:00:00-03:00",
            "owner": {"document_number": "bench-payee-doc"},
        },
        "source": {
            "device_id": "bench-device",
            "channel": "app",
            "ip": "198.51.100.23",
        },
    }


def post(base_url: str, token: str, body: dict) -> float:
    request = Request(
        f"{base_url.rstrip('/')}/pix/transaction",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"INTERNAL-TOKEN": token, "Content-Type": "application/json"},
    )
    started = time.perf_counter()
    with urlopen(request, timeout=10) as response:
        response.read()
        if response.status != 200:
            raise RuntimeError(f"unexpected HTTP {response.status}")
    return (time.perf_counter() - started) * 1000


def percentile(values: list[float], percentage: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentage * len(ordered)) - 1)
    return round(ordered[index], 2)


def summarize(values: list[float]) -> dict:
    return {
        "requests": len(values),
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "p99_ms": percentile(values, 0.99),
        "max_ms": round(max(values), 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:3300")
    parser.add_argument("--token", default="pix-test-token")
    parser.add_argument("--requests", type=int, default=40)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--warmups", type=int, default=3)
    args = parser.parse_args()
    run = uuid4().hex[:12]
    for sequence in range(args.warmups):
        post(args.base_url, args.token, payload(f"{run}-warmup", sequence))
    bodies = [payload(run, sequence) for sequence in range(args.requests)]
    started = time.perf_counter()
    if args.workers == 1:
        latencies = [post(args.base_url, args.token, body) for body in bodies]
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            latencies = list(
                executor.map(lambda body: post(args.base_url, args.token, body), bodies)
            )
    result = summarize(latencies)
    result["workers"] = args.workers
    result["wall_ms"] = round((time.perf_counter() - started) * 1000, 2)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
