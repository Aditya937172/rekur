from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


class AppSettings(BaseModel):
    shopify_store_domain: str = Field(default="your-store.myshopify.com")
    shopify_api_key: Optional[str] = Field(default=None)
    shopify_api_secret: Optional[str] = Field(default=None)
    shopify_admin_access_token: Optional[str] = Field(default=None)
    shopify_api_version: str = Field(default="2026-01")
    dry_run: bool = Field(default=True)
    delete_seeded_data: bool = Field(default=False)
    seed_tag: str = Field(default="seeded_by_retention_app")
    request_timeout_seconds: int = Field(default=45)
    max_retries: int = Field(default=5)
    retry_base_delay_seconds: float = Field(default=1.0)
    data_dir: Path = Field(default=PROJECT_ROOT / "data")
    nango_base_url: str = Field(default="http://localhost:3005")
    nango_secret_key: Optional[str] = Field(default=None)
    nango_public_key: Optional[str] = Field(default=None)
    nango_environment: str = Field(default="dev")
    nango_provider_config_key: str = Field(default="shopify")
    nango_timeout_seconds: int = Field(default=30)
    database_url: str = Field(default="sqlite:///./app.db")
    public_app_url: Optional[str] = Field(default=None)
    tracking_script_url: Optional[str] = Field(default=None)
    groq_api_key: Optional[str] = Field(default=None)
    groq_base_url: str = Field(
        default="https://api.groq.com/openai/v1/chat/completions"
    )
    groq_model: str = Field(default="llama-3.3-70b-versatile")
    groq_timeout_seconds: int = Field(default=30)
    groq_max_retries: int = Field(default=2)
    google_client_id: Optional[str] = Field(default=None)
    google_client_secret: Optional[str] = Field(default=None)
    gmail_sender_email: Optional[str] = Field(default=None)
    gmail_refresh_token: Optional[str] = Field(default=None)
    gmail_redirect_uri: str = Field(
        default="http://127.0.0.1:8010/gmail/oauth/callback"
    )
    gmail_auth_uri: str = Field(default="https://accounts.google.com/o/oauth2/v2/auth")
    gmail_token_uri: str = Field(default="https://oauth2.googleapis.com/token")
    gmail_send_scope: str = Field(default="https://www.googleapis.com/auth/gmail.send")
    gmail_api_base_url: str = Field(default="https://gmail.googleapis.com/gmail/v1")
    gmail_timeout_seconds: int = Field(default=30)
    image_api_key: Optional[str] = Field(default=None)
    image_api_base_url: str = Field(
        default="https://api.evolink.ai/v1/images/generations"
    )
    image_task_base_url: str = Field(default="https://api.evolink.ai/v1/tasks")
    image_model: str = Field(default="gpt-image-2")
    image_size: str = Field(default="auto")
    image_resolution: str = Field(default="1K")
    image_quality: str = Field(default="medium")
    image_timeout_seconds: int = Field(default=90)
    image_poll_interval_seconds: float = Field(default=5.0)
    image_max_wait_seconds: int = Field(default=240)
    image_provider: str = Field(default="evolink")
    vector_cache_threshold: float = Field(default=0.92)
    fashion_clip_provider: str = Field(default="local_hash")
    fashion_clip_embedding_dimensions: int = Field(default=384)
    runpod_api_key: Optional[str] = Field(default=None)
    runpod_seedream_endpoint_id: Optional[str] = Field(default=None)
    runpod_base_url: str = Field(default="https://api.runpod.ai/v2")
    runpod_timeout_seconds: int = Field(default=120)
    runpod_max_wait_seconds: int = Field(default=300)
    runpod_poll_interval_seconds: float = Field(default=5.0)
    sendgrid_api_key: Optional[str] = Field(default=None)
    sendgrid_from_email: Optional[str] = Field(default=None)
    sendgrid_from_name: str = Field(default="Aevnai")
    sendgrid_base_url: str = Field(default="https://api.sendgrid.com/v3")
    sendgrid_timeout_seconds: int = Field(default=30)
    retention_sender_provider: str = Field(default="gmail")
    shopify_webhook_secret: Optional[str] = Field(default=None)
    redis_url: str = Field(default="redis://localhost:6379/0")
    celery_result_backend: str = Field(default="redis://localhost:6379/1")
    gemini_api_key: Optional[str] = Field(default=None)

    @property
    def normalized_store_domain(self) -> str:
        return (
            self.shopify_store_domain.replace("https://", "")
            .replace("http://", "")
            .strip("/")
            .strip()
        )

    @property
    def admin_base_url(self) -> str:
        return (
            f"https://{self.normalized_store_domain}/admin/api/"
            f"{self.shopify_api_version}"
        )

    def validate_for_live_api(self) -> None:
        if self.dry_run:
            return
        if not self.shopify_admin_access_token:
            raise ValueError(
                "SHOPIFY_ADMIN_ACCESS_TOKEN is required when DRY_RUN=false. "
                "The API key and secret are not enough for Admin API calls."
            )
        if (
            not self.normalized_store_domain
            or self.normalized_store_domain == "your-store.myshopify.com"
        ):
            raise ValueError(
                "SHOPIFY_STORE_DOMAIN must be set to your development store domain."
            )


def load_settings(env_file: Optional[Path | str] = None) -> AppSettings:
    load_dotenv(dotenv_path=env_file or PROJECT_ROOT / ".env", override=True)

    return AppSettings(
        shopify_store_domain=os.getenv(
            "SHOPIFY_STORE_DOMAIN", "your-store.myshopify.com"
        ),
        shopify_api_key=os.getenv("SHOPIFY_API_KEY"),
        shopify_api_secret=os.getenv("SHOPIFY_API_SECRET"),
        shopify_admin_access_token=os.getenv("SHOPIFY_ADMIN_ACCESS_TOKEN"),
        shopify_api_version=os.getenv("SHOPIFY_API_VERSION", "2026-01"),
        dry_run=parse_bool(os.getenv("DRY_RUN"), True),
        delete_seeded_data=parse_bool(os.getenv("DELETE_SEEDED_DATA"), False),
        seed_tag=os.getenv("SEED_TAG", "seeded_by_retention_app"),
        request_timeout_seconds=int(os.getenv("SHOPIFY_TIMEOUT_SECONDS", "45")),
        max_retries=int(os.getenv("SHOPIFY_MAX_RETRIES", "5")),
        retry_base_delay_seconds=float(os.getenv("SHOPIFY_RETRY_BASE_DELAY", "1.0")),
        data_dir=Path(os.getenv("SEED_DATA_DIR", str(PROJECT_ROOT / "data"))),
        nango_base_url=os.getenv("NANGO_BASE_URL", "http://localhost:3005"),
        nango_secret_key=os.getenv("NANGO_SECRET_KEY") or None,
        nango_public_key=os.getenv("NANGO_PUBLIC_KEY") or None,
        nango_environment=os.getenv("NANGO_ENVIRONMENT", "dev"),
        nango_provider_config_key=os.getenv("NANGO_PROVIDER_CONFIG_KEY", "shopify"),
        nango_timeout_seconds=int(os.getenv("NANGO_TIMEOUT_SECONDS", "30")),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./app.db"),
        public_app_url=os.getenv("PUBLIC_APP_URL") or None,
        tracking_script_url=os.getenv("TRACKING_SCRIPT_URL") or None,
        groq_api_key=os.getenv("GROQ_API_KEY") or None,
        groq_base_url=os.getenv(
            "GROQ_BASE_URL",
            "https://api.groq.com/openai/v1/chat/completions",
        ),
        groq_model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        groq_timeout_seconds=int(os.getenv("GROQ_TIMEOUT_SECONDS", "30")),
        groq_max_retries=int(os.getenv("GROQ_MAX_RETRIES", "2")),
        google_client_id=os.getenv("GOOGLE_CLIENT_ID")
        or os.getenv("client_id")
        or None,
        google_client_secret=(
            os.getenv("GOOGLE_CLIENT_SECRET") or os.getenv("client_secret") or None
        ),
        gmail_sender_email=os.getenv("GMAIL_SENDER_EMAIL") or None,
        gmail_refresh_token=os.getenv("GMAIL_REFRESH_TOKEN") or None,
        gmail_redirect_uri=os.getenv(
            "GMAIL_REDIRECT_URI",
            "http://127.0.0.1:8010/gmail/oauth/callback",
        ),
        gmail_auth_uri=os.getenv(
            "GMAIL_AUTH_URI",
            "https://accounts.google.com/o/oauth2/v2/auth",
        ),
        gmail_token_uri=os.getenv(
            "GMAIL_TOKEN_URI",
            "https://oauth2.googleapis.com/token",
        ),
        gmail_send_scope=os.getenv(
            "GMAIL_SEND_SCOPE",
            "https://www.googleapis.com/auth/gmail.send",
        ),
        gmail_api_base_url=os.getenv(
            "GMAIL_API_BASE_URL",
            "https://gmail.googleapis.com/gmail/v1",
        ),
        gmail_timeout_seconds=int(os.getenv("GMAIL_TIMEOUT_SECONDS", "30")),
        image_api_key=(
            os.getenv("GPT_IMAGE_KEY")
            or os.getenv("IMAGE_API_KEY")
            or os.getenv("EVO_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or None
        ),
        image_api_base_url=os.getenv(
            "IMAGE_API_BASE_URL",
            "https://api.evolink.ai/v1/images/generations",
        ),
        image_task_base_url=os.getenv(
            "IMAGE_TASK_BASE_URL",
            "https://api.evolink.ai/v1/tasks",
        ),
        image_model=os.getenv("IMAGE_MODEL", "gpt-image-2"),
        image_size=os.getenv("IMAGE_SIZE", "auto"),
        image_resolution=os.getenv("IMAGE_RESOLUTION", "1K"),
        image_quality=os.getenv("IMAGE_QUALITY", "medium"),
        image_timeout_seconds=int(os.getenv("IMAGE_TIMEOUT_SECONDS", "90")),
        image_poll_interval_seconds=float(
            os.getenv("IMAGE_POLL_INTERVAL_SECONDS", "5")
        ),
        image_max_wait_seconds=int(os.getenv("IMAGE_MAX_WAIT_SECONDS", "240")),
        image_provider=os.getenv("IMAGE_PROVIDER", "evolink"),
        vector_cache_threshold=float(os.getenv("VECTOR_CACHE_THRESHOLD", "0.92")),
        fashion_clip_provider=os.getenv("FASHIONCLIP_PROVIDER", "local_hash"),
        fashion_clip_embedding_dimensions=int(
            os.getenv("FASHIONCLIP_EMBEDDING_DIMENSIONS", "384")
        ),
        runpod_api_key=os.getenv("RUNPOD_API_KEY") or None,
        runpod_seedream_endpoint_id=os.getenv("RUNPOD_SEEDREAM_ENDPOINT_ID") or None,
        runpod_base_url=os.getenv("RUNPOD_BASE_URL", "https://api.runpod.ai/v2"),
        runpod_timeout_seconds=int(os.getenv("RUNPOD_TIMEOUT_SECONDS", "120")),
        runpod_max_wait_seconds=int(os.getenv("RUNPOD_MAX_WAIT_SECONDS", "300")),
        runpod_poll_interval_seconds=float(
            os.getenv("RUNPOD_POLL_INTERVAL_SECONDS", "5")
        ),
        sendgrid_api_key=os.getenv("SENDGRID_API_KEY") or None,
        sendgrid_from_email=os.getenv("SENDGRID_FROM_EMAIL") or None,
        sendgrid_from_name=os.getenv("SENDGRID_FROM_NAME", "Aevnai"),
        sendgrid_base_url=os.getenv(
            "SENDGRID_BASE_URL",
            "https://api.sendgrid.com/v3",
        ),
        sendgrid_timeout_seconds=int(os.getenv("SENDGRID_TIMEOUT_SECONDS", "30")),
        retention_sender_provider=os.getenv("RETENTION_SENDER_PROVIDER", "gmail"),
        shopify_webhook_secret=os.getenv("SHOPIFY_WEBHOOK_SECRET") or None,
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        celery_result_backend=os.getenv(
            "CELERY_RESULT_BACKEND", "redis://localhost:6379/1"
        ),
        gemini_api_key=os.getenv("GEMINI_API_KEY")
        or os.getenv("gemini_api_key")
        or None,
    )
