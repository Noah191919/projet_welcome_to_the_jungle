from __future__ import annotations
from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'flight_data.db'}"
    MOCK_API_URL: str = "https://mockapi.example.com/v1/customers"
    EXTERNAL_TIMEOUT_SECONDS: float = 5.0
    EXTERNAL_MAX_RETRIES: int = 3
    ENV: str = "development"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()