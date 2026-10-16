"""
Configuración central usando Pydantic Settings.
Lee desde variables de entorno / archivo .env
"""
import math
from collections import Counter
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ─── App ────────────────────────────────────────────────────────────────
    APP_ENV: str = "development"
    SECRET_KEY: str = "changeme_super_secret_key_32chars_min"
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ─── Database ───────────────────────────────────────────────────────────
    DATABASE_URL: str = (
        "postgresql+asyncpg://sports_user:sports_pass_dev@db:5432/sports_analytics"
    )

    # ─── Redis / Celery ─────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # ─── The Odds API ────────────────────────────────────────────────────────
    ODDS_API_KEY: str = ""
    ODDS_API_KEY_2: str = ""
    ODDS_API_BASE_URL: str = "https://api.the-odds-api.com/v4"

    # ─── NBA API ─────────────────────────────────────────────────────────────
    NBA_API_SLEEP_SECONDS: float = 0.7

    # ─── ML ──────────────────────────────────────────────────────────────────
    MODEL_VERSION: str = "v1"
    MODELS_DIR: str = "/app/ml/models"

    # ─── P3: Freshness Thresholds (hours) ────────────────────────────────────
    FRESHNESS_HOURS_NFL_ODDS: float = 36.0
    FRESHNESS_HOURS_NBA_ODDS: float = 18.0
    FRESHNESS_HOURS_NEWS: float = 24.0

    # ─── Computed ────────────────────────────────────────────────────────────
    @property
    def allowed_origins_list(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",")]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"

    @property
    def debug(self) -> bool:
        return self.APP_ENV.lower() == "development" and not self.is_production

    def validate_security(self):
        if self.is_production:
            if not self.SECRET_KEY or not isinstance(self.SECRET_KEY, str):
                raise ValueError("En producción, SECRET_KEY es obligatoria y debe ser una cadena de texto.")

            clean_key = self.SECRET_KEY.strip()
            if len(clean_key) < 32:
                raise ValueError("En producción, SECRET_KEY debe tener una longitud mínima de 32 caracteres.")

            placeholders = ["changeme", "placeholder", "default", "secret_key", "password", "insecure", "admin123"]
            lower_key = clean_key.lower()
            if any(ph in lower_key for ph in placeholders):
                raise ValueError("En producción, SECRET_KEY no puede contener placeholders conocidos ni palabras clave inseguras.")

            has_upper = any(c.isupper() for c in clean_key)
            has_lower = any(c.islower() for c in clean_key)
            has_digit = any(c.isdigit() for c in clean_key)
            has_symbol = any(not c.isalnum() for c in clean_key)
            category_count = sum([has_upper, has_lower, has_digit, has_symbol])

            counts = Counter(clean_key)
            entropy = -sum((cnt / len(clean_key)) * math.log2(cnt / len(clean_key)) for cnt in counts.values())

            if entropy < 3.0 or category_count < 3:
                raise ValueError(
                    "En producción, SECRET_KEY debe poseer entropía suficiente (>= 3.0 bits) "
                    "y diversidad de caracteres (al menos 3 categorías: mayúsculas, minúsculas, dígitos o símbolos)."
                )


@lru_cache()
def get_settings() -> Settings:
    import os
    s = Settings()
    # Si estamos corriendo dentro de Docker, usar el hostname 'db' en lugar de '127.0.0.1'
    if os.path.exists('/.dockerenv'):
        s.DATABASE_URL = s.DATABASE_URL.replace("127.0.0.1", "db").replace("localhost", "db")
        s.REDIS_URL = s.REDIS_URL.replace("127.0.0.1", "redis").replace("localhost", "redis")
        s.CELERY_BROKER_URL = s.CELERY_BROKER_URL.replace("127.0.0.1", "redis").replace("localhost", "redis")
        s.CELERY_RESULT_BACKEND = s.CELERY_RESULT_BACKEND.replace("127.0.0.1", "redis").replace("localhost", "redis")
    return s


settings = get_settings()
