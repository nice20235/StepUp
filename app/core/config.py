from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Any

class Settings(BaseSettings):
    # Pydantic v2 settings: read from .env and ignore extra keys to prevent crashes from unused env vars
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    DATABASE_URL: str = "postgresql+asyncpg://iskandar:stepup2024@localhost:5432/stepup_db"
    SECRET_KEY: str = "LDakBFywwkqZaLGerOiNVhvuOea-Xr_Oq5HkO5Lpjlg"
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
    # App runtime settings (production deploy alignment)
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    APP_WORKERS: int = 1  # only used if a process manager launches multiple workers

    # Order guards
    ORDER_MAX_QTY_PER_ITEM: int = 50  # hard upper bound per order line to prevent accidental huge quantities

settings = Settings() 