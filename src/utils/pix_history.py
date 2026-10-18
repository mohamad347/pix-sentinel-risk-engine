import hashlib
import json
from datetime import datetime, timezone


EVIDENCE_SCHEMA_VERSION = "pix-history-v1"
HISTORY_WINDOWS_MINUTES = (5, 60)


def payer_advisory_lock_key(document: str, branch: str, account_number: str) -> int:
    identity = json.dumps(
        [document, branch, account_number],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(identity).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def build_evidence_snapshot(
    admitted_at: datetime,
    prior_attempt_count: int,
    windows: dict[int, dict[str, int]],
) -> dict:
    admitted_at_utc = admitted_at.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "admitted_at": admitted_at_utc,
        "prior_attempt_count": int(prior_attempt_count),
        "is_first_observed_attempt": prior_attempt_count == 0,
        "windows": {
            f"{minutes}m": {
                "attempt_count": int(metrics["attempt_count"]),
                "amount_cents": int(metrics["amount_cents"]),
            }
            for minutes, metrics in windows.items()
        },
    }
