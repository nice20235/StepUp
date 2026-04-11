from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr


class Settings(BaseSettings):
    """Application settings.

    All sensitive values (DB credentials, secret keys, external API passwords)
    must be provided via environment variables or .env file. The defaults below
    are **development placeholders only** and are safe to publish to GitHub.
    """

    # Pydantic v2 settings: read from .env and ignore extra keys to prevent crashes from unused env vars
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database connection string. Override in production via env DATABASE_URL.
    # Example: postgresql+asyncpg://user:strong_password@db-host:5432/stepup_db
    # NOTE: This is a non-secret placeholder. Override via env DATABASE_URL.
    DATABASE_URL: str = "postgresql+asyncpg://dev_user:dev_password@localhost:5432/stepup_db"

    # JWT secret key. MUST be overridden in any non-local environment.
    # JWT secret key. Use a strong value from env SECRET_KEY in real deployments.
    SECRET_KEY: SecretStr = SecretStr("CHANGE_ME_DEVELOPMENT_SECRET_KEY")
    ALGORITHM: str = "HS256"
    # CALLBACK_BASIC_AUTH_USERNAME and CALLBACK_BASIC_AUTH_PASSWORD removed (payment system)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 1  # Reduced from 7 to 1 day  
    SESSION_MAX_DAYS: int = 1  # Absolute maximum session lifetime (sliding disabled past this). 0 disables hard cap.
    SESSION_MAX_HOURS: int = 8  # Alternative to DAYS. If >0, hours takes precedence. Reduced from 12 to 8.
    ALLOWED_ORIGINS: str = (
        "http://localhost:3000,"
        "http://127.0.0.1:3000,"
        "http://localhost:5173,"
        "https://oyoqkiyim.duckdns.org,"
        "https://optomoyoqkiyim.uz,"
        "https://www.optomoyoqkiyim.uz,"
        "https://step-up-7.vercel.app"
    )
    # Allow any subdomain on production domain by default (www, ru., uz., etc.)
    ALLOWED_ORIGIN_REGEX: str | None = r"^https?://(.+\.)?optomoyoqkiyim\.uz$"
    LOGIN_RATE_LIMIT: int = 5  # попыток
    LOGIN_RATE_WINDOW_SEC: int = 300  # окно в секундах (5 минут)
    COOKIE_SAMESITE: str = "lax"  # options: 'lax', 'strict', 'none'
    COOKIE_SECURE: bool = True
    COOKIE_DOMAIN: str | None = None
    # Global rate limiting
    RATE_LIMIT_REQUESTS: int = 100  # запросов за окно
    RATE_LIMIT_WINDOW_SEC: int = 60  # окно, секунд
    RATE_LIMIT_EXCLUDE_PATHS: str = "/docs,/redoc,/openapi.json,/favicon.ico,/static"
    TRUST_PROXY: bool = False  # если True, брать IP из X-Forwarded-For
    DEBUG: bool = True  # для разработки
    PHONE_ALLOWED_PREFIXES: str = "+,+998"  # допустимые префиксы телефонов
    # Payment configuration: Stripe settings are read from environment variables
    # (STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_SUCCESS_URL, STRIPE_CANCEL_URL)

    # Acquiring integration settings (safe defaults; override in .env for real creds)
    ACQUIRING_BASE_URL: str = "https://acquiring.example.com"  # Base URL of acquirer REST API
    # Legacy /rpc Basic Auth credentials (non-secret placeholders).
    # Real values must be provided in env: ACQUIRING_RPC_BASIC_USERNAME / ACQUIRING_RPC_BASIC_PASSWORD
    ACQUIRING_RPC_BASIC_USERNAME: str = "dev_rpc_user"
    ACQUIRING_RPC_BASIC_PASSWORD: SecretStr = SecretStr("dev_rpc_password")

    # JSON-RPC auth and external ekayring API (development placeholders)
    # Real credentials for /api/rpc must be set via env RPC_USERNAME / RPC_PASSWORD.
    RPC_USERNAME: str = "dev_merchant_api_user"
    RPC_PASSWORD: SecretStr = SecretStr("dev_merchant_api_password")
    EKAYRING_BASE_URL: str = "https://ekayring-api.example.com"
    # App runtime settings (production deploy alignment)
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    APP_WORKERS: int = 1  # only used if a process manager launches multiple workers

    # Order guards
    # Upper bound per order line to prevent accidental huge quantities.
    # Increased to 1000 so that wholesale-like orders from cart (e.g. 700 pcs)
    # are allowed by default. Can be overridden via env if needed.
    ORDER_MAX_QTY_PER_ITEM: int = 1000

settings = Settings()
