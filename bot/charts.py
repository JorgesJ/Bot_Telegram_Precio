"""Generación de gráficas de evolución de precios con matplotlib.

Se usa el backend 'Agg' (sin interfaz gráfica) porque corre en un servidor.
Devuelve la imagen PNG en un buffer de memoria, lista para enviar por Telegram.
"""
from __future__ import annotations

import io
import logging
from datetime import datetime

import matplotlib

matplotlib.use("Agg")  # backend sin display, imprescindible en servidor
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from .database import Database, Product  # noqa: E402

logger = logging.getLogger(__name__)


def _parse_ts(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return datetime.now()


def build_price_chart(db: Database, product: Product) -> io.BytesIO | None:
    """Genera un PNG con la evolución de precios de cada tienda del producto.

    Devuelve None si no hay suficiente histórico para dibujar nada.
    """
    stores = db.list_stores(product.id)
    if not stores:
        return None

    fig, ax = plt.subplots(figsize=(10, 5.5))
    plotted_any = False
    currency = "EUR"

    for store in stores:
        history = db.get_history(store.id)
        if not history:
            continue
        currency = store.currency or currency
        xs = [_parse_ts(p.checked_at) for p in history]
        ys = [p.price for p in history]
        if len(xs) == 1:
            # Un solo punto: lo marcamos para que se vea.
            ax.scatter(xs, ys, label=store.name, s=40)
        else:
            ax.plot(xs, ys, marker="o", markersize=3, linewidth=1.6, label=store.name)
        plotted_any = True

    if not plotted_any:
        plt.close(fig)
        return None

    symbol = {"EUR": "€", "USD": "$", "GBP": "£"}.get(currency, currency)
    ax.set_title(f"Evolución de precio · {product.name}", fontsize=13, fontweight="bold")
    ax.set_ylabel(f"Precio ({symbol})")
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend(loc="best", fontsize=9)

    # Formato de fechas en el eje X.
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    fig.autofmt_xdate(rotation=30)

    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=110)
    plt.close(fig)
    buffer.seek(0)
    return buffer
