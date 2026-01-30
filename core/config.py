"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database (async driver for app, sync driver for Alembic)
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/datacurve"
    DATABASE_URL_SYNC: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/datacurve"

    # Redis (Celery broker + result backend)
    REDIS_URL: str = "redis://localhost:6379/0"

    # Blob store root directory
    BLOB_STORE_PATH: str = "/data/blobs"

    # LLM judge model
    JUDGE_MODEL: str = "claude-sonnet-4-20250514"

    model_config = {"env_prefix": "", "case_sensitive": True}


settings = Settings()
