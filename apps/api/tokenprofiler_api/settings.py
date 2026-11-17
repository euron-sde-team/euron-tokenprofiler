"""API settings from ``TP_*`` environment variables."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEV_PEPPER = "dev-pepper-change-me"


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TP_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8380
    data_dir: Path = Path("~/.tokenprofiler")
    api_key_pepper: str = DEV_PEPPER
    bootstrap_api_key: str | None = Field(
        default=None, description="An all-scopes key installed on every start (automated setups)"
    )
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3300"])
    log_level: str = "INFO"

    # Ingestion limits
    max_batch_events: int = Field(default=1000, ge=1, le=10_000)
    max_request_bytes: int = Field(default=8 * 1024 * 1024, ge=1024)
    ingest_rate_limit_rpm: int = Field(
        default=0, ge=0, description="Requests per minute per key on /v1/events and the proxy; 0 = off"
    )
    retention_days: int = Field(default=400, ge=1, description="Events older than this are deleted daily")
    cache_ttl_s: float = Field(
        default=15.0,
        ge=0,
        description="Seconds to cache the heavy dashboard queries (overview, recommendations, anomalies); 0 disables",
    )
    derive_prompt_ids: bool = Field(
        default=True,
        description="Group calls by prompt shape when the client gives no prompt_id (needs prompt_text)",
    )

    # Pricing
    pricing_file: str | None = None

    # The profiling proxy
    upstream_base_url: str | None = Field(
        default=None,
        description="OpenAI-compatible base URL the proxy forwards to, e.g. https://api.openai.com/v1",
    )
    upstream_api_key: str | None = None
    upstream_timeout_s: float = Field(default=120.0, gt=0)

    # Alerts
    alert_interval_s: float = Field(
        default=60.0, gt=0, description="How often budgets and anomalies are evaluated"
    )
    allow_private_webhooks: bool = Field(
        default=False,
        description="Permit webhook targets on private addresses (for an internal alert receiver)",
    )

    # AWS (optional): daily reports archived to S3, secrets from Secrets Manager
    aws_region: str | None = None
    aws_endpoint_url: str | None = None
    reports_bucket: str | None = None
    secrets_arn: str | None = Field(
        default=None, description="Secrets Manager secret (JSON) with api_key_pepper and bootstrap_api_key"
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip().startswith("["):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    @property
    def data_path(self) -> Path:
        return self.data_dir.expanduser()


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()


def apply_aws_secrets(settings: ApiSettings, client: Any | None = None) -> ApiSettings:
    """Overlays the pepper and bootstrap key from AWS Secrets Manager (``TP_SECRETS_ARN``), so neither sits in the
    container environment or a task definition. Without an ARN the settings are returned unchanged."""
    if not settings.secrets_arn:
        return settings
    if client is None:
        import boto3

        client = boto3.client(
            "secretsmanager",
            region_name=settings.aws_region or "us-east-1",
            endpoint_url=settings.aws_endpoint_url,
        )
    data = json.loads(client.get_secret_value(SecretId=settings.secrets_arn)["SecretString"])
    return settings.model_copy(
        update={k: data[k] for k in ("api_key_pepper", "bootstrap_api_key") if data.get(k)}
    )
