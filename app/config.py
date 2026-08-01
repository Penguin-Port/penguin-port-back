import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./smartpass.db")
    jwt_secret: str = os.getenv(
        "JWT_SECRET", "local-mvp-secret-change-me-use-at-least-32-bytes"
    )
    demo_key: str = os.getenv("DEMO_KEY", "demo-key")
    demo_otp_code: str = os.getenv("DEMO_OTP_CODE", "123456")
    expire_interval_seconds: int = int(os.getenv("EXPIRE_INTERVAL_SECONDS", "60"))
    access_token_minutes: int = int(os.getenv("ACCESS_TOKEN_MINUTES", "60"))

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url.startswith("postgres://"):
            return self.database_url.replace("postgres://", "postgresql+psycopg://", 1)
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return self.database_url


settings = Settings()
