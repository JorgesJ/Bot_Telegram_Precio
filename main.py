"""Punto de entrada del bot rastreador de precios.

Arranca:
- la base de datos SQLite,
- el scraper y el tracker,
- la aplicación de Telegram con sus handlers,
- el chequeo diario programado.

Uso:
    python main.py
(antes copia .env.example a .env y rellena TELEGRAM_BOT_TOKEN)
"""
from __future__ import annotations

import logging

from telegram.ext import Application

from bot.config import configure_logging, load_settings
from bot.database import Database
from bot.handlers import BotHandlers
from bot.scheduler import setup_daily_job
from bot.scraper import Scraper
from bot.tracker import Tracker

logger = logging.getLogger(__name__)


def build_application() -> Application:
    settings = load_settings()
    configure_logging(settings.log_level)

    db = Database(settings.database_path)
    scraper = Scraper(timeout=settings.http_timeout, delay=settings.scrape_delay_seconds)
    tracker = Tracker(db, scraper)

    app = Application.builder().token(settings.bot_token).build()

    handlers = BotHandlers(db, tracker, settings)
    handlers.register(app)

    setup_daily_job(app, db, tracker, settings)

    # Guarda referencias por si se necesitan en shutdown.
    app.bot_data["db"] = db
    app.bot_data["settings"] = settings

    logger.info(
        "Bot listo. Autenticación: %s.",
        "activada" if settings.auth_enabled else "DESACTIVADA (acceso abierto)",
    )
    return app


def main() -> None:
    app = build_application()
    # run_polling gestiona el ciclo de vida completo (inicio, polling, parada).
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
