"""Tests de la capa de base de datos (solo librería estándar)."""
import pytest

from bot.database import Database


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    yield database
    database.close()


def test_add_and_get_product(db):
    pid = db.add_product(owner_id=10, name="Cafetera Krups")
    product = db.get_product(pid)
    assert product is not None
    assert product.name == "Cafetera Krups"
    assert product.owner_id == 10
    assert product.target_price is None


def test_list_products_by_owner(db):
    db.add_product(1, "A")
    db.add_product(1, "B")
    db.add_product(2, "C")
    assert len(db.list_products(owner_id=1)) == 2
    assert len(db.list_products(owner_id=2)) == 1
    assert len(db.list_products()) == 3


def test_target_price(db):
    pid = db.add_product(1, "X")
    db.set_target_price(pid, 79.99)
    assert db.get_product(pid).target_price == pytest.approx(79.99)
    db.set_target_price(pid, None)
    assert db.get_product(pid).target_price is None


def test_add_store_and_list(db):
    pid = db.add_product(1, "X")
    sid = db.add_store(pid, "Amazon", "https://amazon.es/x")
    stores = db.list_stores(pid)
    assert len(stores) == 1
    assert stores[0].id == sid
    assert stores[0].name == "Amazon"
    assert stores[0].currency == "EUR"


def test_record_price_updates_store_and_history(db):
    pid = db.add_product(1, "X")
    sid = db.add_store(pid, "Amazon", "https://amazon.es/x")
    db.record_price(sid, 100.0)
    db.record_price(sid, 90.0)
    store = db.get_store(sid)
    assert store.last_price == pytest.approx(90.0)
    assert store.available is True
    history = db.get_history(sid)
    assert len(history) == 2
    assert [h.price for h in history] == [100.0, 90.0]


def test_min_max(db):
    pid = db.add_product(1, "X")
    sid = db.add_store(pid, "Amazon", "https://amazon.es/x")
    for price in (120.0, 99.5, 110.0, 95.0):
        db.record_price(sid, price)
    mn, mx = db.get_min_max(sid)
    assert mn == pytest.approx(95.0)
    assert mx == pytest.approx(120.0)


def test_is_all_time_low(db):
    pid = db.add_product(1, "X")
    sid = db.add_store(pid, "Amazon", "https://amazon.es/x")
    # Sin histórico, cualquier precio es mínimo.
    assert db.is_all_time_low(sid, 100.0) is True
    db.record_price(sid, 100.0)
    assert db.is_all_time_low(sid, 100.0) is True   # igual al mínimo
    assert db.is_all_time_low(sid, 99.99) is True   # nuevo mínimo
    assert db.is_all_time_low(sid, 100.01) is False  # por encima


def test_best_current_price(db):
    pid = db.add_product(1, "X")
    s1 = db.add_store(pid, "Amazon", "https://amazon.es/x")
    s2 = db.add_store(pid, "MediaMarkt", "https://mediamarkt.es/x")
    db.record_price(s1, 100.0)
    db.record_price(s2, 95.0)
    best = db.best_current_price(pid)
    assert best is not None
    store, price = best
    assert store.id == s2
    assert price == pytest.approx(95.0)


def test_best_current_price_ignores_unavailable(db):
    pid = db.add_product(1, "X")
    s1 = db.add_store(pid, "Amazon", "https://amazon.es/x")
    s2 = db.add_store(pid, "MediaMarkt", "https://mediamarkt.es/x")
    db.record_price(s1, 100.0, available=True)
    db.record_price(s2, 80.0, available=False)  # más barato pero agotado
    store, price = db.best_current_price(pid)
    assert store.id == s1
    assert price == pytest.approx(100.0)


def test_delete_product_cascades(db):
    pid = db.add_product(1, "X")
    sid = db.add_store(pid, "Amazon", "https://amazon.es/x")
    db.record_price(sid, 100.0)
    db.delete_product(pid)
    assert db.get_product(pid) is None
    assert db.list_stores(pid) == []
    assert db.get_history(sid) == []


def test_record_error(db):
    pid = db.add_product(1, "X")
    sid = db.add_store(pid, "Amazon", "https://amazon.es/x")
    db.record_error(sid, "HTTP 503")
    store = db.get_store(sid)
    assert store.last_error == "HTTP 503"
    assert store.last_price is None



def test_month_credits_accumulate(db):
    assert db.get_month_credits("2026-06") == 0
    assert db.add_month_credits("2026-06", 10) == 10
    assert db.add_month_credits("2026-06", 10) == 20
    assert db.get_month_credits("2026-06") == 20
    # Otro mes empieza de cero (reset automático).
    assert db.get_month_credits("2026-07") == 0


def test_month_warned_flag(db):
    assert db.is_month_warned("2026-06") is False
    db.mark_month_warned("2026-06")
    assert db.is_month_warned("2026-06") is True
    # No afecta a los créditos ya contados.
    db.add_month_credits("2026-06", 5)
    assert db.get_month_credits("2026-06") == 5
    assert db.is_month_warned("2026-06") is True



def test_best_current_stores_tie(db):
    pid = db.add_product(1, "X")
    s1 = db.add_store(pid, "Amazon", "https://amazon.es/x")
    s2 = db.add_store(pid, "Delonghi", "https://delonghi.com/x")
    s3 = db.add_store(pid, "MediaMarkt", "https://mediamarkt.es/x")
    db.record_price(s1, 449.90)
    db.record_price(s2, 449.90)
    db.record_price(s3, 499.00)
    tied, price = db.best_current_stores(pid)
    assert price == pytest.approx(449.90)
    assert {s.name for s in tied} == {"Amazon", "Delonghi"}


def test_best_current_stores_single(db):
    pid = db.add_product(1, "X")
    s1 = db.add_store(pid, "Amazon", "https://amazon.es/x")
    s2 = db.add_store(pid, "Tien21", "https://tien21.es/x")
    db.record_price(s1, 449.90)
    db.record_price(s2, 449.00)
    tied, price = db.best_current_stores(pid)
    assert price == pytest.approx(449.00)
    assert [s.name for s in tied] == ["Tien21"]
