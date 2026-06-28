"""Formateo de mensajes para Telegram (HTML parse mode).

Mantiene toda la presentación (precios, informes, listados) separada de la
lógica de los handlers.
"""
from __future__ import annotations

from html import escape

from .database import Database, Product
from .tracker import ProductReport, StoreCheck

_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}


def fmt_price(price: float | None, currency: str = "EUR") -> str:
    """Formatea un precio al estilo español: 1.299,99 €."""
    if price is None:
        return "—"
    symbol = _SYMBOLS.get(currency, currency)
    s = f"{price:,.2f}"  # estilo anglosajón 1,299.99
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")  # -> 1.299,99
    return f"{s} {symbol}"


def fmt_delta(check: StoreCheck) -> str:
    """Texto del cambio de precio con flecha y signo."""
    delta = check.delta
    if delta is None:
        return ""
    currency = check.store.currency
    arrow = "🔻" if delta < 0 else "🔺"
    sign = "" if delta < 0 else "+"
    return f"{arrow} {sign}{fmt_price(delta, currency)}"


# --------------------------------------------------------------------------- #
# Informe de un chequeo (usado por /check y por las alertas diarias)
# --------------------------------------------------------------------------- #
def format_report(report: ProductReport, only_changes: bool = False) -> str:
    p = report.product
    lines = [f"📦 <b>{escape(p.name)}</b>"]

    if p.target_price is not None:
        lines.append(f"🎯 Objetivo: {fmt_price(p.target_price)}")
    lines.append("")

    shown = 0
    for check in report.checks:
        if only_changes and not (
            check.changed or check.is_all_time_low or check.target_hit
        ):
            continue
        shown += 1
        lines.append(_format_check_line(check))

    if only_changes and shown == 0:
        lines.append("Sin cambios de precio. ✅")

    # Mejor precio actual
    if report.best_stores and report.best_price is not None:
        names = ", ".join(escape(s.name) for s in report.best_stores)
        lines.append("")
        lines.append(
            f"🏆 Mejor precio ahora: <b>{fmt_price(report.best_price, report.best_stores[0].currency)}</b> "
            f"en {names}"
        )
    return "\n".join(lines)


def _format_check_line(check: StoreCheck) -> str:
    store = check.store
    if not check.ok:
        return f"⚠️ <b>{escape(store.name)}</b>: no se pudo leer ({escape(check.result.error or '')})"

    parts = [f"🛒 <b>{escape(store.name)}</b>: {fmt_price(check.new_price, store.currency)}"]
    flags = []
    if check.changed:
        flags.append(fmt_delta(check))
    if check.is_all_time_low:
        flags.append("💎 ¡mínimo histórico!")
    if check.target_hit:
        flags.append("🎯 ¡objetivo alcanzado!")
    if check.result.available is False:
        flags.append("🚫 agotado")
    if flags:
        parts.append("  " + "  ".join(flags))
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Detalle de producto (con mín/máx histórico por tienda)
# --------------------------------------------------------------------------- #
def format_product_detail(db: Database, product: Product) -> str:
    lines = [f"📦 <b>{escape(product.name)}</b>  <code>#{product.id}</code>"]
    if product.target_price is not None:
        lines.append(f"🎯 Precio objetivo: {fmt_price(product.target_price)}")
    lines.append("")

    stores = db.list_stores(product.id)
    if not stores:
        lines.append("Aún no hay tiendas. Añade una con el botón ➕ Añadir tienda.")
        return "\n".join(lines)

    for store in stores:
        mn, mx = db.get_min_max(store.id)
        header = f"🛒 <b>{escape(store.name)}</b>  <code>#{store.id}</code>"
        if store.last_price is not None:
            header += f" — {fmt_price(store.last_price, store.currency)}"
            if store.available is False:
                header += " 🚫"
        elif store.last_error:
            header += " — ⚠️ error"
        lines.append(header)
        if mn is not None and mx is not None:
            lines.append(
                f"   📉 mín: {fmt_price(mn, store.currency)} · "
                f"📈 máx: {fmt_price(mx, store.currency)}"
            )
        if store.last_error:
            lines.append(f"   ⚠️ {escape(store.last_error)}")

    best = db.best_current_stores(product.id)
    if best:
        bstores, bprice = best
        names = ", ".join(escape(s.name) for s in bstores)
        lines.append("")
        lines.append(
            f"🏆 Mejor precio: <b>{fmt_price(bprice, bstores[0].currency)}</b> "
            f"en {names}"
        )
    return "\n".join(lines)


def format_product_list_line(db: Database, product: Product) -> str:
    stores = db.list_stores(product.id)
    best = db.best_current_stores(product.id)
    if best:
        bstores, bprice = best
        store_txt = (
            escape(bstores[0].name) if len(bstores) == 1 else f"{len(bstores)} tiendas"
        )
        price_txt = f"{fmt_price(bprice, bstores[0].currency)} ({store_txt})"
    else:
        price_txt = "sin datos aún"
    return f"<code>#{product.id}</code> 📦 <b>{escape(product.name)}</b> — {len(stores)} tienda(s) · {price_txt}"
