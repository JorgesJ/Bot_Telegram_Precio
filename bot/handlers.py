"""Handlers de Telegram: comandos, menús con botones y conversaciones.

Estructura:
- BotHandlers agrupa los objetos compartidos (db, tracker, settings) y expone
  los handlers como métodos.
- Navegación pura (ver producto, comprobar, gráfica, borrar) -> CallbackQueryHandler.
- Flujos con texto (añadir producto, añadir tienda, fijar objetivo) -> ConversationHandler.

Convención de callback_data (prefijos disjuntos para no solaparse):
  nav_*      -> navegación de menús
  prod_*     -> acciones sobre un producto
  store_*    -> acciones sobre una tienda
  conv_*     -> entradas que arrancan una conversación
"""
from __future__ import annotations

import functools
import logging
from typing import Callable, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from . import formatting, quota, stores
from .charts import build_price_chart
from .config import Settings
from .database import Database
from .tracker import StoreCheck, Tracker

logger = logging.getLogger(__name__)

# Estados de conversación
(
    ADD_NAME,
    ADD_STORE_URL,
    ADDSTORE_URL,
    SET_TARGET,
) = range(4)


def _authorized(method: Callable):
    """Decorador: bloquea a usuarios no autorizados."""

    @functools.wraps(method)
    async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user is None or not self.settings.is_authorized(user.id):
            if update.callback_query:
                await update.callback_query.answer("No autorizado.", show_alert=True)
            elif update.message:
                await update.message.reply_text(
                    "🚫 No estás autorizado a usar este bot.\n"
                    f"Tu ID de Telegram es <code>{user.id if user else '?'}</code>. "
                    "Pídele al administrador que lo añada.",
                    parse_mode=ParseMode.HTML,
                )
            return None
        if self.db is not None and user is not None:
            self.db.upsert_user(user.id, user.username, user.first_name)
        return await method(self, update, context)

    return wrapper


class BotHandlers:
    def __init__(self, db: Database, tracker: Tracker, settings: Settings):
        self.db = db
        self.tracker = tracker
        self.settings = settings

    # ------------------------------------------------------------------ #
    # Registro en la aplicación
    # ------------------------------------------------------------------ #
    def register(self, app: Application) -> None:
        conv = ConversationHandler(
            entry_points=[
                CommandHandler("add", self.cmd_add),
                CallbackQueryHandler(self.cb_conv_add, pattern=r"^conv_add$"),
                CallbackQueryHandler(self.cb_conv_addstore, pattern=r"^conv_addstore:\d+$"),
                CallbackQueryHandler(self.cb_conv_target, pattern=r"^conv_target:\d+$"),
            ],
            states={
                ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_name)],
                ADD_STORE_URL: [
                    CommandHandler("done", self.add_store_done),
                    CallbackQueryHandler(self.conv_finish, pattern=r"^conv_finish$"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_store_url),
                ],
                ADDSTORE_URL: [
                    CommandHandler("done", self.addstore_done),
                    CallbackQueryHandler(self.conv_finish, pattern=r"^conv_finish$"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.addstore_url),
                ],
                SET_TARGET: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.set_target_value)
                ],
            },
            fallbacks=[CommandHandler("cancel", self.cmd_cancel)],
            allow_reentry=True,
            name="main_conversation",
        )
        app.add_handler(conv)
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CommandHandler("list", self.cmd_list))
        app.add_handler(CommandHandler("check", self.cmd_check_all))
        app.add_handler(CommandHandler("uso", self.cmd_usage))
        app.add_handler(CommandHandler("menu", self.cmd_menu))
        # Navegación por botones (patrones disjuntos de conv_*)
        app.add_handler(CallbackQueryHandler(self.on_callback, pattern=r"^(nav_|prod_|store_|target_).*"))

    # ------------------------------------------------------------------ #
    # Comandos básicos
    # ------------------------------------------------------------------ #
    @_authorized
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (
            "👋 <b>Rastreador de precios</b>\n\n"
            "Hago seguimiento del precio de tus productos en varias tiendas, "
            "los comparo cada día y te aviso si hay cambios, mínimos históricos "
            "o si se alcanza tu precio objetivo.\n\n"
            "Usa el menú o escribe /help para ver todos los comandos."
        )
        await self._reply(update, text, self._main_menu_kb())

    @_authorized
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (
            "<b>Comandos</b>\n"
            "/menu — menú principal\n"
            "/add — añadir un producto nuevo y sus tiendas\n"
            "/list — ver tus productos\n"
            "/check — comprobar precios ahora (todos)\n"
            "/uso — ver el consumo de la API de scraping\n"
            "/cancel — cancelar la operación actual\n"
            "/help — esta ayuda\n\n"
            "<b>Cómo funciona</b>\n"
            "1️⃣ Crea un producto (ej: «Cafetera Krups XXX»).\n"
            "2️⃣ Pégame las URLs del producto en cada tienda (Amazon, MediaMarkt, "
            "PcComponentes, Leroy Merlin, FNAC…).\n"
            "3️⃣ Cada día comparo los precios y te aviso de los cambios.\n\n"
            "Puedes añadir tiendas nuevas en cualquier momento (➕ Añadir tienda), "
            "fijar un 🎯 precio objetivo y ver la 📈 gráfica de evolución."
        )
        await self._reply(update, text, self._main_menu_kb())

    @_authorized
    async def cmd_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._show_list(update, context)

    @_authorized
    async def cmd_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._reply(update, "Menú principal:", self._main_menu_kb())

    @_authorized
    async def cmd_check_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        products = self.db.list_products(owner_id=user.id)
        if not products:
            await self._reply(update, "No tienes productos todavía. Usa /add.")
            return
        await self._reply(update, "⏳ Comprobando precios, dame unos segundos…")
        for product in products:
            report = await self.tracker.check_product(product.id)
            if report:
                await self._send(
                    context, user.id, formatting.format_report(report)
                )
        alert = quota.pop_alert(self.db, self.settings)
        if alert:
            await self._send(context, user.id, alert)

    @_authorized
    async def cmd_usage(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._reply(update, quota.usage_text(self.db, self.settings))

    @_authorized
    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.pop("product_id", None)
        await update.message.reply_text("Operación cancelada.", reply_markup=None)
        return ConversationHandler.END

    # ------------------------------------------------------------------ #
    # Conversación: añadir producto
    # ------------------------------------------------------------------ #
    @_authorized
    async def cmd_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "📦 ¿Cómo se llama el producto? (ej: «Cafetera Krups XXX»)\n"
            "Escribe /cancel para salir."
        )
        return ADD_NAME

    async def cb_conv_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not self.settings.is_authorized(user.id):
            await update.callback_query.answer("No autorizado.", show_alert=True)
            return ConversationHandler.END
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            "📦 ¿Cómo se llama el producto? (ej: «Cafetera Krups XXX»)\n"
            "Escribe /cancel para salir."
        )
        return ADD_NAME

    async def add_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        name = update.message.text.strip()
        if not name:
            await update.message.reply_text("Dame un nombre válido, por favor.")
            return ADD_NAME
        product_id = self.db.add_product(update.effective_user.id, name)
        context.user_data["product_id"] = product_id
        await update.message.reply_text(
            f"✅ Producto «{name}» creado.\n\n"
            "Ahora pégame la <b>URL del producto en la primera tienda</b> "
            "(Amazon, MediaMarkt, PcComponentes…).\n"
            "Cuando termines de añadir tiendas, pulsa ✅ Terminar y volver o escribe /done.",
            parse_mode=ParseMode.HTML,
            reply_markup=self._finish_kb(),
        )
        return ADD_STORE_URL

    async def add_store_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        product_id = context.user_data.get("product_id")
        if product_id is None:
            await update.message.reply_text("Algo falló, empieza de nuevo con /add.")
            return ConversationHandler.END
        await self._add_store_from_text(update, product_id)
        return ADD_STORE_URL

    async def add_store_done(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        product_id = context.user_data.pop("product_id", None)
        if product_id:
            product = self.db.get_product(product_id)
            await update.message.reply_text(
                formatting.format_product_detail(self.db, product),
                parse_mode=ParseMode.HTML,
                reply_markup=self._product_kb(product_id),
            )
        return ConversationHandler.END

    # ------------------------------------------------------------------ #
    # Conversación: añadir tienda a producto existente
    # ------------------------------------------------------------------ #
    async def cb_conv_addstore(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not self.settings.is_authorized(user.id):
            await update.callback_query.answer("No autorizado.", show_alert=True)
            return ConversationHandler.END
        product_id = int(update.callback_query.data.split(":")[1])
        context.user_data["product_id"] = product_id
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            "🔗 Pégame la URL del producto en la nueva tienda.\n"
            "Puedes pegar varias seguidas; pulsa ✅ Terminar y volver o escribe /done al acabar.",
            reply_markup=self._finish_kb(),
        )
        return ADDSTORE_URL

    async def addstore_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        product_id = context.user_data.get("product_id")
        if product_id is None:
            await update.message.reply_text("Algo falló, vuelve a intentarlo.")
            return ConversationHandler.END
        await self._add_store_from_text(update, product_id)
        return ADDSTORE_URL

    async def addstore_done(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        product_id = context.user_data.pop("product_id", None)
        if product_id:
            product = self.db.get_product(product_id)
            await update.message.reply_text(
                formatting.format_product_detail(self.db, product),
                parse_mode=ParseMode.HTML,
                reply_markup=self._product_kb(product_id),
            )
        return ConversationHandler.END

    async def conv_finish(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        product_id = context.user_data.pop("product_id", None)
        if product_id:
            product = self.db.get_product(product_id)
            await update.callback_query.message.reply_text(
                formatting.format_product_detail(self.db, product),
                parse_mode=ParseMode.HTML,
                reply_markup=self._product_kb(product_id),
            )
        return ConversationHandler.END

    async def _add_store_from_text(self, update: Update, product_id: int) -> None:
        url = update.message.text.strip()
        if not stores.is_valid_url(url):
            await update.message.reply_text(
                "❌ Eso no parece una URL válida (debe empezar por http/https). "
                "Inténtalo de nuevo o escribe /done."
            )
            return
        if not stores.is_supported(url):
            await update.message.reply_text(
                "🚫 <b>URL no aceptada en el scrapper.</b>\n"
                "Por ahora solo se admiten tiendas ya probadas: "
                "<b>Amazon, Delonghi, Tien21, MediaMarkt y FNAC</b>.\n"
                "Prueba con otra o pulsa ✅ Terminar y volver / escribe /done.",
                parse_mode=ParseMode.HTML,
                reply_markup=self._finish_kb(),
            )
            return
        store_name = stores.infer_store_name(url)
        store_id = self.db.add_store(product_id, store_name, url)
        msg = await update.message.reply_text(
            f"⏳ Añadida <b>{store_name}</b>. Leyendo precio inicial…",
            parse_mode=ParseMode.HTML,
        )
        check = await self.tracker.check_store(store_id)
        if check and check.ok:
            await msg.edit_text(
                f"✅ <b>{store_name}</b>: {formatting.fmt_price(check.new_price, check.store.currency)}\n"
                "Pega otra URL, pulsa ✅ Terminar y volver o escribe /done.",
                parse_mode=ParseMode.HTML,
                reply_markup=self._finish_kb(),
            )
        else:
            err = check.result.error if check else "desconocido"
            await msg.edit_text(
                f"⚠️ Añadida <b>{store_name}</b>, pero no pude leer el precio "
                f"({err}).\nLo reintentaré en el chequeo diario. Pega otra URL, "
                "pulsa ✅ Terminar y volver o escribe /done.",
                parse_mode=ParseMode.HTML,
                reply_markup=self._finish_kb(),
            )

    # ------------------------------------------------------------------ #
    # Conversación: fijar precio objetivo
    # ------------------------------------------------------------------ #
    async def cb_conv_target(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not self.settings.is_authorized(user.id):
            await update.callback_query.answer("No autorizado.", show_alert=True)
            return ConversationHandler.END
        product_id = int(update.callback_query.data.split(":")[1])
        context.user_data["product_id"] = product_id
        await update.callback_query.answer()
        product = self.db.get_product(product_id)
        if product is not None and product.target_price is not None:
            prompt = (
                f"🎯 Objetivo actual: {formatting.fmt_price(product.target_price)}.\n"
                "Envíame el <b>nuevo precio</b> (ej: 79,99)."
            )
        else:
            prompt = "🎯 Envíame el <b>precio</b> al que quieres que te avise (ej: 79,99)."
        await update.callback_query.message.reply_text(prompt, parse_mode=ParseMode.HTML)
        return SET_TARGET

    async def set_target_value(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        product_id = context.user_data.pop("product_id", None)
        if product_id is None:
            await update.message.reply_text("Algo falló, vuelve a intentarlo.")
            return ConversationHandler.END
        from .scraper import parse_price

        value = parse_price(update.message.text.strip())
        if value is None:
            await update.message.reply_text(
                "No entendí el precio. Envíame solo el número, ej: 79,99."
            )
            context.user_data["product_id"] = product_id
            return SET_TARGET
        self.db.set_target_price(product_id, value)
        await update.message.reply_text(
            f"🎯 Objetivo fijado en {formatting.fmt_price(value)}.\n"
            "Te avisaré en el chequeo diario cuando alguna tienda baje a ese precio o menos.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« Volver al producto", callback_data=f"prod_view:{product_id}")]]
            ),
        )
        return ConversationHandler.END

    # ------------------------------------------------------------------ #
    # Navegación por botones
    # ------------------------------------------------------------------ #
    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user = update.effective_user
        if not self.settings.is_authorized(user.id):
            await query.answer("No autorizado.", show_alert=True)
            return
        self.db.upsert_user(user.id, user.username, user.first_name)
        data = query.data
        await query.answer()

        if data == "nav_menu":
            await query.edit_message_text(
                "Menú principal:", reply_markup=self._main_menu_kb()
            )
        elif data == "nav_list":
            await self._show_list(update, context, edit=True)
        elif data == "nav_help":
            await self.cmd_help(update, context)
        elif data == "nav_usage":
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("« Menú principal", callback_data="nav_menu")]]
            )
            await query.edit_message_text(
                quota.usage_text(self.db, self.settings),
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        elif data.startswith("prod_view:"):
            await self._show_product(update, int(data.split(":")[1]))
        elif data.startswith("prod_check:"):
            await self._do_check(update, context, int(data.split(":")[1]))
        elif data.startswith("prod_chart:"):
            await self._do_chart(update, context, int(data.split(":")[1]))
        elif data.startswith("prod_stores:"):
            await self._show_stores(update, int(data.split(":")[1]))
        elif data.startswith("prod_del:"):
            pid = int(data.split(":")[1])
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("✅ Sí, eliminar", callback_data=f"prod_delok:{pid}"),
                        InlineKeyboardButton("❌ No", callback_data=f"prod_view:{pid}"),
                    ]
                ]
            )
            await query.edit_message_text("¿Eliminar este producto y su histórico?", reply_markup=kb)
        elif data.startswith("prod_delok:"):
            self.db.delete_product(int(data.split(":")[1]))
            await self._show_list(update, context, edit=True, prefix="🗑 Producto eliminado.\n\n")
        elif data.startswith("store_del:"):
            _, sid, pid = data.split(":")
            self.db.delete_store(int(sid))
            await self._show_stores(update, int(pid), prefix="🗑 Tienda eliminada.\n\n")
        elif data.startswith("target_menu:"):
            await self._show_target_menu(update, int(data.split(":")[1]))
        elif data.startswith("target_clear:"):
            pid = int(data.split(":")[1])
            self.db.set_target_price(pid, None)
            await self._show_target_menu(update, pid)

    # ------------------------------------------------------------------ #
    # Vistas
    # ------------------------------------------------------------------ #
    async def _show_list(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False, prefix: str = ""
    ):
        user = update.effective_user
        products = self.db.list_products(owner_id=user.id)
        if not products:
            text = prefix + "No tienes productos todavía.\nUsa ➕ para añadir el primero."
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("➕ Añadir producto", callback_data="conv_add")],
                    [InlineKeyboardButton("« Menú principal", callback_data="nav_menu")],
                ]
            )
        else:
            lines = [prefix + "<b>Tus productos:</b>", ""]
            for p in products:
                lines.append(formatting.format_product_list_line(self.db, p))
            text = "\n".join(lines)
            buttons = [
                [InlineKeyboardButton(f"📦 {p.name}", callback_data=f"prod_view:{p.id}")]
                for p in products
            ]
            buttons.append([InlineKeyboardButton("➕ Añadir producto", callback_data="conv_add")])
            buttons.append([InlineKeyboardButton("« Menú principal", callback_data="nav_menu")])
            kb = InlineKeyboardMarkup(buttons)
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(
                text, parse_mode=ParseMode.HTML, reply_markup=kb
            )
        else:
            await self._reply(update, text, kb)

    async def _show_product(self, update: Update, product_id: int):
        product = self.db.get_product(product_id)
        query = update.callback_query
        if product is None:
            await query.answer("Producto no encontrado.", show_alert=True)
            return
        text = formatting.format_product_detail(self.db, product)
        kb = self._product_kb(product_id)
        # Si venimos de la gráfica (mensaje con foto), no se puede editar a texto:
        # enviamos un mensaje nuevo en ese caso.
        if query.message is not None and query.message.text is not None:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
            await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    async def _show_target_menu(self, update: Update, product_id: int):
        product = self.db.get_product(product_id)
        query = update.callback_query
        if product is None:
            await query.answer("Producto no encontrado.", show_alert=True)
            return
        back = InlineKeyboardButton("« Volver al producto", callback_data=f"prod_view:{product_id}")
        if product.target_price is not None:
            text = (
                f"🎯 <b>Precio objetivo</b>\n{product.name}\n\n"
                f"Actual: <b>{formatting.fmt_price(product.target_price)}</b>\n"
                "Te aviso cuando alguna tienda baje a ese precio o menos."
            )
            rows = [
                [InlineKeyboardButton("✏️ Modificar precio", callback_data=f"conv_target:{product_id}")],
                [InlineKeyboardButton("🗑 Quitar objetivo", callback_data=f"target_clear:{product_id}")],
                [back],
            ]
        else:
            text = (
                f"🎯 <b>Precio objetivo</b>\n{product.name}\n\n"
                "No hay objetivo fijado."
            )
            rows = [
                [InlineKeyboardButton("➕ Fijar precio objetivo", callback_data=f"conv_target:{product_id}")],
                [back],
            ]
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows)
        )

    async def _show_stores(self, update: Update, product_id: int, prefix: str = ""):
        product = self.db.get_product(product_id)
        store_list = self.db.list_stores(product_id)
        back = [InlineKeyboardButton("« Volver al producto", callback_data=f"prod_view:{product_id}")]
        if not store_list:
            text = prefix + f"🗑 <b>Eliminar tiendas de {product.name}</b>\n\nNo hay tiendas."
            await update.callback_query.edit_message_text(
                text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([back])
            )
            return
        text = prefix + (
            f"🗑 <b>Eliminar tiendas de {product.name}</b>\n\n"
            "Pulsa una tienda para eliminarla:"
        )
        buttons = [
            [
                InlineKeyboardButton(
                    f"🗑 {s.name} — {formatting.fmt_price(s.last_price, s.currency)}",
                    callback_data=f"store_del:{s.id}:{product_id}",
                )
            ]
            for s in store_list
        ]
        buttons.append(back)
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons)
        )

    async def _do_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
        query = update.callback_query
        await query.edit_message_text("⏳ Comprobando precios…")
        report = await self.tracker.check_product(product_id)
        if report is None:
            await query.edit_message_text("Producto no encontrado.")
            return
        await query.edit_message_text(
            formatting.format_report(report),
            parse_mode=ParseMode.HTML,
            reply_markup=self._product_kb(product_id),
        )
        alert = quota.pop_alert(self.db, self.settings)
        if alert:
            await context.bot.send_message(
                chat_id=update.effective_user.id, text=alert, parse_mode=ParseMode.HTML
            )

    async def _do_chart(self, update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
        query = update.callback_query
        product = self.db.get_product(product_id)
        if product is None:
            await query.answer("Producto no encontrado.", show_alert=True)
            return
        buffer = build_price_chart(self.db, product)
        if buffer is None:
            await query.answer(
                "Todavía no hay histórico suficiente para la gráfica.", show_alert=True
            )
            return
        chat_id = update.effective_user.id
        # Borra la gráfica anterior para quedarnos solo con la más reciente.
        prev_id = context.chat_data.get("last_chart_msg_id")
        if prev_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=prev_id)
            except Exception:  # noqa: BLE001 - si ya no existe o pasó de 48h, da igual
                pass
        sent = await context.bot.send_photo(
            chat_id=chat_id,
            photo=buffer,
            caption=f"📈 Evolución de precio · {product.name}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« Volver al producto", callback_data=f"prod_view:{product_id}")]]
            ),
        )
        context.chat_data["last_chart_msg_id"] = sent.message_id

    # ------------------------------------------------------------------ #
    # Teclados
    # ------------------------------------------------------------------ #
    @staticmethod
    def _finish_kb() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("✅ Terminar y volver", callback_data="conv_finish")]]
        )

    @staticmethod
    def _main_menu_kb() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📋 Mis productos", callback_data="nav_list")],
                [InlineKeyboardButton("➕ Añadir producto", callback_data="conv_add")],
                [InlineKeyboardButton("📊 Consumo API", callback_data="nav_usage")],
                [InlineKeyboardButton("❓ Ayuda", callback_data="nav_help")],
            ]
        )

    @staticmethod
    def _product_kb(product_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔄 Comprobar", callback_data=f"prod_check:{product_id}"),
                    InlineKeyboardButton("📈 Gráfica", callback_data=f"prod_chart:{product_id}"),
                ],
                [
                    InlineKeyboardButton("➕ Añadir tienda", callback_data=f"conv_addstore:{product_id}"),
                    InlineKeyboardButton("🗑 Eliminar tiendas", callback_data=f"prod_stores:{product_id}"),
                ],
                [
                    InlineKeyboardButton("🎯 Precio objetivo", callback_data=f"target_menu:{product_id}"),
                    InlineKeyboardButton("🗑 Eliminar producto", callback_data=f"prod_del:{product_id}"),
                ],
                [InlineKeyboardButton("« Mis productos", callback_data="nav_list")],
            ]
        )

    # ------------------------------------------------------------------ #
    # Utilidades de envío
    # ------------------------------------------------------------------ #
    async def _reply(self, update: Update, text: str, kb: Optional[InlineKeyboardMarkup] = None):
        target = update.message or (update.callback_query.message if update.callback_query else None)
        if target is not None:
            await target.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    async def _send(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str):
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
