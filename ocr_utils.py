"""
ocr_utils.py — Extracción de datos de facturas mediante pytesseract.
Detecta proveedor, fecha y total de una imagen (jpg/png/webp).
"""

import re
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from PIL import Image
import pytesseract

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  Funciones de extracción individuales
# ─────────────────────────────────────────────

def _extraer_lineas(ruta_imagen: str) -> List[str]:
    """Lee la imagen y devuelve las líneas de texto detectadas."""
    imagen = Image.open(ruta_imagen)
    texto = pytesseract.image_to_string(imagen, lang="spa+eng")
    return [linea.strip() for linea in texto.split("\n") if linea.strip()]


def _extraer_proveedor(lineas: List[str]) -> str:
    """
    Heurística: primera línea con >50 % de letras mayúsculas entre las
    primeras 6 líneas (típico en encabezados de ticket).
    Fallback: primera línea no vacía.
    """
    for linea in lineas[:6]:
        letras = [c for c in linea if c.isalpha()]
        if letras and sum(1 for c in letras if c.isupper()) / len(letras) >= 0.5:
            return linea
    return lineas[0] if lineas else ""


def _extraer_fecha(lineas: List[str]) -> str:
    """
    Reconoce varios formatos de fecha comunes en tickets:
    dd/mm/aaaa  dd-mm-aaaa  aaaa-mm-dd  dd/mm/aa
    Si no encuentra ninguna, devuelve la fecha actual.
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
    Extrae el importe TOTAL final (con impuestos incluidos).
    """
    def _parse_importe(texto: str) -> Optional[float]:
        texto = (texto.replace(" ", "").replace("$", "").replace("€", "")
                      .replace("£", "").replace(",", "."))
        partes = texto.split(".")
        if len(partes) > 2:
            texto = "".join(partes[:-1]) + "." + partes[-1]
        try:
            valor = float(texto)
            return valor if 0 < valor < 1_000_000 else None
        except ValueError:
            return None

    patron_importe = r"([\d]+[\d\s,\.]*)"

    keywords_total_final = [
        "total a pagar", "total con iva", "total con impuesto",
        "importe total", "total factura", "a pagar", "total €",
        "total eur", "total usd", "grand total", "amount due",
        "total due", "net total", "total neto",
    ]
    for linea in lineas:
        linea_lower = linea.lower()
        if any(kw in linea_lower for kw in keywords_total_final):
            m = re.search(patron_importe, linea)
            if m:
                valor = _parse_importe(m.group(1))
                if valor is not None:
                    return valor

    candidatos: List[float] = []
    for linea in lineas:
        linea_lower = linea.lower()
        if "subtotal" in linea_lower or "base imp" in linea_lower:
            continue
        if "total" in linea_lower or "importe" in linea_lower:
            for m in re.finditer(patron_importe, linea):
                valor = _parse_importe(m.group(1))
                if valor is not None:
                    candidatos.append(valor)

    if candidatos:
        return max(candidatos)

    texto_completo = " ".join(lineas)
    todos = []
    for pat in [r"\$\s*([\d,\.]+)", r"([\d,\.]+)\s*(?:€|£)"]:
        for m in re.finditer(pat, texto_completo):
            valor = _parse_importe(m.group(1))
            if valor is not None:
                todos.append(valor)
    if todos:
        return max(todos)

    return None


# ─────────────────────────────────────────────
#  Función pública principal
# ─────────────────────────────────────────────

def extraer_datos_factura(ruta_imagen: str) -> Dict[str, Any]:
    """
    Extrae proveedor, fecha y total de la imagen indicada.
    Siempre devuelve un dict con las claves:
        proveedor (str), fecha (str), total (float|None),
        exito (bool), texto_raw (list[str])
    """
    resultado: Dict[str, Any] = {
        "proveedor": "",
        "fecha": datetime.now().strftime("%Y-%m-%d"),
        "total": None,
        "exito": False,
        "texto_raw": [],
    }

    try:
        logger.info("OCR iniciado: %s", ruta_imagen)
        lineas = _extraer_lineas(ruta_imagen)

        if not lineas:
            logger.warning("OCR no encontró texto en la imagen.")
            return resultado

        logger.info("Líneas detectadas (%d): %s", len(lineas), lineas[:8])

        resultado["texto_raw"] = lineas
        resultado["proveedor"] = _extraer_proveedor(lineas)
        resultado["fecha"]     = _extraer_fecha(lineas)
        resultado["total"]     = _extraer_total(lineas)
        resultado["exito"]     = bool(resultado["proveedor"] and resultado["total"] is not None)

        logger.info(
            "OCR finalizado → proveedor='%s' | fecha='%s' | total=%s",
            resultado["proveedor"], resultado["fecha"], resultado["total"],
        )

    except Exception as exc:
        logger.error("Error en OCR: %s", exc, exc_info=True)

    return resultado


def extraer_texto_crudo(ruta_imagen: str) -> List[str]:
    """Devuelve las líneas de texto detectadas sin procesamiento adicional."""
    try:
        return _extraer_lineas(ruta_imagen)
    except Exception as exc:
        logger.error("Error extrayendo texto: %s", exc)
        return []
