"""Lógica central de seguimiento de precios.

Une la base de datos y el scraper:
- Chequea todas las tiendas de un producto.
- Detecta cambios de precio respecto al último valor guardado.
- Detecta mínimo histórico (chollo) ANTES de guardar el nuevo punto.
- Detecta si se alcanza el precio objetivo.
- Calcula la mejor tienda actual.

Devuelve estructuras de datos puras (ProductReport / StoreCheck) que la capa
de Telegram y el scheduler formatean en mensajes. No depende de Telegram.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .database import Database, Product, Store
from .scraper import ScrapeResult, Scraper

logger = logging.getLogger(__name__)


@dataclass
class StoreCheck:
    store: Store
    result: ScrapeResult
    old_price: Optional[float]
    new_price: Optional[float]
    changed: bool = False
    is_all_time_low: bool = False
    target_hit: bool = False

    @property
    def ok(self) -> bool:
        return self.result.ok

    @property
    def delta(self) -> Optional[float]:
        if self.old_price is None or self.new_price is None:
            return None
        return self.new_price - self.old_price

    @property
    def went_down(self) -> bool:
        return self.delta is not None and self.delta < 0

    @property
    def went_up(self) -> bool:
        return self.delta is not None and self.delta > 0


@dataclass
class ProductReport:
    product: Product
    checks: list[StoreCheck] = field(default_factory=list)
    best_store: Optional[Store] = None
    best_price: Optional[float] = None

    @property
    def any_change(self) -> bool:
        return any(c.changed for c in self.checks)

    @property
    def any_low(self) -> bool:
        return any(c.is_all_time_low and c.ok for c in self.checks)

    @property
    def any_target_hit(self) -> bool:
        return any(c.target_hit for c in self.checks)

    @property
    def any_error(self) -> bool:
        return any(not c.ok for c in self.checks)

    @property
    def notable(self) -> bool:
        """¿Merece la pena avisar al usuario?"""
        return self.any_change or self.any_low or self.any_target_hit


class Tracker:
    def __init__(self, db: Database, scraper: Scraper):
        self.db = db
        self.scraper = scraper

    async def check_product(self, product_id: int) -> Optional[ProductReport]:
        product = self.db.get_product(product_id)
        if product is None:
            return None

        store_list = self.db.list_stores(product_id)
        report = ProductReport(product=product)
        if not store_list:
            return report

        items = [(s.url, s.css_selector) for s in store_list]
        results = await self.scraper.fetch_many(items)

        for store, result in zip(store_list, results):
            check = self._process_store(product, store, result)
            report.checks.append(check)

        best = self.db.best_current_price(product_id)
        if best:
            report.best_store, report.best_price = best
        return report

    def _process_store(
        self, product: Product, store: Store, result: ScrapeResult
    ) -> StoreCheck:
        old_price = store.last_price

        if not result.ok or result.price is None:
            self.db.record_error(store.id, result.error or "Error desconocido")
            return StoreCheck(
                store=store, result=result, old_price=old_price, new_price=None
            )

        new_price = result.price
        # IMPORTANTE: comprobar mínimo histórico ANTES de insertar el nuevo punto.
        is_low = self.db.is_all_time_low(store.id, new_price)
        changed = old_price is not None and abs(new_price - old_price) >= 0.01
        target_hit = (
            product.target_price is not None and new_price <= product.target_price
        )

        self.db.record_price(store.id, new_price, available=result.available)

        return StoreCheck(
            store=store,
            result=result,
            old_price=old_price,
            new_price=new_price,
            changed=changed,
            is_all_time_low=is_low and old_price is not None,
            target_hit=target_hit,
        )

    async def check_store(self, store_id: int) -> Optional[StoreCheck]:
        """Chequea una única tienda (p.ej. justo al añadirla)."""
        store = self.db.get_store(store_id)
        if store is None:
            return None
        product = self.db.get_product(store.product_id)
        if product is None:
            return None
        result = await self.scraper.fetch(store.url, store.css_selector)
        return self._process_store(product, store, result)

    async def check_all(self) -> list[ProductReport]:
        """Chequea todos los productos de todos los usuarios (uso del scheduler)."""
        reports: list[ProductReport] = []
        for product in self.db.list_products():
            report = await self.check_product(product.id)
            if report is not None:
                reports.append(report)
        return reports
