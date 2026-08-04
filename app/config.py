import os
from pathlib import Path
from typing import List, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def _running_in_container() -> bool:
    return Path("/.dockerenv").exists() or bool(os.getenv("KUBERNETES_SERVICE_HOST"))


def _resolve_redis_host(host: str) -> str:
    """Map compose/docker hostnames: host OS → 127.0.0.1; in-container keep service DNS."""
    h = (host or "localhost").strip()
    if not _running_in_container() and h in ("redis-stack", "redis"):
        return "127.0.0.1"
    return h or "localhost"

class Settings(BaseSettings):
    # Core Application Settings
    app_name: str = "MahaVistaar AI API"
    environment: str = os.getenv("ENVIRONMENT", "production")
    debug: bool = False
    base_dir: Path = Path(__file__).resolve().parent.parent
    secret_key: str = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
    timezone: str = "Asia/Kolkata"

    # Server Configuration
    host: str = "0.0.0.0"
    port: int = 8000
    api_prefix: str = "/api"
    # Public API base URL for upload image links (e.g. https://api.example.com). Falls back to request host.
    api_public_base_url: Optional[str] = os.getenv("API_PUBLIC_BASE_URL")
    rate_limit_requests_per_minute: int = 1000

    # Security Settings
    allowed_origins: List[str] = os.getenv("ALLOWED_ORIGINS", "*").split(",")
    allowed_credentials: bool = True
    allowed_methods: List[str] = ["*"]
    allowed_headers: List[str] = ["*"]

    # JWT Configuration
    jwt_algorithm: str = "RS256"
    jwt_public_key_path: str = os.getenv("JWT_PUBLIC_KEY_PATH", "jwt_public_key.pem")
    jwt_private_key_path: Optional[str] = os.getenv("JWT_PRIVATE_KEY_PATH")

    # Worker Settings
    uvicorn_workers: int = os.cpu_count() or 1

    # Redis Settings
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_key_prefix: str = "sva-cache-"

    @field_validator("redis_host", mode="before")
    @classmethod
    def _redis_host_resolved(cls, v: object) -> str:
        raw = str(v).strip() if v is not None and str(v).strip() else "localhost"
        return _resolve_redis_host(raw)
    redis_socket_connect_timeout: int = 10
    redis_socket_timeout: int = 10
    redis_max_connections: int = 20  # Reduced from 100 to 20 per worker
    redis_retry_on_timeout: bool = True

    # Cache Configuration
    default_cache_ttl: int = 60 * 60 * 24  # 24 hours
    suggestions_cache_ttl: int = 60 * 30    # 30 minutes
    pest_upload_cache_ttl: int = 60 * 60 * 24  # 24 hours

    # MinIO (S3-compatible) storage for pest images. Configure lifecycle
    # retention on this bucket; the API never deletes objects itself.
    minio_endpoint_url: Optional[str] = os.getenv("MINIO_ENDPOINT_URL")
    minio_access_key: Optional[str] = os.getenv("MINIO_ACCESS_KEY")
    minio_secret_key: Optional[str] = os.getenv("MINIO_SECRET_KEY")
    minio_region: str = os.getenv("MINIO_REGION", "us-east-1")
    minio_pest_upload_bucket: str = os.getenv(
        "MINIO_PEST_UPLOAD_BUCKET", "pest-detection-uploads"
    )

    # Pest & disease detection (Mahapocra / TIH)
    # URLs are read directly from environment variables in `agents/tools/pest_detection.py`.
    pest_detection_http_timeout: float = 60.0

    # Logging Configuration
    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # External Service URLs
    telemetry_api_url: str = "https://vistaar.kenpath.ai/observability-service/action/data/v3/telemetry"
    bhashini_api_url: str = ""
    ollama_endpoint_url: Optional[str] = None
    marqo_endpoint_url: Optional[str] = None
    inference_endpoint_url: Optional[str] = None

    # External Service API Keys
    openai_api_key: Optional[str] = None
    meity_api_key_value: Optional[str] = None
    logfire_token: Optional[str] = None
    bhashini_api_key: str = ""
    langfuse_public_key: Optional[str] = None
    langfuse_secret_key: Optional[str] = None
    langfuse_host: Optional[str] = None
    # Langfuse UI "Env" badge + OTel resource; defaults to app ENVIRONMENT so it matches env: tags in chat traces.
    langfuse_tracing_environment: str = (
        os.getenv("LANGFUSE_TRACING_ENVIRONMENT") or os.getenv("ENVIRONMENT", "development")
    )
    eleven_labs_api_key: str = ""
    inference_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    mapbox_api_token: Optional[str] = None

    # AWS Configuration
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    aws_region: Optional[str] = None
    aws_s3_bucket: Optional[str] = None

    # LLM Configuration
    llm_provider: Optional[str] = None
    llm_model_name: Optional[str] = None
    marqo_index_name: Optional[str] = None

    class Config:
        env_file = ".env"
        extra = 'ignore'  # Ignore extra fields from .env

settings = Settings() 
