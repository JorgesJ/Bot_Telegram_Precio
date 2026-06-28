"""Capa de acceso a datos SQLite.

Tablas:
- users:         usuarios de Telegram autorizados/registrados.
- products:      productos a seguir (pertenecen a un usuario).
- stores:        tiendas/URLs asociadas a un producto.
- price_history: histórico de precios por tienda (un registro por chequeo).
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


def _utcnow() -> str:
    """Marca de tiempo ISO-8601 en UTC."""
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Modelos
# --------------------------------------------------------------------------- #
@dataclass
class Product:
    id: int
    owner_id: int
    name: str
    target_price: Optional[float]
    created_at: str


@dataclass
class Store:
    id: int
    product_id: int
    name: str
    url: str
    css_selector: Optional[str]
    currency: str
    last_price: Optional[float]
    available: Optional[bool]
    last_checked: Optional[str]
    last_error: Optional[str]
    created_at: str


@dataclass
class PricePoint:
    id: int
    store_id: int
    price: float
    available: bool
    checked_at: str


# --------------------------------------------------------------------------- #
# Base de datos
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id    INTEGER PRIMARY KEY,
    username   TEXT,
    first_name TEXT,
    is_admin   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id     INTEGER NOT NULL,
    name         TEXT NOT NULL,
    target_price REAL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stores (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id   INTEGER NOT NULL,
    name         TEXT NOT NULL,
    url          TEXT NOT NULL,
    css_selector TEXT,
    currency     TEXT NOT NULL DEFAULT 'EUR',
    last_price   REAL,
    available    INTEGER,
    last_checked TEXT,
    last_error   TEXT,
    created_at   TEXT NOT NULL,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS price_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id   INTEGER NOT NULL,
    price      REAL NOT NULL,
    available  INTEGER NOT NULL DEFAULT 1,
    checked_at TEXT NOT NULL,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS api_usage (
    month   TEXT PRIMARY KEY,
    credits INTEGER NOT NULL DEFAULT 0,
    warned  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_stores_product ON stores(product_id);
CREATE INDEX IF NOT EXISTS idx_history_store ON price_history(store_id);
CREATE INDEX IF NOT EXISTS idx_products_owner ON products(owner_id);
"""


class Database:
    """Wrapper sencillo y thread-safe sobre sqlite3."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ #
    # Usuarios
    # ------------------------------------------------------------------ #
    def upsert_user(
        self, chat_id: int, username: Optional[str], first_name: Optional[str]
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO users (chat_id, username, first_name, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name
                """,
                (chat_id, username, first_name, _utcnow()),
            )
            self._conn.commit()

    def list_user_ids(self) -> list[int]:
        with self._lock:
            rows = self._conn.execute("SELECT chat_id FROM users").fetchall()
        return [r["chat_id"] for r in rows]

    # ------------------------------------------------------------------ #
    # Productos
    # ------------------------------------------------------------------ #
    def add_product(self, owner_id: int, name: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO products (owner_id, name, created_at) VALUES (?, ?, ?)",
                (owner_id, name.strip(), _utcnow()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def get_product(self, product_id: int) -> Optional[Product]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM products WHERE id = ?", (product_id,)
            ).fetchone()
        return _to_product(row) if row else None

    def list_products(self, owner_id: Optional[int] = None) -> list[Product]:
        with self._lock:
            if owner_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM products ORDER BY id"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM products WHERE owner_id = ? ORDER BY id",
                    (owner_id,),
                ).fetchall()
        return [_to_product(r) for r in rows]

    def set_target_price(self, product_id: int, target: Optional[float]) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE products SET target_price = ? WHERE id = ?",
                (target, product_id),
            )
            self._conn.commit()

    def rename_product(self, product_id: int, name: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE products SET name = ? WHERE id = ?", (name.strip(), product_id)
            )
            self._conn.commit()

    def delete_product(self, product_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
            self._conn.commit()

    # ------------------------------------------------------------------ #
    # Tiendas
    # ------------------------------------------------------------------ #
    def add_store(
        self,
        product_id: int,
        name: str,
        url: str,
        css_selector: Optional[str] = None,
        currency: str = "EUR",
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO stores
                    (product_id, name, url, css_selector, currency, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (product_id, name, url, css_selector, currency, _utcnow()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def get_store(self, store_id: int) -> Optional[Store]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM stores WHERE id = ?", (store_id,)
            ).fetchone()
        return _to_store(row) if row else None

    def list_stores(self, product_id: int) -> list[Store]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM stores WHERE product_id = ? ORDER BY id",
                (product_id,),
            ).fetchall()
        return [_to_store(r) for r in rows]

    def delete_store(self, store_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM stores WHERE id = ?", (store_id,))
            self._conn.commit()

    def set_store_selector(self, store_id: int, css_selector: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE stores SET css_selector = ? WHERE id = ?",
                (css_selector, store_id),
            )
            self._conn.commit()

    # ------------------------------------------------------------------ #
    # Precios e histórico
    # ------------------------------------------------------------------ #
    def record_price(
        self, store_id: int, price: float, available: bool = True
    ) -> None:
        """Guarda un nuevo punto de precio y actualiza la tienda."""
        now = _utcnow()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO price_history (store_id, price, available, checked_at)
                VALUES (?, ?, ?, ?)
                """,
                (store_id, price, 1 if available else 0, now),
            )
            self._conn.execute(
                """
                UPDATE stores
                SET last_price = ?, available = ?, last_checked = ?, last_error = NULL
                WHERE id = ?
                """,
                (price, 1 if available else 0, now, store_id),
            )
            self._conn.commit()

    def record_error(self, store_id: int, error: str) -> None:
        """Registra que el chequeo falló (sin tocar el último precio válido)."""
        with self._lock:
            self._conn.execute(
                "UPDATE stores SET last_checked = ?, last_error = ? WHERE id = ?",
                (_utcnow(), error[:500], store_id),
            )
            self._conn.commit()

    def get_history(self, store_id: int, limit: Optional[int] = None) -> list[PricePoint]:
        with self._lock:
            sql = (
                "SELECT * FROM price_history WHERE store_id = ? ORDER BY checked_at ASC"
            )
            if limit:
                sql += f" LIMIT {int(limit)}"
            rows = self._conn.execute(sql, (store_id,)).fetchall()
        return [_to_pricepoint(r) for r in rows]

    def get_min_max(self, store_id: int) -> tuple[Optional[float], Optional[float]]:
        """Devuelve (mínimo, máximo) histórico de una tienda."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(price) AS mn, MAX(price) AS mx FROM price_history "
                "WHERE store_id = ?",
                (store_id,),
            ).fetchone()
        return (row["mn"], row["mx"]) if row else (None, None)

    def is_all_time_low(self, store_id: int, price: float) -> bool:
        """True si `price` es <= al mínimo histórico previo de la tienda."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(price) AS mn FROM price_history WHERE store_id = ?",
                (store_id,),
            ).fetchone()
        previous_min = row["mn"] if row else None
        return previous_min is None or price <= previous_min

    def best_current_price(
        self, product_id: int
    ) -> Optional[tuple[Store, float]]:
        """Tienda con el precio actual más bajo (entre las disponibles)."""
        stores = [
            s
            for s in self.list_stores(product_id)
            if s.last_price is not None and (s.available is not False)
        ]
        if not stores:
            return None
        best = min(stores, key=lambda s: s.last_price)  # type: ignore[arg-type]
        return best, float(best.last_price)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ #
    # Consumo de la API de scraping (contador mensual)
    # ------------------------------------------------------------------ #
    def get_month_credits(self, month: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT credits FROM api_usage WHERE month = ?", (month,)
            ).fetchone()
        return int(row["credits"]) if row else 0

    def add_month_credits(self, month: str, amount: int) -> int:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO api_usage (month, credits) VALUES (?, ?)
                ON CONFLICT(month) DO UPDATE SET credits = credits + excluded.credits
                """,
                (month, amount),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT credits FROM api_usage WHERE month = ?", (month,)
            ).fetchone()
        return int(row["credits"]) if row else amount

    def is_month_warned(self, month: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT warned FROM api_usage WHERE month = ?", (month,)
            ).fetchone()
        return bool(row["warned"]) if row else False

    def mark_month_warned(self, month: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO api_usage (month, warned) VALUES (?, 1)
                ON CONFLICT(month) DO UPDATE SET warned = 1
                """,
                (month,),
            )
            self._conn.commit()


# --------------------------------------------------------------------------- #
# Conversores fila -> dataclass
# --------------------------------------------------------------------------- #
def _to_product(row: sqlite3.Row) -> Product:
    return Product(
        id=row["id"],
        owner_id=row["owner_id"],
        name=row["name"],
        target_price=row["target_price"],
        created_at=row["created_at"],
    )


def _to_store(row: sqlite3.Row) -> Store:
    return Store(
        id=row["id"],
        product_id=row["product_id"],
        name=row["name"],
        url=row["url"],
        css_selector=row["css_selector"],
        currency=row["currency"],
        last_price=row["last_price"],
        available=None if row["available"] is None else bool(row["available"]),
        last_checked=row["last_checked"],
        last_error=row["last_error"],
        created_at=row["created_at"],
    )


def _to_pricepoint(row: sqlite3.Row) -> PricePoint:
    return PricePoint(
        id=row["id"],
        store_id=row["store_id"],
        price=row["price"],
        available=bool(row["available"]),
        checked_at=row["checked_at"],
    )
