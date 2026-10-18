from errors import BootstrapPolicyUnavailable


BOOTSTRAP_POLICY_VERSION = "bootstrap-dev-v1"
ALLOWED_BOOTSTRAP_ENVIRONMENTS = {"LOCAL", "TEST", "DEVELOPMENT"}


class PixBootstrapPolicy:
    """Temporary policy used only to exercise the persisted API flow."""

    def __init__(self, app_env: str) -> None:
        self.app_env = app_env

    def evaluate(self, _transaction_data: dict) -> dict:
        if self.app_env.upper() not in ALLOWED_BOOTSTRAP_ENVIRONMENTS:
            raise BootstrapPolicyUnavailable()

        return {
            "status": "approved",
            "metadata": {
                "schema_version": "1.0",
                "policy_version": BOOTSTRAP_POLICY_VERSION,
                "reason_code": "BOOTSTRAP_NO_RISK_ASSESSMENT",
                "reason_description": "No antifraud risk assessment was performed.",
            },
            "evidence_snapshot": {},
            "policy_version": BOOTSTRAP_POLICY_VERSION,
        }
