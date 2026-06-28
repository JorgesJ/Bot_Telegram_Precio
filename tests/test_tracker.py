"""Tests de la lógica central del tracker, con un scraper falso (sin red)."""
import asyncio

import pytest

from bot.database import Database
from bot.scraper import ScrapeResult
from bot.tracker import Tracker


class FakeScraper:
    """Scraper falso: devuelve precios predefinidos por URL.

    `prices` es un dict {url: precio | ScrapeResult}. Si el valor es None,
    simula un fallo de lectura.
    """

    def __init__(self, prices):
        self.prices = prices

    def _result_for(self, url, selector):
        value = self.prices.get(url)
        if isinstance(value, ScrapeResult):
            return value
        if value is None:
            return ScrapeResult(url=url, ok=False, error="no encontrado")
        return ScrapeResult(url=url, ok=True, price=float(value), currency="EUR")

    async def fetch(self, url, user_selector=None):
        return self._result_for(url, user_selector)

    async def fetch_many(self, items):
        return [self._result_for(url, sel) for url, sel in items]


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "t.db"))
    yield database
    database.close()


def run(coro):
    return asyncio.run(coro)


def test_first_check_no_change_flag(db):
    pid = db.add_product(1, "X")
    db.add_store(pid, "Amazon", "https://a.com/p")
    tracker = Tracker(db, FakeScraper({"https://a.com/p": 100.0}))
    report = run(tracker.check_product(pid))
    check = report.checks[0]
    assert check.ok
    assert check.new_price == 100.0
    assert check.changed is False        # primera lectura, no hay "cambio"
    assert check.is_all_time_low is False  # requiere precio previo
    assert report.notable is False


def test_price_drop_detected(db):
    pid = db.add_product(1, "X")
    sid = db.add_store(pid, "Amazon", "https://a.com/p")
    db.record_price(sid, 100.0)  # precio previo
    tracker = Tracker(db, FakeScraper({"https://a.com/p": 80.0}))
    report = run(tracker.check_product(pid))
    check = report.checks[0]
    assert check.changed is True
    assert check.went_down is True
    assert check.delta == pytest.approx(-20.0)
    assert check.is_all_time_low is True  # 80 < 100 previo
    assert report.notable is True
    assert report.any_low is True


def test_price_increase_detected(db):
    pid = db.add_product(1, "X")
    sid = db.add_store(pid, "Amazon", "https://a.com/p")
    db.record_price(sid, 100.0)
    tracker = Tracker(db, FakeScraper({"https://a.com/p": 130.0}))
    report = run(tracker.check_product(pid))
    check = report.checks[0]
    assert check.changed is True
    assert check.went_up is True
    assert check.is_all_time_low is False


def test_target_hit(db):
    pid = db.add_product(1, "X")
    sid = db.add_store(pid, "Amazon", "https://a.com/p")
    db.record_price(sid, 100.0)
    db.set_target_price(pid, 85.0)
    tracker = Tracker(db, FakeScraper({"https://a.com/p": 84.0}))
    report = run(tracker.check_product(pid))
    assert report.any_target_hit is True
    assert report.checks[0].target_hit is True


def test_best_price_across_stores(db):
    pid = db.add_product(1, "X")
    db.add_store(pid, "Amazon", "https://a.com/p")
    db.add_store(pid, "MediaMarkt", "https://m.com/p")
    tracker = Tracker(
        db, FakeScraper({"https://a.com/p": 100.0, "https://m.com/p": 92.0})
    )
    report = run(tracker.check_product(pid))
    assert report.best_store.name == "MediaMarkt"
    assert report.best_price == pytest.approx(92.0)


def test_scrape_failure_is_graceful(db):
    pid = db.add_product(1, "X")
    sid = db.add_store(pid, "Amazon", "https://a.com/p")
    tracker = Tracker(db, FakeScraper({"https://a.com/p": None}))
    report = run(tracker.check_product(pid))
    check = report.checks[0]
    assert check.ok is False
    assert report.any_error is True
    # No se guardó precio, pero sí el error.
    assert db.get_store(sid).last_error is not None


def test_check_store_seeds_initial_price(db):
    pid = db.add_product(1, "X")
    sid = db.add_store(pid, "Amazon", "https://a.com/p")
    tracker = Tracker(db, FakeScraper({"https://a.com/p": 49.99}))
    check = run(tracker.check_store(sid))
    assert check.ok
    assert db.get_store(sid).last_price == pytest.approx(49.99)


def test_check_all_multiple_products(db):
    p1 = db.add_product(1, "A")
    db.add_store(p1, "Amazon", "https://a.com/1")
    p2 = db.add_product(2, "B")
    db.add_store(p2, "Amazon", "https://a.com/2")
    tracker = Tracker(
        db, FakeScraper({"https://a.com/1": 10.0, "https://a.com/2": 20.0})
    )
    reports = run(tracker.check_all())
    assert len(reports) == 2
