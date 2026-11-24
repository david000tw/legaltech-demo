from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "pilot", "prod"] = "dev"
    database_url: str = "postgresql+psycopg://app_user:apppass@127.0.0.1:55432/legaltech"
    database_admin_url: str = "postgresql+psycopg://postgres:devpass@127.0.0.1:55432/legaltech"
    # 只能讀、新增判決與段落的帳號：律師貼司法院網址加入判決時用（網頁伺服器不持有最高權限帳號）
    database_corpus_url: str = "postgresql+psycopg://corpus_writer:corpuspass@127.0.0.1:55432/legaltech"
    storage_dir: Path = Path("storage")
    ai_provider: Literal["claude_code_local", "bedrock"] = "claude_code_local"
    claude_cmd: list[str] = ["claude"]
    ai_model: str = "opus"
    ocr_model: str = "opus"  # 辨識固定用 Opus（比較測試：Sonnet 會偷改原文）
    bind_host: str = "127.0.0.1"
    port: int = 8000
    session_secret: str = "dev-only-secret-change-me"
    # 允許送出寫入請求的來源（Origin 標頭）；其他網站或本機其他埠一律拒絕
    allowed_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173",
                                  "http://127.0.0.1:8000", "http://localhost:8000"]
