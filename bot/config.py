"""Configuración cargada desde variables de entorno (.env)."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _parse_int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _parse_ids(raw: str | None) -> set[int]:
    if not raw:
        return set()
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                ids.add(int(part))
            except ValueError:
                logging.warning("ID de usuario inválido ignorado: %r", part)
    return ids


def _parse_domains(raw: str | None, default: set[str]) -> set[str]:
    if raw is None or raw.strip() == "":
        return set(default)
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


@dataclass
class Settings:
    """Configuración global de la aplicación."""

    bot_token: str
    allowed_user_ids: set[int] = field(default_factory=set)
    database_path: str = "data/price_tracker.db"
    daily_check_hour: int = 9
    daily_check_minute: int = 0
    timezone: str = "Europe/Madrid"
    scrape_delay_seconds: float = 3.0
    http_timeout: float = 20.0
    log_level: str = "INFO"
    scraperapi_key: str = ""
    scraperapi_domains: set[str] = field(
        default_factory=lambda: {"mediamarkt"}
    )
    scraperapi_monthly_budget: int = 1000
    scraperapi_credits_per_request: int = 10
    scraperapi_warn_percent: int = 80

    @property
    def auth_enabled(self) -> bool:
        """True si hay una lista blanca de usuarios configurada."""
        return len(self.allowed_user_ids) > 0

    @property
    def api_enabled(self) -> bool:
        """True si hay API de scraping (ScraperAPI) configurada."""
        return bool(self.scraperapi_key)

    def is_authorized(self, user_id: int) -> bool:
        return not self.auth_enabled or user_id in self.allowed_user_ids


def load_settings() -> Settings:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Falta TELEGRAM_BOT_TOKEN. Copia .env.example a .env y rellénalo."
        )

    db_path = os.getenv("DATABASE_PATH", "data/price_tracker.db").strip()
    # Asegura que el directorio de la BD existe.
    Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        bot_token=token,
        allowed_user_ids=_parse_ids(os.getenv("ALLOWED_USER_IDS")),
        database_path=db_path,
        daily_check_hour=_parse_int(os.getenv("DAILY_CHECK_HOUR"), 9),
        daily_check_minute=_parse_int(os.getenv("DAILY_CHECK_MINUTE"), 0),
        timezone=os.getenv("TIMEZONE", "Europe/Madrid").strip() or "Europe/Madrid",
        scrape_delay_seconds=float(_parse_int(os.getenv("SCRAPE_DELAY_SECONDS"), 3)),
        http_timeout=float(_parse_int(os.getenv("HTTP_TIMEOUT"), 20)),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        scraperapi_key=os.getenv("SCRAPERAPI_KEY", "").strip(),
        scraperapi_domains=_parse_domains(
            os.getenv("SCRAPERAPI_DOMAINS"), {"mediamarkt"}
        ),
        scraperapi_monthly_budget=_parse_int(os.getenv("SCRAPERAPI_MONTHLY_BUDGET"), 1000),
        scraperapi_credits_per_request=_parse_int(
            os.getenv("SCRAPERAPI_CREDITS_PER_REQUEST"), 10
        ),
        scraperapi_warn_percent=_parse_int(os.getenv("SCRAPERAPI_WARN_PERCENT"), 80),
    )


def configure_logging(level: str) -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=getattr(logging, level, logging.INFO),
    )
    # python-telegram-bot y httpx son muy verbosos en DEBUG.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
