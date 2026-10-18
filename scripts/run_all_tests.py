"""Run every policy-specific test against the policy it targets.

This module is the command of the Docker test runner. It starts the API inside
the runner, switches policy only between pytest processes, and resets the
isolated PostgreSQL database before each policy suite.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen
from xml.etree import ElementTree

from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parents[1]
API_URL = "http://127.0.0.1:3000/health_check"

SUITES = (
    (
        "R6",
        "mvp-risk-adaptive-v3-r6",
        (
            "tests/unit",
            "tests/integration/test_healthcheck.py",
            "tests/integration/pix/test_pix_transaction_flow.py",
            "tests/integration/pix/test_pix_history_flow.py",
            "tests/integration/pix/test_pix_resilience_flow.py",
            "tests/integration/pix/test_pix_counterparty_flow.py",
            "tests/integration/pix/test_pix_adaptive_v3_flow.py",
            "tests/integration/pix/test_pix_risk_policy_flow.py::test_replay_preserves_legacy_bootstrap_decision_after_active_policy_changes",
        ),
    ),
    (
        "V1",
        "mvp-risk-experimental-v1",
        (
            "tests/integration/pix/test_pix_risk_policy_flow.py::test_http_post_and_get_produce_approved_decision",
            "tests/integration/pix/test_pix_risk_policy_flow.py::test_http_post_and_get_produce_two_factor_recommendation_without_executing_it",
            "tests/integration/pix/test_pix_risk_policy_flow.py::test_http_post_and_get_produce_rejected_decision_at_combined_boundary",
        ),
    ),
    (
        "V2",
        "mvp-risk-contextual-v2-candidate",
        ("tests/integration/pix/test_pix_contextual_v2_flow.py",),
    ),
    (
        "V2 R2",
        "mvp-risk-contextual-v2-candidate-r2",
        ("tests/integration/pix/test_pix_contextual_v2_r2_flow.py",),
    ),
)


def reset_database(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "TRUNCATE TABLE pix_analyses, pix_transactions "
                    "RESTART IDENTITY CASCADE"
                )
            )
    finally:
        engine.dispose()


def wait_for_api(process: subprocess.Popen, timeout_seconds: int = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"API exited during startup with code {process.returncode}")
        try:
            with urlopen(API_URL, timeout=1) as response:
                if response.status == 204:
                    return
        except (URLError, TimeoutError):
            time.sleep(0.2)
    raise RuntimeError("API did not become healthy within 30 seconds")


def stop_api(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def read_pytest_report(report_path: Path) -> tuple[int, int]:
    root = ElementTree.parse(report_path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
    executed = sum(int(suite.attrib.get("tests", 0)) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", 0)) for suite in suites)
    return executed, skipped


def run_suite(
    name: str, policy: str, targets: tuple[str, ...], base_env: dict
) -> int:
    print(f"\n=== {name} | {policy} ===", flush=True)
    reset_database(base_env["DATABASE_URL"])

    policy_env = base_env.copy()
    policy_env["PIX_RISK_POLICY"] = policy
    api_process = subprocess.Popen(
        (
            sys.executable,
            "-m",
            "uvicorn",
            "app:app",
            "--app-dir",
            str(ROOT / "src"),
            "--host",
            "127.0.0.1",
            "--port",
            "3000",
            "--no-server-header",
        ),
        cwd=ROOT,
        env=policy_env,
    )
    try:
        wait_for_api(api_process)
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as report:
            report_path = Path(report.name)
        try:
            result = subprocess.run(
                (
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    f"--junitxml={report_path}",
                    *targets,
                ),
                cwd=ROOT,
                env=policy_env,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Test suite {name} failed with code {result.returncode}"
                )
            executed, skipped = read_pytest_report(report_path)
            if skipped:
                raise RuntimeError(f"Test suite {name} skipped {skipped} test(s)")
            return executed
        finally:
            report_path.unlink(missing_ok=True)
    finally:
        stop_api(api_process)


def main() -> None:
    base_env = os.environ.copy()
    base_env.update(
        {
            "APP_ENV": "test",
            "PIX_INTEGRATION_TESTS": "1",
            "SERVER_LOCALHOST": "127.0.0.1",
            "API_PORT": "3000",
        }
    )
    executed = sum(run_suite(*suite, base_env) for suite in SUITES)
    print(
        f"\n{executed} passed, 0 skipped across all policy suites.",
        flush=True,
    )


if __name__ == "__main__":
    main()
