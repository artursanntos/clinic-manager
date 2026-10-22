"""Settings from environment variables (and an optional .env file). Secrets never live in code or YAML."""

from __future__ import annotations

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---------------------------------------------------------------- files
    clinic_config_path: str = "config/clinic.yaml"
    templates_path: str = "config/templates.yaml"
    skills_dir: str = "skills"

    # ---------------------------------------------------------------- LLM (vendor-agnostic, see ADR 0007)
    agent_model: str | None = None
    """PydanticAI model string, e.g. ``openrouter:<model>``, ``anthropic:<model>``, ``google:<model>``,
    ``openai:<model>``, or ``test`` for the scripted model. Keys come from each provider's standard env var."""
    agent_fallback_models: str = ""
    """Comma-separated list of fallback model strings."""
    agent_output_mode: Literal["tool", "native", "prompted"] = "tool"
    agent_temperature: float = 0.0
    agent_max_tokens: int = 800
    agent_retries: int = 2
    agent_history_messages: int = 12

    # ---------------------------------------------------------------- provider selection (see ADR 0001)
    calendar_provider: Literal["fake", "google", "microsoft", "caldav"] = "fake"
    storage_backend: Literal["memory", "firestore"] = "memory"
    messaging_provider: Literal["fake", "whatsapp_cloud"] = "fake"

    # ---------------------------------------------------------------- WhatsApp Cloud API
    whatsapp_verify_token: SecretStr | None = None
    whatsapp_app_secret: SecretStr | None = None
    whatsapp_access_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_api_version: str = "v23.0"

    # ---------------------------------------------------------------- jobs (Cloud Scheduler)
    jobs_token: SecretStr | None = None

    # ---------------------------------------------------------------- Google Calendar (service account / ADC)
    google_calendar_id: str | None = None
    google_credentials_file: str | None = None
    """Service-account key file. Leave empty on Cloud Run to use the runtime service account (ADC)."""

    # ---------------------------------------------------------------- Microsoft 365 / Outlook (Graph)
    ms_auth_mode: Literal["client_credentials", "refresh_token"] = "refresh_token"
    ms_tenant_id: str = "consumers"
    ms_client_id: str | None = None
    ms_client_secret: SecretStr | None = None
    ms_refresh_token: SecretStr | None = None
    ms_user_id: str | None = None
    """Mailbox (UPN or id) for client_credentials mode. Ignored in refresh_token mode (uses /me)."""
    ms_calendar_id: str | None = None
    """Leave empty for the default calendar."""

    # ---------------------------------------------------------------- CalDAV (iCloud, Nextcloud, Fastmail…)
    caldav_url: str | None = None
    caldav_username: str | None = None
    caldav_password: SecretStr | None = None
    caldav_calendar_name: str | None = None

    # ---------------------------------------------------------------- Firestore
    firestore_project: str | None = None
    firestore_database: str = "(default)"
    firestore_collection_prefix: str = ""

    log_level: str = "INFO"
