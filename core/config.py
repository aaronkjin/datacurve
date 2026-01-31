"""App config via env vars"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/datacurve"  # Async driver for app
    DATABASE_URL_SYNC: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/datacurve"  # Sync driver for Alembic
    
    REDIS_URL: str = "redis://localhost:6379/0"

    # Blob store root dir
    BLOB_STORE_PATH: str = "/data/blobs"

    # QA test runner
    TEST_TIMEOUT_SECONDS: int = 120
    TEST_MEMORY_LIMIT: str = "512m"
    TEST_BASE_IMAGE: str = "python:3.11-slim"
    TEST_COMMAND: str = "pytest"

    # LLM judge model
    JUDGE_MODEL: str = "gpt-5.2"
    OPENAI_API_KEY: str = ""

    model_config = {"env_prefix": "", "case_sensitive": True, "env_file": ".env"}


settings = Settings()
