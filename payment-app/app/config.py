from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'flight_data.db'}"
    MOCK_API_URL: str = "https://mockapi.example.com/v1/customers"
    MAX_RETRIES: int = 3

settings = Settings()