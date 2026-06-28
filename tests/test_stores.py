"""Tests del registro de tiendas y utilidades de URL."""
from bot import stores


def test_normalize_domain():
    assert stores.normalize_domain("https://www.amazon.es/dp/B0XXX") == "amazon"
    assert stores.normalize_domain("https://pccomponentes.com/p/1") == "pccomponentes"
    assert stores.normalize_domain("http://www.mediamarkt.es/x") == "mediamarkt"


def test_infer_store_name_known():
    assert stores.infer_store_name("https://www.amazon.es/dp/1") == "Amazon"
    assert stores.infer_store_name("https://www.mediamarkt.es/x") == "MediaMarkt"
    assert stores.infer_store_name("https://www.leroymerlin.es/p") == "Leroy Merlin"


def test_infer_store_name_unknown():
    assert stores.infer_store_name("https://tiendarara.com/p") == "Tiendarara"


def test_candidate_selectors_priorizes_user_selector():
    sels = stores.candidate_selectors("https://www.amazon.es/dp/1", ".mi-selector")
    assert sels[0] == ".mi-selector"
    # incluye los del dominio y los genéricos
    assert any("a-offscreen" in s for s in sels)
    assert any(".price" == s for s in sels)


def test_candidate_selectors_no_duplicates():
    sels = stores.candidate_selectors("https://x.com", 'meta[itemprop="price"]')
    assert len(sels) == len(set(sels))


def test_is_valid_url():
    assert stores.is_valid_url("https://amazon.es/x")
    assert stores.is_valid_url("http://x.com")
    assert not stores.is_valid_url("amazon.es/x")
    assert not stores.is_valid_url("ftp://x.com")
    assert not stores.is_valid_url("hola")
