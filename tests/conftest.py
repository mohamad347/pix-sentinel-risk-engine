from pathlib import Path
from os import path, environ

root = Path(__file__).resolve().parents[1]

if not environ.get("APP_ENV") or environ.get("APP_ENV") == "local":
    from dotenv import load_dotenv

    load_dotenv(path.join(str(root), ".env"))

    if environ.get("SERVER_LOCALHOST") is None:
        environ["SERVER_LOCALHOST"] = "0.0.0.0"

# Collection must work in a clean checkout. These values never connect during
# unit tests; PostgreSQL/API integration remains guarded by PIX_INTEGRATION_TESTS.
environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://unit:unit@localhost:5432/unit_test_not_connected",
)
environ.setdefault("INTERNAL_TOKEN", "unit-test-token")
