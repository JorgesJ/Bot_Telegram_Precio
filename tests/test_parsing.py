"""Tests del parsing de precios y extracción desde HTML."""
import pytest

from bot.scraper import detect_currency, extract_price, parse_price


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1.299,99 €", 1299.99),       # europeo con miles
        ("79,99 €", 79.99),            # europeo simple
        ("€1,299.99", 1299.99),        # anglosajón con miles
        ("1299.00", 1299.00),          # solo punto decimal
        ("1.299", 1299.0),             # punto como miles, sin decimales
        ("1,299", 1299.0),             # coma como miles
        ("Precio: 49,95€ IVA incl.", 49.95),
        ("2 499,00 €", 2499.0),        # espacio como separador de miles
        ("\u00a01 234,56\u00a0€", 1234.56),  # non-breaking spaces
    ],
)
def test_parse_price_formats(text, expected):
    assert parse_price(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "  ", "sin precio", None, "0,00 €"])
def test_parse_price_invalid(text):
    assert parse_price(text) is None


def test_detect_currency():
    assert detect_currency("79,99 €") == "EUR"
    assert detect_currency("$19.99") == "USD"
    assert detect_currency("£10") == "GBP"
    assert detect_currency("19.99") == "EUR"  # por defecto


def test_extract_price_from_jsonld():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "Product", "name": "Cafetera",
     "offers": {"@type": "Offer", "price": "129.95", "priceCurrency": "EUR"}}
    </script></head><body></body></html>
    """
    result = extract_price(html, "https://tienda-desconocida.com/p/1", None)
    assert result.ok
    assert result.price == pytest.approx(129.95)
    assert result.currency == "EUR"
    assert result.method == "json-ld"


def test_extract_price_from_meta_tag():
    html = """
    <html><head>
    <meta property="product:price:amount" content="59.90">
    </head><body></body></html>
    """
    result = extract_price(html, "https://otra-tienda.com/p", None)
    assert result.ok
    assert result.price == pytest.approx(59.90)


def test_extract_price_with_user_selector():
    html = '<html><body><span class="mi-precio">  84,50 €  </span></body></html>'
    result = extract_price(html, "https://x.com", ".mi-precio")
    assert result.ok
    assert result.price == pytest.approx(84.50)
    assert result.method.startswith("selector:")


def test_extract_price_unavailable():
    html = """
    <html><body>
      <span class="price">99,00 €</span>
      <div>Producto agotado temporalmente</div>
    </body></html>
    """
    result = extract_price(html, "https://x.com", ".price")
    assert result.ok
    assert result.available is False


def test_extract_price_not_found():
    html = "<html><body><p>Hola</p></body></html>"
    result = extract_price(html, "https://x.com", None)
    assert not result.ok
    assert result.error
