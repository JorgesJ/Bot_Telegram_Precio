"""Scraper best-effort de precios.

Estrategia de extracción, por orden:
  1. Selector CSS configurado por el usuario (si lo hay).
  2. JSON-LD (schema.org/Product -> offers.price): muy fiable cuando existe.
  3. Selectores específicos del dominio.
  4. Selectores genéricos (meta tags, clases comunes).

El parsing de precios soporta formatos europeos y anglosajones:
  "1.299,99 €", "€1,299.99", "79,99", "1299.00", "1 299,99 €", etc.

Está pensado para fallar con elegancia: si una tienda no se puede leer,
se devuelve un ScrapeResult con ok=False y el motivo, sin romper el resto.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode, urlparse

import httpx
from selectolax.parser import HTMLParser

from . import stores

logger = logging.getLogger(__name__)

SCRAPERAPI_ENDPOINT = "https://api.scraperapi.com/"


def month_key() -> str:
    """Clave del mes actual (YYYY-MM) para el contador de la API."""
    return datetime.now().strftime("%Y-%m")

# User-agents para rotar y parecer un navegador real.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

_DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Cache-Control": "max-age=0",
}

# Palabras que sugieren que el producto NO está disponible.
_UNAVAILABLE_HINTS = (
    "agotado",
    "sin stock",
    "no disponible",
    "out of stock",
    "currently unavailable",
    "temporarily out of stock",
)


@dataclass
class ScrapeResult:
    url: str
    ok: bool
    price: Optional[float] = None
    currency: str = "EUR"
    available: bool = True
    method: str = ""          # cómo se obtuvo el precio (debug)
    error: Optional[str] = None


# --------------------------------------------------------------------------- #
# Parsing de precios (funciones puras, fáciles de testear)
# --------------------------------------------------------------------------- #
_PRICE_RE = re.compile(r"\d[\d.\s\u00a0,]*\d|\d")


def parse_price(text: str) -> Optional[float]:
    """Extrae un número de precio de un texto arbitrario.

    Detecta automáticamente si la coma o el punto es el separador decimal.
    Devuelve None si no encuentra nada parseable.
    """
    if text is None:
        return None
    text = str(text).strip()
    if not text:
        return None

    match = _PRICE_RE.search(text)
    if not match:
        return None

    raw = match.group(0)
    # Quita espacios (incl. no-break space) usados como separador de miles.
    raw = raw.replace("\u00a0", "").replace(" ", "")

    has_comma = "," in raw
    has_dot = "." in raw

    if has_comma and has_dot:
        # El último símbolo que aparece es el separador decimal.
        if raw.rfind(",") > raw.rfind("."):
            # Formato europeo: 1.299,99
            raw = raw.replace(".", "").replace(",", ".")
        else:
            # Formato anglosajón: 1,299.99
            raw = raw.replace(",", "")
    elif has_comma:
        # Solo coma: decide si es decimal (1,99) o miles (1,299)
        if re.search(r",\d{3}$", raw) and len(raw.split(",")[-1]) == 3:
            raw = raw.replace(",", "")  # miles
        else:
            raw = raw.replace(",", ".")  # decimal
    elif has_dot:
        # Solo punto: si parece miles (1.299) sin decimales de 1-2, quítalo.
        if re.search(r"\.\d{3}$", raw) and len(raw.split(".")[-1]) == 3:
            raw = raw.replace(".", "")

    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def detect_currency(text: str) -> str:
    if "€" in text or "eur" in text.lower():
        return "EUR"
    if "$" in text or "usd" in text.lower():
        return "USD"
    if "£" in text or "gbp" in text.lower():
        return "GBP"
    return "EUR"


# --------------------------------------------------------------------------- #
# Extracción desde HTML
# --------------------------------------------------------------------------- #
def _price_from_jsonld(tree: HTMLParser) -> Optional[tuple[float, str]]:
    """Busca offers.price en bloques JSON-LD de schema.org/Product."""
    for node in tree.css('script[type="application/ld+json"]'):
        content = node.text(strip=False)
        if not content:
            continue
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            continue
        for price, currency in _walk_jsonld_offers(data):
            parsed = parse_price(str(price))
            if parsed is not None:
                return parsed, currency or "EUR"
    return None


def _walk_jsonld_offers(data) -> list[tuple]:
    """Recorre recursivamente el JSON-LD recopilando (price, currency)."""
    found: list[tuple] = []

    def visit(obj):
        if isinstance(obj, dict):
            offers = obj.get("offers")
            if offers is not None:
                visit(offers)
            if "price" in obj:
                found.append((obj.get("price"), obj.get("priceCurrency")))
            if "lowPrice" in obj:
                found.append((obj.get("lowPrice"), obj.get("priceCurrency")))
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    visit(value)
        elif isinstance(obj, list):
            for item in obj:
                visit(item)

    visit(data)
    return found


def _price_from_selector(tree: HTMLParser, selector: str) -> Optional[tuple[float, str]]:
    node = tree.css_first(selector)
    if node is None:
        return None
    # Para <meta> el precio está en 'content'; si no, en el texto.
    raw = node.attributes.get("content") if node.tag == "meta" else None
    if not raw:
        raw = node.attributes.get("data-price") or node.text(strip=True)
    if not raw:
        return None
    price = parse_price(raw)
    if price is None:
        return None
    return price, detect_currency(raw)


def _looks_unavailable(html: str) -> bool:
    low = html.lower()
    return any(hint in low for hint in _UNAVAILABLE_HINTS)


def extract_price(html: str, url: str, user_selector: Optional[str]) -> ScrapeResult:
    """Extrae precio de un HTML ya descargado. Función pura (sin red)."""
    tree = HTMLParser(html)

    # 1 + 3 + 4) selectores (usuario, dominio, genéricos)
    user_sels = [user_selector] if user_selector else []
    for selector in user_sels:
        result = _price_from_selector(tree, selector)
        if result:
            price, currency = result
            return ScrapeResult(
                url=url, ok=True, price=price, currency=currency,
                available=True,
                method=f"selector:{selector}",
            )

    # 2) JSON-LD
    jsonld = _price_from_jsonld(tree)
    if jsonld:
        price, currency = jsonld
        return ScrapeResult(
            url=url, ok=True, price=price, currency=currency,
            available=True, method="json-ld",
        )

    # 3 + 4) selectores de dominio y genéricos
    for selector in stores.candidate_selectors(url, None):
        result = _price_from_selector(tree, selector)
        if result:
            price, currency = result
            return ScrapeResult(
                url=url, ok=True, price=price, currency=currency,
                available=True,
                method=f"selector:{selector}",
            )

    return ScrapeResult(
        url=url, ok=False,
        error="No se encontró el precio en la página (revisa la URL o añade un selector).",
    )


# --------------------------------------------------------------------------- #
# Descarga + extracción (con red)
# --------------------------------------------------------------------------- #
class Scraper:
    def __init__(
        self,
        timeout: float = 20.0,
        delay: float = 3.0,
        api_key: str = "",
        api_domains: Optional[set[str]] = None,
        api_budget: int = 1000,
        api_credits_per_request: int = 10,
        db=None,
    ):
        self.timeout = timeout
        self.delay = delay
        self.api_key = api_key
        self.api_domains = api_domains or set()
        self.api_budget = api_budget
        self.api_credits_per_request = api_credits_per_request
        self.db = db
        self._ua_index = 0

    def _headers(self, url: Optional[str] = None) -> dict:
        ua = USER_AGENTS[self._ua_index % len(USER_AGENTS)]
        self._ua_index += 1
        headers = {**_DEFAULT_HEADERS, "User-Agent": ua}
        if url:
            parsed = urlparse(url)
            headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
        return headers

    def _use_api(self, url: str) -> bool:
        return bool(self.api_key) and stores.normalize_domain(url) in self.api_domains

    async def fetch(self, url: str, user_selector: Optional[str] = None) -> ScrapeResult:
        """Descarga la URL y extrae el precio. Nunca lanza excepción."""
        if not stores.is_valid_url(url):
            return ScrapeResult(url=url, ok=False, error="URL inválida.")
        if self._use_api(url):
            return await self._fetch_via_api(url, user_selector)
        last_status: Optional[int] = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    follow_redirects=True,
                    headers=self._headers(url),
                ) as client:
                    resp = await client.get(url)
                if resp.status_code in (403, 429) and attempt == 0:
                    last_status = resp.status_code
                    await asyncio.sleep(1.5)
                    continue
                resp.raise_for_status()
                return extract_price(resp.text, url, user_selector)
            except httpx.HTTPStatusError as exc:
                return ScrapeResult(
                    url=url, ok=False,
                    error=f"La tienda respondió con error HTTP {exc.response.status_code}.",
                )
            except httpx.RequestError as exc:
                return ScrapeResult(
                    url=url, ok=False, error=f"No se pudo conectar: {exc!s}"
                )
            except Exception as exc:  # noqa: BLE001 - best effort, nunca romper
                logger.exception("Error inesperado scrapeando %s", url)
                return ScrapeResult(url=url, ok=False, error=f"Error inesperado: {exc!s}")
        return ScrapeResult(
            url=url, ok=False,
            error=f"La tienda respondió con error HTTP {last_status}.",
        )

    async def _fetch_via_api(
        self, url: str, user_selector: Optional[str]
    ) -> ScrapeResult:
        """Descarga vía ScraperAPI (IP residencial) controlando el presupuesto."""
        month = month_key()
        cost = self.api_credits_per_request
        if self.db is not None:
            used = self.db.get_month_credits(month)
            if used + cost > self.api_budget:
                return ScrapeResult(
                    url=url, ok=False,
                    error="⏸️ Límite mensual de la API de scraping alcanzado. "
                    "Se reanudará automáticamente el mes que viene.",
                )
        params = {
            "api_key": self.api_key,
            "url": url,
            "premium": "true",
            "render": "true",
            "country_code": "es",
        }
        api_url = f"{SCRAPERAPI_ENDPOINT}?{urlencode(params)}"
        api_timeout = max(self.timeout, 70.0)
        try:
            async with httpx.AsyncClient(timeout=api_timeout) as client:
                resp = await client.get(api_url)
        except httpx.RequestError as exc:
            return ScrapeResult(
                url=url, ok=False, error=f"No se pudo conectar con la API: {exc!s}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error inesperado con la API scrapeando %s", url)
            return ScrapeResult(url=url, ok=False, error=f"Error inesperado: {exc!s}")

        if resp.status_code != 200:
            return ScrapeResult(
                url=url, ok=False,
                error=f"La API respondió con error HTTP {resp.status_code}.",
            )
        if self.db is not None:
            self.db.add_month_credits(month, cost)
        return extract_price(resp.text, url, user_selector)

    async def fetch_many(self, items: list[tuple[str, Optional[str]]]) -> list[ScrapeResult]:
        """Scrapea varias (url, selector) en serie con un pequeño delay."""
        results: list[ScrapeResult] = []
        for i, (url, selector) in enumerate(items):
            results.append(await self.fetch(url, selector))
            if i < len(items) - 1 and self.delay > 0:
                await asyncio.sleep(self.delay)
        return results
