# 🛒 Bot de Telegram rastreador de precios

Bot de Telegram que sigue el precio de tus productos en **varias tiendas online**
(Amazon, MediaMarkt, PcComponentes, Leroy Merlin, FNAC, El Corte Inglés…),
los compara **cada 24 h** y te avisa de los cambios. Guarda el **histórico
completo** (mínimo y máximo) y permite **añadir tiendas nuevas** en cualquier
momento.

## ✨ Funcionalidades

- 📦 **Multiproducto y multitienda**: sigue todos los productos que quieras, cada
  uno con tantas tiendas como quieras.
- 🔁 **Chequeo diario automático** + chequeo manual bajo demanda (botón 🔄).
- 🚨 **Alertas de cambio de precio**: te dice qué tienda subió o bajó y cuánto.
- 💎 **Detección de mínimo histórico** ("chollo").
- 🎯 **Precio objetivo**: te avisa cuando una tienda baja del precio que fijes.
- 🏆 **Comparador**: muestra qué tienda tiene el mejor precio ahora mismo.
- 📈 **Gráfica de evolución** de precios por tienda (imagen PNG).
- ➕ **Añadir tiendas al vuelo**: si mañana el producto aparece en FNAC, pegas la
  URL y entra en el seguimiento diario.
- 📉📈 **Histórico**: guarda mínimo y máximo de cada tienda.
- 🔐 **Multiusuario** con lista blanca de IDs autorizados.

## 🧭 Cómo funciona (flujo de uso)

1. `/add` → le das un nombre al producto (ej: *Cafetera Krups XXX*).
2. Pegas las **URLs del producto en cada tienda**. El bot detecta la tienda por
   el dominio y lee el precio inicial.
3. Cada día (a la hora que configures) el bot revisa todas las tiendas y te
   manda un mensaje **solo si hay algo reseñable**.
4. En cualquier momento puedes ver el detalle (📦), comparar, pedir la 📈 gráfica,
   fijar un 🎯 objetivo o ➕ añadir tiendas.

## 🛠️ Requisitos

- Python 3.10+ (recomendado 3.12) **o** Docker.
- Un servidor encendido 24/7 (VPS, Raspberry Pi…). El chequeo diario necesita
  que el proceso esté vivo.
- Un token de bot de Telegram.

## 🚀 Puesta en marcha

### 1. Crear el bot en Telegram

1. Habla con [@BotFather](https://t.me/BotFather) y usa `/newbot`.
2. Copia el **token** que te da.
3. Para limitar quién puede usarlo, averigua tu **ID de Telegram** escribiendo a
   [@userinfobot](https://t.me/userinfobot).

### 2. Configurar

```bash
cp .env.example .env
# edita .env y rellena al menos TELEGRAM_BOT_TOKEN y (opcional) ALLOWED_USER_IDS
```

### 3a. Ejecutar directamente (con venv)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

### 3b. Ejecutar con Docker (recomendado para el VPS)

```bash
docker compose up -d --build
docker compose logs -f      # ver los logs
```

La base de datos se guarda en `./data/` (volumen), así que tu histórico
sobrevive a reinicios y actualizaciones.

### 3c. Ejecutar como servicio systemd

```bash
# instala en /opt/price-tracker-bot con su .venv, luego:
sudo cp deploy/price-tracker-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now price-tracker-bot
sudo journalctl -u price-tracker-bot -f
```

## 💬 Comandos del bot

| Comando | Descripción |
|---|---|
| `/start` | Bienvenida y menú principal |
| `/add` | Añadir un producto nuevo y sus tiendas |
| `/list` | Ver tus productos (con el mejor precio actual) |
| `/check` | Comprobar precios ahora mismo |
| `/cancel` | Cancelar la operación en curso |
| `/help` | Ayuda |

Casi todo se maneja también con **botones** dentro del chat.

## 🕷️ Sobre el scraping (lee esto)

El bot extrae el precio en este orden:

1. Un **selector CSS** que hayas configurado para esa tienda (opcional).
2. **JSON-LD** (`schema.org/Product`): lo exponen muchísimas tiendas y es lo más
   fiable.
3. **Selectores específicos** del dominio (ver `bot/stores.py`).
4. **Selectores genéricos** (`meta` de precio, clases comunes).

**Limitaciones honestas:**

- ⚠️ **Amazon** bloquea bots de forma agresiva. A veces funcionará y a veces no.
  Si necesitas Amazon de forma 100% fiable, lo ideal es integrar la API de
  [Keepa](https://keepa.com) o la Product Advertising API (queda como mejora
  futura).
- Si una tienda cambia su HTML, su selector puede dejar de funcionar. El bot **no
  se rompe**: marca esa tienda con ⚠️ y sigue con las demás. Puedes añadir un
  selector nuevo en `bot/stores.py`.
- Respeta a las tiendas: hay un retardo configurable entre peticiones
  (`SCRAPE_DELAY_SECONDS`) y se rota el *User-Agent*.

### Añadir una tienda nueva al catálogo

Edita `STORE_REGISTRY` en `bot/stores.py`:

```python
"fnac": {
    "name": "FNAC",
    "selectors": ["span.userPrice", 'meta[itemprop="price"]'],
},
```

No es obligatorio: aunque el dominio sea desconocido, el bot intentará leer el
precio con JSON-LD y los selectores genéricos.

## ⚙️ Configuración (`.env`)

| Variable | Por defecto | Descripción |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Token de @BotFather (**obligatorio**) |
| `ALLOWED_USER_IDS` | *(vacío)* | IDs autorizados separados por comas. Vacío = abierto |
| `DATABASE_PATH` | `data/price_tracker.db` | Ruta de la base de datos |
| `DAILY_CHECK_HOUR` / `DAILY_CHECK_MINUTE` | `9` / `0` | Hora del chequeo diario |
| `TIMEZONE` | `Europe/Madrid` | Zona horaria del scheduler |
| `SCRAPE_DELAY_SECONDS` | `3` | Segundos entre peticiones |
| `HTTP_TIMEOUT` | `20` | Timeout por petición |
| `LOG_LEVEL` | `INFO` | Nivel de log |

## 🧪 Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## 🗺️ Ideas para más adelante

- Integración con **Keepa** para Amazon fiable.
- Seguimiento de **stock/disponibilidad** como evento propio.
- **Export a CSV** del histórico.
- Comprobaciones más frecuentes que cada 24 h por producto.
- Compartir un producto entre varios usuarios.

## 🧱 Estructura del proyecto

```
price-tracker-bot/
├── bot/
│   ├── config.py       # configuración desde .env
│   ├── database.py     # SQLite: productos, tiendas, histórico
│   ├── stores.py       # registro de tiendas y selectores
│   ├── scraper.py      # descarga + parsing de precios (best-effort)
│   ├── tracker.py      # lógica: cambios, mínimos, objetivo, mejor precio
│   ├── charts.py       # gráfica de evolución (matplotlib)
│   ├── formatting.py   # formateo de mensajes de Telegram
│   ├── handlers.py     # comandos, menús y conversaciones
│   └── scheduler.py    # chequeo diario automático
├── main.py             # punto de entrada
├── tests/              # pruebas (pytest)
├── deploy/             # servicio systemd
├── Dockerfile
└── docker-compose.yml
```
