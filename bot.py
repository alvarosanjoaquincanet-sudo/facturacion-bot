"""
bot.py — Bot de Telegram para registro automático de facturas y tickets.

Flujo principal:
  1. Usuario envía foto → OCR extrae proveedor, fecha y total.
  2. Si faltan datos → se solicitan manualmente.
  3. Se muestra un teclado inline para elegir categoría.
  4. La factura se guarda en SQLite con número correlativo (F-0001…).

Ejecución:
  export TELEGRAM_TOKEN="tu_token"   (Linux/macOS)
  set TELEGRAM_TOKEN=tu_token        (Windows)
  python bot.py
"""

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from database import guardar_factura, init_db, obtener_siguiente_numero
from ocr_utils import extraer_datos_factura

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ─── Constantes ────────────────────────────────────────────────────────────────
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://127.0.0.1:8501")
IMAGENES_DIR  = Path(__file__).parent / "imagenes"

# Estados del ConversationHandler
(
    ESPERANDO_FOTO,
    SELECCIONANDO_CATEGORIA,
    CATEGORIA_MANUAL,
    MANUAL_PROVEEDOR,
    MANUAL_FECHA,
    MANUAL_TOTAL,
) = range(6)

CATEGORIAS = [
    ("🛒 Supermercado",       "Supermercado"),
    ("🍽️ Restaurante",        "Restaurante"),
    ("⚡ Servicios",          "Servicios"),
    ("📦 Otros",              "Otros"),
    ("✏️ Escribir manualmente", "MANUAL"),
]


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _teclado_categorias() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(txt, callback_data=val)] for txt, val in CATEGORIAS]
    )


def _resumen_datos(ctx: ContextTypes.DEFAULT_TYPE) -> str:
    ud = ctx.user_data
    total_str = f"${ud['total']:.2f}" if ud.get("total") is not None else "—"
    return (
        f"🏪 *Proveedor:* {ud.get('proveedor') or '—'}\n"
        f"📅 *Fecha:*     {ud.get('fecha') or '—'}\n"
        f"💰 *Total:*     {total_str}"
    )


async def _pedir_proveedor(update: Update) -> None:
    await update.effective_message.reply_text(
        "📝 Ingresa el *nombre del proveedor o establecimiento*:",
        parse_mode="Markdown",
    )


async def _pedir_fecha(update: Update) -> None:
    await update.effective_message.reply_text(
        "📅 ¿Cuál es la *fecha* de la factura?\n"
        "Formato: `DD/MM/AAAA` — o escribe `hoy` para la fecha actual.",
        parse_mode="Markdown",
    )


async def _pedir_total(update: Update) -> None:
    await update.effective_message.reply_text(
        "💰 ¿Cuál es el *total* de la factura?\n"
        "Ingresa solo el número, ej: `1234.50`",
        parse_mode="Markdown",
    )


async def _mostrar_categoria_teclado(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        f"✅ *Datos de la factura:*\n\n{_resumen_datos(ctx)}\n\n"
        "Selecciona la *categoría*:",
        parse_mode="Markdown",
        reply_markup=_teclado_categorias(),
    )


# ─── Comandos ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "👋 *Bienvenido al Sistema de Facturación*\n\n"
        "Envíame una foto de tu factura o ticket y yo me encargo del resto:\n\n"
        "1️⃣ Envía la *foto* de la factura\n"
        "2️⃣ Verifico y extraigo los datos con OCR\n"
        "3️⃣ Selecciona la categoría\n"
        "4️⃣ ¡La factura queda guardada con número único!\n\n"
        "📊 /dashboard → enlace al panel de control\n"
        "❌ /cancel    → cancelar en cualquier momento\n\n"
        "Cuando estés listo, *envía la foto*. 👇",
        parse_mode="Markdown",
    )
    return ESPERANDO_FOTO


async def cmd_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"📊 *Dashboard de Facturación*\n\n"
        f"🔗 {DASHBOARD_URL}\n\n"
        f"_(Ejecuta `streamlit run dashboard.py` para iniciarlo)_",
        parse_mode="Markdown",
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Operación cancelada.\n\nEnvía una foto para empezar de nuevo."
    )
    return ESPERANDO_FOTO


# ─── Recepción de foto ─────────────────────────────────────────────────────────

async def recibir_foto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Descarga la imagen y ejecuta OCR; enruta según el resultado."""
    IMAGENES_DIR.mkdir(parents=True, exist_ok=True)

    procesando = await update.effective_message.reply_text(
        "⏳ *Procesando imagen…* Por favor espera.",
        parse_mode="Markdown",
    )

    ruta_temp = ""
    try:
        # Soporta fotos comprimidas y documentos/imágenes sin comprimir
        if update.message.photo:
            file_obj = await context.bot.get_file(update.message.photo[-1].file_id)
            ext = ".jpg"
        else:
            doc = update.message.document
            file_obj = await context.bot.get_file(doc.file_id)
            nombre = doc.file_name or "imagen.jpg"
            ext = Path(nombre).suffix.lower() or ".jpg"

        ruta_temp = str(IMAGENES_DIR / f"temp_{file_obj.file_id}{ext}")
        await file_obj.download_to_drive(ruta_temp)
        logger.info("Imagen descargada: %s", ruta_temp)

        datos = extraer_datos_factura(ruta_temp)

        context.user_data.update(
            {
                "imagen_temp": ruta_temp,
                "proveedor":   datos["proveedor"],
                "fecha":       datos["fecha"] or datetime.now().strftime("%Y-%m-%d"),
                "total":       datos["total"],
            }
        )

        await procesando.delete()

        tiene_proveedor = bool(context.user_data["proveedor"])
        tiene_total     = context.user_data["total"] is not None

        if tiene_proveedor and tiene_total:
            await _mostrar_categoria_teclado(update, context)
            return SELECCIONANDO_CATEGORIA

        # Datos incompletos → flujo manual
        campos_faltantes = []
        if not tiene_proveedor:
            campos_faltantes.append("proveedor")
        if not tiene_total:
            campos_faltantes.append("total")

        await update.effective_message.reply_text(
            "⚠️ *No pude extraer todos los datos automáticamente.*\n\n"
            f"Lo que encontré:\n{_resumen_datos(context)}\n\n"
            f"Faltan: *{', '.join(campos_faltantes)}*\n\n"
            "Completaremos los datos manualmente. 📝",
            parse_mode="Markdown",
        )

        if not tiene_proveedor:
            await _pedir_proveedor(update)
            return MANUAL_PROVEEDOR

        # Tiene proveedor pero no total
        await _pedir_total(update)
        return MANUAL_TOTAL

    except Exception as exc:
        logger.error("Error procesando foto: %s", exc, exc_info=True)
        await procesando.delete()
        if ruta_temp:
            context.user_data["imagen_temp"] = ruta_temp
        await update.effective_message.reply_text(
            "❌ *Error al procesar la imagen.* Ingresaremos los datos manualmente.",
            parse_mode="Markdown",
        )
        await _pedir_proveedor(update)
        return MANUAL_PROVEEDOR


async def mensaje_no_foto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "📸 Por favor envía una *foto* de tu factura.\n"
        "Usa /cancel para cancelar.",
        parse_mode="Markdown",
    )
    return ESPERANDO_FOTO


# ─── Flujo de entrada manual ───────────────────────────────────────────────────

async def manual_proveedor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    proveedor = update.message.text.strip()
    if not proveedor:
        await update.message.reply_text("Por favor ingresa un nombre válido.")
        return MANUAL_PROVEEDOR

    context.user_data["proveedor"] = proveedor

    # Si ya tenemos total (parcialmente extraído por OCR), ir directo a categoría
    if context.user_data.get("total") is not None:
        await _mostrar_categoria_teclado(update, context)
        return SELECCIONANDO_CATEGORIA

    await _pedir_fecha(update)
    return MANUAL_FECHA


async def manual_fecha(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    texto = update.message.text.strip()

    if texto.lower() in ("hoy", "today"):
        fecha = datetime.now().strftime("%Y-%m-%d")
    else:
        formatos = ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y"]
        fecha = None
        for fmt in formatos:
            try:
                fecha = datetime.strptime(texto, fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue

        if not fecha:
            await update.message.reply_text(
                "❌ Formato no reconocido. Usa `DD/MM/AAAA` (ej: 15/05/2024) o escribe `hoy`.",
                parse_mode="Markdown",
            )
            return MANUAL_FECHA

    context.user_data["fecha"] = fecha
    await _pedir_total(update)
    return MANUAL_TOTAL


async def manual_total(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    texto = (
        update.message.text.strip()
        .replace(",", ".")
        .replace("$", "")
        .replace("€", "")
        .replace("£", "")
        .replace(" ", "")
    )

    try:
        total = float(texto)
        if total <= 0:
            raise ValueError("Total no positivo")
    except ValueError:
        await update.message.reply_text(
            "❌ Ingresa un número válido y positivo (ej: `1234.50`).",
            parse_mode="Markdown",
        )
        return MANUAL_TOTAL

    context.user_data["total"] = total
    await _mostrar_categoria_teclado(update, context)
    return SELECCIONANDO_CATEGORIA


# ─── Selección de categoría ────────────────────────────────────────────────────

async def seleccionar_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query: CallbackQuery = update.callback_query
    await query.answer()

    if query.data == "MANUAL":
        await query.edit_message_text("✏️ Escribe el nombre de la categoría:")
        return CATEGORIA_MANUAL

    context.user_data["categoria"] = query.data
    return await _guardar_y_confirmar(update, context)


async def categoria_manual_texto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    categoria = update.message.text.strip()
    if not categoria:
        await update.message.reply_text("Por favor ingresa una categoría válida.")
        return CATEGORIA_MANUAL

    context.user_data["categoria"] = categoria
    return await _guardar_y_confirmar(update, context)


async def _ignorar_texto_en_categoria(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    await update.message.reply_text(
        "Por favor selecciona una categoría usando los botones del mensaje anterior."
    )
    return SELECCIONANDO_CATEGORIA


# ─── Guardar factura ───────────────────────────────────────────────────────────

async def _guardar_y_confirmar(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Persiste la factura en BD, renombra la imagen y envía confirmación."""
    ud = context.user_data
    try:
        proveedor  = ud["proveedor"]
        fecha      = ud["fecha"]
        total      = float(ud["total"])
        categoria  = ud["categoria"]
        imagen_tmp = ud.get("imagen_temp", "")

        numero = obtener_siguiente_numero()

        # Mover imagen temporal al nombre definitivo
        imagen_final = ""
        if imagen_tmp and os.path.exists(imagen_tmp):
            ext          = Path(imagen_tmp).suffix or ".jpg"
            imagen_final = str(IMAGENES_DIR / f"{numero}{ext}")
            shutil.move(imagen_tmp, imagen_final)
            logger.info("Imagen guardada: %s", imagen_final)

        guardar_factura(
            numero      = numero,
            proveedor   = proveedor,
            fecha       = fecha,
            total       = total,
            categoria   = categoria,
            imagen_path = imagen_final,
        )

        await update.effective_message.reply_text(
            f"🎉 *¡Factura guardada exitosamente!*\n\n"
            f"📋 *Número:*    `{numero}`\n"
            f"🏪 *Proveedor:* {proveedor}\n"
            f"📅 *Fecha:*     {fecha}\n"
            f"💰 *Total:*     ${total:.2f}\n"
            f"🏷️ *Categoría:* {categoria}\n\n"
            "Envía otra foto para registrar una nueva factura.",
            parse_mode="Markdown",
        )

    except Exception as exc:
        logger.error("Error guardando factura: %s", exc, exc_info=True)
        # Limpiar imagen temporal huérfana
        img_tmp = ud.get("imagen_temp", "")
        if img_tmp and os.path.exists(img_tmp):
            try:
                os.remove(img_tmp)
            except OSError:
                pass
        await update.effective_message.reply_text(
            "❌ *Error al guardar la factura.* Por favor intenta de nuevo con /start.",
            parse_mode="Markdown",
        )

    context.user_data.clear()
    return ESPERANDO_FOTO


# ─── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise SystemExit(
            "❌ No se encontró TELEGRAM_TOKEN en las variables de entorno.\n"
            "  Linux/macOS: export TELEGRAM_TOKEN='tu_token'\n"
            "  Windows:     set TELEGRAM_TOKEN=tu_token"
        )

    init_db()
    IMAGENES_DIR.mkdir(parents=True, exist_ok=True)

    app = Application.builder().token(token).build()

    filtro_imagen = filters.PHOTO | filters.Document.IMAGE

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            MessageHandler(filtro_imagen, recibir_foto),
        ],
        states={
            ESPERANDO_FOTO: [
                MessageHandler(filtro_imagen, recibir_foto),
                MessageHandler(filters.TEXT & ~filters.COMMAND, mensaje_no_foto),
            ],
            SELECCIONANDO_CATEGORIA: [
                CallbackQueryHandler(seleccionar_categoria),
                MessageHandler(filters.TEXT & ~filters.COMMAND, _ignorar_texto_en_categoria),
            ],
            CATEGORIA_MANUAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, categoria_manual_texto),
            ],
            MANUAL_PROVEEDOR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, manual_proveedor),
            ],
            MANUAL_FECHA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, manual_fecha),
            ],
            MANUAL_TOTAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, manual_total),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("start",  cmd_start),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))

    logger.info("🚀 Bot iniciado. Esperando mensajes…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
