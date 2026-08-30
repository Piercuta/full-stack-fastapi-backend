import secrets
import warnings
from typing import Annotated, Any, Literal
from urllib.parse import quote_plus
import json
import boto3

from pydantic import (
    AnyUrl,
    BeforeValidator,
    EmailStr,
    HttpUrl,
    PostgresDsn,
    computed_field,
    model_validator,
)
from pydantic_core import MultiHostUrl
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Self


def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",")]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file="../.env",
        env_ignore_empty=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = secrets.token_urlsafe(32)
    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    FRONTEND_HOST: str = "http://localhost:5173"
    ENVIRONMENT: Literal["local", "dev", "staging", "prod"] = "local"

    BACKEND_CORS_ORIGINS: Annotated[
        list[AnyUrl] | str, BeforeValidator(parse_cors)
    ] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_cors_origins(self) -> list[str]:
        return [str(origin).rstrip("/") for origin in self.BACKEND_CORS_ORIGINS] + [
            self.FRONTEND_HOST
        ]

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None
    POSTGRES_SERVER: str
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = ""
    # Injected by External Secrets (Secrets Manager ARN for RDS master user).
    AWS_SECRET_ARN: str | None = None
    AWS_REGION: str = "eu-west-1"
    MEDIA_QUEUE_URL: str | None = None
    MEDIA_WORKER_SECRET: str | None = None

    # Optional Redis (ElastiCache). When unset, dashboard hits RDS every request.
    REDIS_URL: str | None = None
    DASHBOARD_CACHE_TTL_SECONDS: int = 30

    # Cognito OIDC (optional). Injected by External Secrets on EKS; set directly for local dev.
    COGNITO_CLIENT_ID: str | None = None
    COGNITO_CLIENT_SECRET: str | None = None
    COGNITO_DOMAIN: str | None = None
    COGNITO_ISSUER: str | None = None

    # File Service Configuration
    FILE_SERVICE_URL: str = "http://file-service-svc.file-service.svc.cluster.local"
    MAX_AVATAR_SIZE: int = 5 * 1024 * 1024  # 5MB
    ALLOWED_AVATAR_TYPES: list[str] = ["image/jpeg", "image/png", "image/gif", "image/webp"]

    def _get_secret(self) -> str:
        if not self.AWS_SECRET_ARN:
            return self.POSTGRES_PASSWORD

        try:
            session = boto3.session.Session()
            client = session.client(
                service_name="secretsmanager",
                region_name=self.AWS_REGION,
            )
            response = client.get_secret_value(SecretId=self.AWS_SECRET_ARN)
            secret = json.loads(response["SecretString"])
            return secret.get("password", self.POSTGRES_PASSWORD)
        except Exception as e:
            print(f"[ERROR] Failed to fetch Secrets Manager secret: {e}")
            return self.POSTGRES_PASSWORD

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> PostgresDsn:
        password = self._get_secret()
        return MultiHostUrl.build(
            scheme="postgresql+psycopg",
            username=self.POSTGRES_USER,
            password=quote_plus(password),
            host=self.POSTGRES_SERVER,
            port=self.POSTGRES_PORT,
            path=self.POSTGRES_DB,
        )

    SMTP_TLS: bool = True
    SMTP_SSL: bool = False
    SMTP_PORT: int = 587
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: EmailStr | None = None

    @model_validator(mode="after")
    def _set_default_emails_from(self) -> Self:
        if not self.EMAILS_FROM_NAME:
            self.EMAILS_FROM_NAME = self.PROJECT_NAME
        return self

    EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.EMAILS_FROM_EMAIL)

    EMAIL_TEST_USER: EmailStr = "test@example.com"
    FIRST_SUPERUSER: EmailStr
    FIRST_SUPERUSER_PASSWORD: str

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value == "changethis":
            message = (
                f'The value of {var_name} is "changethis", '
                "for security, please change it, at least for deployments."
            )
            if self.ENVIRONMENT == "local":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        if not self.AWS_SECRET_ARN:
            self._check_default_secret("POSTGRES_PASSWORD", self.POSTGRES_PASSWORD)
        self._check_default_secret(
            "FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD
        )

        return self


settings = Settings()  # type: ignore
