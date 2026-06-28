"""Chequeo diario automático de precios.

Usa el JobQueue de python-telegram-bot (APScheduler por debajo) para ejecutar
una comprobación cada 24h a la hora configurada. Tras comprobar, avisa al
propietario de cada producto SOLO si hay algo reseñable (cambio de precio,
mínimo histórico o precio objetivo alcanzado).
"""
from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes

from . import formatting
from .config import Settings
from .database import Database
from .tracker import ProductReport, Tracker

logger = logging.getLogger(__name__)


class DailyChecker:
    def __init__(self, db: Database, tracker: Tracker, settings: Settings):
        self.db = db
        self.tracker = tracker
        self.settings = settings

    async def run(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info("Iniciando chequeo diario de precios…")
        reports = await self.tracker.check_all()
        notified = 0
        for report in reports:
            if not report.notable:
                continue
            try:
                await self._notify(context, report)
                notified += 1
            except Exception:  # noqa: BLE001 - nunca tumbar el job por un envío
                logger.exception(
                    "No se pudo notificar al usuario %s", report.product.owner_id
                )
        logger.info(
            "Chequeo diario terminado: %d producto(s), %d aviso(s) enviados.",
            len(reports),
            notified,
        )

    async def _notify(self, context: ContextTypes.DEFAULT_TYPE, report: ProductReport) -> None:
        header = "🔔 <b>Cambios detectados</b>\n\n"
        if report.any_low:
            header = "💎 <b>¡Mínimo histórico!</b>\n\n"
        if report.any_target_hit:
            header = "🎯 <b>¡Precio objetivo alcanzado!</b>\n\n"
        text = header + formatting.format_report(report, only_changes=True)
        await context.bot.send_message(
            chat_id=report.product.owner_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )


def setup_daily_job(
    app: Application, db: Database, tracker: Tracker, settings: Settings
) -> None:
    """Programa el chequeo diario en el JobQueue de la aplicación."""
    if app.job_queue is None:
        logger.warning(
            "JobQueue no disponible. Instala python-telegram-bot[job-queue]."
        )
        return

    checker = DailyChecker(db, tracker, settings)
    tz = ZoneInfo(settings.timezone)
    run_time = dt.time(
        hour=settings.daily_check_hour,
        minute=settings.daily_check_minute,
        tzinfo=tz,
    )
    app.job_queue.run_daily(checker.run, time=run_time, name="daily_price_check")
    logger.info(
        "Chequeo diario programado a las %02d:%02d (%s).",
        settings.daily_check_hour,
        settings.daily_check_minute,
        settings.timezone,
    )
