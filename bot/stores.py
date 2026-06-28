"""Registro de tiendas conocidas y utilidades de URL.

Para cada dominio guardamos:
- name:      nombre legible de la tienda.
- selectors: lista de selectores CSS candidatos donde suele estar el precio.

El scraper prueba, por orden: el selector configurado por el usuario, JSON-LD,
los selectores del dominio y, por último, selectores genéricos.

Añadir una tienda nueva (p.ej. FNAC) es tan simple como añadir una entrada aquí,
pero NO es obligatorio: si el dominio es desconocido, el scraper usa las
estrategias genéricas (JSON-LD / meta tags), que funcionan en muchísimas webs.
"""
from __future__ import annotations

from urllib.parse import urlparse

# Selectores específicos por dominio (los más estables que se conocen).
STORE_REGISTRY: dict[str, dict] = {
    "amazon": {
        "name": "Amazon",
        "selectors": [
            "span.a-price span.a-offscreen",
            "#corePrice_feature_div span.a-offscreen",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
        ],
    },
    "mediamarkt": {
        "name": "MediaMarkt",
        "selectors": [
            'span[data-test="branded-price-whole-value"]',
            'div[data-test="mms-price"]',
            'meta[itemprop="price"]',
        ],
    },
    "pccomponentes": {
        "name": "PcComponentes",
        "selectors": [
            "#priceBlock #precio-main",
            'span[data-price]',
            'meta[itemprop="price"]',
        ],
    },
    "leroymerlin": {
        "name": "Leroy Merlin",
        "selectors": [
            'span.m-price__value',
            'meta[itemprop="price"]',
        ],
    },
    "fnac": {
        "name": "FNAC",
        "selectors": [
            "span.userPrice",
            "span.f-priceBox-price",
            'meta[itemprop="price"]',
        ],
    },
    "elcorteingles": {
        "name": "El Corte Inglés",
        "selectors": [
            'span[data-test="prices-sale"]',
            "p.price-amount",
            'meta[itemprop="price"]',
        ],
    },
    "carrefour": {
        "name": "Carrefour",
        "selectors": [
            "span.buybox__price",
            'meta[itemprop="price"]',
        ],
    },
    "elotrolado": {
        "name": "Aliexpress",
        "selectors": ['meta[itemprop="price"]'],
    },
    "aliexpress": {
        "name": "AliExpress",
        "selectors": [
            "div.product-price-value",
            'meta[itemprop="price"]',
        ],
    },
}

# Selectores genéricos que prueban como último recurso en cualquier web.
GENERIC_SELECTORS: list[str] = [
    'meta[property="product:price:amount"]',
    'meta[property="og:price:amount"]',
    'meta[itemprop="price"]',
    '[itemprop="price"]',
    '[data-price]',
    '.price',
    '.product-price',
    '.current-price',
]


def normalize_domain(url: str) -> str:
    """Devuelve el dominio base sin 'www.' ni TLD compuesto.

    Ej: 'https://www.amazon.es/dp/123' -> 'amazon'
    """
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    parts = netloc.split(".")
    if len(parts) >= 2:
        return parts[0]
    return netloc or url


def lookup_store(url: str) -> dict | None:
    """Devuelve la entrada del registro para la URL, o None si es desconocida."""
    return STORE_REGISTRY.get(normalize_domain(url))


def infer_store_name(url: str) -> str:
    """Nombre legible de la tienda a partir de la URL."""
    entry = lookup_store(url)
    if entry:
        return entry["name"]
    domain = normalize_domain(url)
    return domain.capitalize() if domain else "Tienda"


def candidate_selectors(url: str, user_selector: str | None) -> list[str]:
    """Lista ordenada de selectores CSS a probar para una URL."""
    selectors: list[str] = []
    if user_selector:
        selectors.append(user_selector)
    entry = lookup_store(url)
    if entry:
        selectors.extend(entry.get("selectors", []))
    selectors.extend(GENERIC_SELECTORS)
    # Elimina duplicados conservando orden.
    seen: set[str] = set()
    unique: list[str] = []
    for sel in selectors:
        if sel not in seen:
            seen.add(sel)
            unique.append(sel)
    return unique


def is_valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
