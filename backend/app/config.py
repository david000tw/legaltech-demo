from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "pilot", "prod"] = "dev"
    database_url: str = "postgresql+psycopg://app_user:apppass@127.0.0.1:55432/legaltech"
    database_admin_url: str = "postgresql+psycopg://postgres:devpass@127.0.0.1:55432/legaltech"
    storage_dir: Path = Path("storage")
    ai_provider: Literal["claude_code_local", "bedrock"] = "claude_code_local"
    claude_cmd: list[str] = ["claude"]
    ai_model: str = "opus"
    bind_host: str = "127.0.0.1"
    port: int = 8000
    session_secret: str = "dev-only-secret-change-me"
