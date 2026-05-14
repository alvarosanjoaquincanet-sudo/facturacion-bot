"""
ocr_utils.py — Extracción de datos de facturas mediante pytesseract.
Detecta proveedor, fecha, total y NIF de una imagen (jpg/png/webp).
"""

import re
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pytesseract

logger = logging.getLogger(__name__)

# Configuración pytesseract: columna de texto variable, mejor para tiquets
_TESSERACT_CONFIG = "--psm 4 --oem 3"


# ─────────────────────────────────────────────
#  Preprocesamiento de imagen
# ─────────────────────────────────────────────

def _preprocesar(imagen: Image.Image) -> Image.Image:
    """
    Mejora la imagen antes del OCR:
    1. Escala de grises
    2. Amplía si es pequeña (mínimo 1400 px de ancho)
    3. Autocontraste
    4. Aumenta contraste y nitidez
    """
    img = imagen.convert("L")  # escala de grises

    ancho, alto = img.size
    if ancho < 1400:
        factor = 1400 / ancho
        img = img.resize(
            (int(ancho * factor), int(alto * factor)),
            Image.LANCZOS,
        )

    img = ImageOps.autocontrast(img, cutoff=2)
    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = ImageEnhance.Sharpness(img).enhance(2.5)
    img = img.filter(ImageFilter.SHARPEN)

    return img


# ─────────────────────────────────────────────
#  Funciones de extracción individuales
# ─────────────────────────────────────────────

def _extraer_lineas(ruta_imagen: str) -> List[str]:
    """Preprocesa la imagen y devuelve las líneas de texto detectadas."""
    imagen = Image.open(ruta_imagen)
    imagen = _preprocesar(imagen)
    texto  = pytesseract.image_to_string(imagen, lang="spa+eng",
                                         config=_TESSERACT_CONFIG)
    return [l.strip() for l in texto.split("\n") if l.strip()]


def _extraer_proveedor(lineas: List[str]) -> str:
    """
    Heurística para el nombre del proveedor/razón social:
    1. Ignora líneas muy cortas o con solo números/símbolos.
    2. Prioriza líneas en MAYÚSCULAS en las primeras 8 líneas.
    3. Fallback: primera línea con al menos 3 letras.
    """
    for linea in lineas[:8]:
        if len(linea) < 3:
            continue
        letras = [c for c in linea if c.isalpha()]
        if len(letras) < 3:
            continue
        ratio_may = sum(1 for c in letras if c.isupper()) / len(letras)
        if ratio_may >= 0.5:
            return linea.strip("*-=_. ")

    for linea in lineas[:8]:
        letras = [c for c in linea if c.isalpha()]
        if len(letras) >= 3:
            return linea.strip("*-=_. ")

    return lineas[0].strip("*-=_. ") if lineas else ""


def _extraer_fecha(lineas: List[str]) -> str:
    """
    Reconoce formatos de fecha comunes en tickets españoles:
    dd/mm/aaaa  dd-mm-aaaa  aaaa-mm-dd  dd/mm/aa
    """
    patrones = [
        (r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b", 0, 1, 2, False),
        (r"\b(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})\b", 2, 1, 0, False),
        (r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2})\b",  0, 1, 2, True),
    ]
    texto = " ".join(lineas)
    for patron, pi_d, pi_m, pi_a, anio_corto in patrones:
        m = re.search(patron, texto)
        if m:
            grupos = m.groups()
            try:
                anio = int(grupos[pi_a])
                if anio_corto:
                    anio += 2000
                dia, mes = int(grupos[pi_d]), int(grupos[pi_m])
                return datetime(anio, mes, dia).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                continue
    return datetime.now().strftime("%Y-%m-%d")


def _extraer_total(lineas: List[str]) -> Optional[float]:
    """
    Extrae el importe TOTAL final (con impuestos).
    Prioridad:
      1. Líneas con keywords de total final ("total a pagar", "importe total"…)
      2. Líneas con "total" excluyendo subtotal → devuelve el mayor
      3. Último importe con símbolo de moneda en el ticket
    Maneja tanto punto como coma decimal (formato español).
    """
    def _parse(txt: str) -> Optional[float]:
        # Normalizar: quitar símbolos monetarios y espacios
        txt = re.sub(r"[€$£\s]", "", txt)
        # Formato español: 1.234,56 → 1234.56
        if re.search(r"\d\.\d{3},\d", txt):
            txt = txt.replace(".", "").replace(",", ".")
        else:
            txt = txt.replace(",", ".")
        # Si quedan varios puntos, el último es decimal
        partes = txt.split(".")
        if len(partes) > 2:
            txt = "".join(partes[:-1]) + "." + partes[-1]
        try:
            v = float(txt)
            return v if 0 < v < 1_000_000 else None
        except ValueError:
            return None

    # Patrón: número con posibles separadores de miles/decimales
    pat_num = r"([\d]+[\d\.,\s]*)"

    # ── Prioridad 1: keywords de total final ──────────────────────────
    kw_final = [
        "total a pagar", "total con iva", "total con impuesto",
        "importe total", "total factura", "a pagar", "total eur",
        "total usd", "grand total", "amount due", "total due",
        "total neto", "total €", "total:",
    ]
    for linea in lineas:
        ll = linea.lower()
        if any(kw in ll for kw in kw_final):
            for m in re.finditer(pat_num, linea):
                v = _parse(m.group(1))
                if v is not None:
                    return v

    # ── Prioridad 2: "total" o "importe" sin ser subtotal ────────────
    candidatos: List[float] = []
    for linea in lineas:
        ll = linea.lower()
        if "subtotal" in ll or "base imp" in ll or "base imponible" in ll:
            continue
        if "total" in ll or "importe" in ll:
            for m in re.finditer(pat_num, linea):
                v = _parse(m.group(1))
                if v is not None:
                    candidatos.append(v)
    if candidatos:
        return max(candidatos)

    # ── Prioridad 3: último importe con símbolo de moneda ────────────
    todos = []
    texto_completo = " ".join(lineas)
    for pat in [r"([\d\.,]+)\s*€", r"\$\s*([\d\.,]+)", r"([\d\.,]+)\s*EUR"]:
        for m in re.finditer(pat, texto_completo, re.IGNORECASE):
            v = _parse(m.group(1))
            if v is not None:
                todos.append(v)
    if todos:
        return max(todos)

    return None


def _extraer_nif(lineas: List[str]) -> str:
    """
    Extrae el NIF/CIF/NIE del establecimiento emisor.
    CIF (empresas):   letra + 7 dígitos + letra/dígito
    NIF (personas):   8 dígitos + letra
    NIE (extranjeros): X/Y/Z + 7 dígitos + letra
    """
    texto = " ".join(lineas).upper()
    patrones = [
        r'\b[ABCDEFGHJKLMNPQRSUVW]\d{7}[A-J0-9]\b',  # CIF
        r'\b\d{8}[A-HJ-NP-TV-Z]\b',                   # NIF
        r'\b[XYZ]\d{7}[A-HJ-NP-TV-Z]\b',              # NIE
    ]
    for patron in patrones:
        m = re.search(patron, texto)
        if m:
            return m.group(0)
    return ""


# ─────────────────────────────────────────────
#  Función pública principal
# ─────────────────────────────────────────────

def extraer_datos_factura(ruta_imagen: str) -> Dict[str, Any]:
    """
    Extrae proveedor, fecha, total y NIF de la imagen indicada.
    Devuelve dict con claves:
        proveedor (str), fecha (str), total (float|None),
        nif (str), exito (bool), texto_raw (list[str])
    """
    resultado: Dict[str, Any] = {
        "proveedor": "",
        "fecha": datetime.now().strftime("%Y-%m-%d"),
        "total": None,
        "nif": "",
        "exito": False,
        "texto_raw": [],
    }

    try:
        logger.info("OCR iniciado: %s", ruta_imagen)
        lineas = _extraer_lineas(ruta_imagen)

        if not lineas:
            logger.warning("OCR no encontró texto en la imagen.")
            return resultado

        logger.info("Líneas detectadas (%d): %s", len(lineas), lineas[:10])

        resultado["texto_raw"] = lineas
        resultado["proveedor"] = _extraer_proveedor(lineas)
        resultado["fecha"]     = _extraer_fecha(lineas)
        resultado["total"]     = _extraer_total(lineas)
        resultado["nif"]       = _extraer_nif(lineas)
        resultado["exito"]     = bool(
            resultado["proveedor"] and resultado["total"] is not None
        )

        logger.info(
            "OCR → proveedor='%s' | nif='%s' | fecha='%s' | total=%s",
            resultado["proveedor"], resultado["nif"],
            resultado["fecha"], resultado["total"],
        )

    except Exception as exc:
        logger.error("Error en OCR: %s", exc, exc_info=True)

    return resultado


def extraer_texto_crudo(ruta_imagen: str) -> List[str]:
    """Devuelve las líneas detectadas sin procesamiento adicional."""
    try:
        return _extraer_lineas(ruta_imagen)
    except Exception as exc:
        logger.error("Error extrayendo texto: %s", exc)
        return []
