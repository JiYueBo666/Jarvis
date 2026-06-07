from pydantic_settings import BaseSettings, SettingsConfigDict
import os


class Settings(BaseSettings):
    API_KEY: str
    BASE_URL: str
    SPEED_MODEL: str
    HIGH_MODEL: str

    # ── Sandbox ──────────────────────────────────────────────────
    SANDBOX_MODE: str = "best_effort"  # off / best_effort / required

    model_config = SettingsConfigDict(
        env_file=".env",  # 从 .env 读取
        env_file_encoding="utf-8",
        case_sensitive=False,  # 不区分大小写
        extra="ignore",  # 忽略多余变量
    )


settings = Settings()



