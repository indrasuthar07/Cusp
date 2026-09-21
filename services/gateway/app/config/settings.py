from enum import Enum
from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

class Environment(str, Enum):
    development = "development"
    test = "test"
    production = "production"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="forbid")

    environment: Environment = Environment.development
    database_url: PostgresDsn
    redis_url: RedisDsn
    session_secret_key: str = Field(min_length=32)

    # provider keys
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

settings = Settings() 