import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    API_KEY: str = ""
    BASE_URL: str = "https://api.deepseek.com/v1"
    SPEED_MODEL: str = "deepseek-chat"
    HIGH_MODEL: str = "deepseek-chat"

    # ── Sandbox ──────────────────────────────────────────────────
    SANDBOX_MODE: str = "off"  # off / best_effort / required

    # ── Auto-Compaction ─────────────────────────────────────────
    COMPACTION_ENABLED: bool = True
    COMPACTION_RESERVE_TOKENS: int = 24000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()



