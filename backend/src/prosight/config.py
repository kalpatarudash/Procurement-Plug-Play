from datetime import date
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime settings. Override any field with a PROSIGHT_* env var or backend/.env."""

    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_prefix="PROSIGHT_", extra="ignore")

    database_url: str = f"sqlite:///{BACKEND_DIR / 'prosight.db'}"
    # "Today" for the demo dataset, so the numbers line up with the UI's FY 2025 story.
    as_of: date = date(2025, 6, 16)
    fiscal_year_start_month: int = 7
    currency: str = "USD"
    # Stock older than this counts as aging.
    aging_days: int = 180
    # Comma-separated sites allowed to call the API from a browser when the frontend is hosted
    # elsewhere, e.g. "https://procurement-plug-play.example.workers.dev". Empty = same-site only.
    cors_origins: str = ""

    # --- Connector credentials (leave blank to keep a connector unconfigured) ---
    sap_base_url: str | None = None
    sap_api_key: str | None = None
    salesforce_instance_url: str | None = None
    salesforce_access_token: str | None = None
    netsuite_account_id: str | None = None
    netsuite_access_token: str | None = None


settings = Settings()
