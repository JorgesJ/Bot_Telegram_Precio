"""Control del consumo mensual de la API de scraping (ScraperAPI).

El contador se guarda por mes (YYYY-MM) en la base de datos, así que el reinicio
mensual es automático: al cambiar de mes se empieza una fila nueva en 0.
"""
from __future__ import annotations

from typing import Optional

from .config import Settings
from .database import Database
from .scraper import month_key


def usage_summary(db: Database, settings: Settings) -> dict:
    month = month_key()
    used = db.get_month_credits(month)
    budget = settings.scraperapi_monthly_budget
    cost = max(settings.scraperapi_credits_per_request, 1)
    percent = (used / budget * 100) if budget else 0.0
    return {
        "month": month,
        "used": used,
        "budget": budget,
        "percent": percent,
        "requests_done": used // cost,
        "requests_left": max(budget - used, 0) // cost,
    }


def usage_text(db: Database, settings: Settings) -> str:
    if not settings.api_enabled:
        return (
            "📊 <b>Consumo API</b>\n\n"
            "La API de scraping no está configurada, así que no se usan créditos. "
            "Tiendas con anti-bot (MediaMarkt, El Corte Inglés) no se leerán."
        )
    s = usage_summary(db, settings)
    return (
        "📊 <b>Consumo API ({month})</b>\n\n"
        "Créditos: <b>{used}</b> / {budget} ({percent:.0f}%)\n"
        "Consultas hechas: ~{requests_done}\n"
        "Consultas restantes: ~{requests_left}\n\n"
        "Se reinicia automáticamente el día 1 del mes que viene."
    ).format(**s)


def pop_alert(db: Database, settings: Settings) -> Optional[str]:
    """Devuelve un aviso (una sola vez al mes) si se cruza el umbral configurado."""
    if not settings.api_enabled:
        return None
    month = month_key()
    used = db.get_month_credits(month)
    budget = settings.scraperapi_monthly_budget
    if budget <= 0:
        return None
    percent = used / budget * 100
    if percent >= settings.scraperapi_warn_percent and not db.is_month_warned(month):
        db.mark_month_warned(month)
        return (
            "⚠️ <b>Aviso de consumo API</b>\n\n"
            f"Llevas <b>{used}/{budget}</b> créditos este mes ({percent:.0f}%).\n"
            "Cuando se agoten, las consultas a las tiendas con anti-bot se "
            "<b>pausarán automáticamente</b> hasta el mes que viene."
        )
    return None
