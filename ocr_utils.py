"""
ocr_utils.py — Extracción de datos de facturas.

Motor principal: Claude Vision (claude-haiku-4-5) — requiere ANTHROPIC_API_KEY.
Fallback:        pytesseract local si la API no está disponible.

Campos extraídos: nombre del negocio, NIF/CIF/NIE, fecha, importe con IVA.
"""

import base64
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pytesseract

logger = logging.getLogger(__name__)

_TESSERACT_CONFIG  = "--psm 4 --oem 3"
_ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


# ─────────────────────────────────────────────
#  Motor primario: Claude Vision
# ─────────────────────────────────────────────

def _extraer_con_claude(ruta_imagen: str) -> Optional[Dict[str, Any]]:
    """
    Envía la imagen a Claude Haiku y pide que devuelva los 4 campos en JSON.
    Devuelve None si no hay API key o si ocurre un error.
    """
    if not _ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY no configurada — usando pytesseract.")
        return None

    try:
        import anthropic  # importación diferida para no romper si no está instalado

        with open(ruta_imagen, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        ext = Path(ruta_imagen).suffix.lower()
        media_type = {
            ".jpg":  "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png":  "image/png",
            ".webp": "image/webp",
        }.get(ext, "image/jpeg")

        client = anthropic.Anthropic(api_key=_ANTHROPIC_API_KEY)

        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Analiza esta imagen de factura o ticket y extrae exactamente "
                            "estos 4 campos en formato JSON:\n\n"
                            "{\n"
                            '  "nombre_negocio": "razón social o nombre del establecimiento",\n'
                            '  "nif": "NIF/CIF/NIE del emisor sin espacios ni etiquetas, '
                            'vacío si no aparece",\n'
                            '  "fecha": "fecha en formato YYYY-MM-DD",\n'
                            '  "total": importe TOTAL con IVA incluido como número decimal '
                            "(usa punto como separador decimal), null si no aparece\n"
                            "}\n\n"
                            "Reglas importantes:\n"
                            "- nombre_negocio: el nombre del negocio/empresa que emite el "
                            "documento, tal como aparece.\n"
                            "- nif: solo el código alfanumérico (ej: B12345678, 12345678A, "
                            "X1234567A). Vacío si no es visible.\n"
                            "- fecha: la fecha del documento. Si no aparece, usa la fecha "
                            f"de hoy: {datetime.now().strftime('%Y-%m-%d')}.\n"
                            "- total: el importe FINAL con todos los impuestos incluidos. "
                            "NUNCA el subtotal ni la base imponible.\n\n"
                            "Responde ÚNICAMENTE con el JSON, sin texto adicional ni "
                            "bloques de código."
                        ),
                    },
                ],
            }],
        )

        texto = msg.content[0].text.strip()
        # Limpiar posibles bloques de código markdown
        texto = re.sub(r"^```[a-z]*\n?|\n?```$", "", texto, flags=re.MULTILINE).strip()

        datos = json.loads(texto)

        return {
            "proveedor": str(datos.get("nombre_negocio") or "").strip(),
            "nif":       str(datos.get("nif")            or "").strip().upper(),
            "fecha":     str(datos.get("fecha")          or datetime.now().strftime("%Y-%m-%d")).strip(),
            "total":     float(datos["total"]) if datos.get("total") is not None else None,
        }

    except Exception as exc:
        logger.error("Error Claude Vision: %s", exc, exc_info=True)
        return None


# ─────────────────────────────────────────────
#  Motor de respaldo: pytesseract
# ─────────────────────────────────────────────

def _preprocesar(imagen: Image.Image) -> Image.Image:
    img = imagen.convert("L")
    ancho, alto = img.size
    if ancho < 1400:
        factor = 1400 / ancho
        img = img.resize((int(ancho * factor), int(alto * factor)), Image.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=2)
    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = ImageEnhance.Sharpness(img).enhance(2.5)
    img = img.filter(ImageFilter.SHARPEN)
    return img


def _extraer_lineas(ruta_imagen: str) -> List[str]:
    imagen = _preprocesar(Image.open(ruta_imagen))
    texto  = pytesseract.image_to_string(imagen, lang="spa+eng",
                                         config=_TESSERACT_CONFIG)
    return [l.strip() for l in texto.split("\n") if l.strip()]


def _extraer_proveedor_tess(lineas: List[str]) -> str:
    for linea in lineas[:8]:
        letras = [c for c in linea if c.isalpha()]
        if len(letras) < 3:
            continue
        if sum(1 for c in letras if c.isupper()) / len(letras) >= 0.5:
            return linea.strip("*-=_. ")
    for linea in lineas[:8]:
        if len([c for c in linea if c.isalpha()]) >= 3:
            return linea.strip("*-=_. ")
    return lineas[0].strip("*-=_. ") if lineas else ""


def _extraer_fecha_tess(lineas: List[str]) -> str:
    patrones = [
        (r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b", 0, 1, 2, False),
        (r"\b(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})\b", 2, 1, 0, False),
        (r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2})\b",  0, 1, 2, True),
    ]
    texto = " ".join(lineas)
    for patron, pi_d, pi_m, pi_a, corto in patrones:
        m = re.search(patron, texto)
        if m:
            try:
                g = m.groups()
                anio = int(g[pi_a]) + (2000 if corto else 0)
                return datetime(anio, int(g[pi_m]), int(g[pi_d])).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                continue
    return datetime.now().strftime("%Y-%m-%d")


def _extraer_total_tess(lineas: List[str]) -> Optional[float]:
    def _parse(txt: str) -> Optional[float]:
        txt = re.sub(r"[€$£\s]", "", txt)
        if re.search(r"\d\.\d{3},\d", txt):
            txt = txt.replace(".", "").replace(",", ".")
        else:
            txt = txt.replace(",", ".")
        partes = txt.split(".")
        if len(partes) > 2:
            txt = "".join(partes[:-1]) + "." + partes[-1]
        try:
            v = float(txt)
            return v if 0 < v < 1_000_000 else None
        except ValueError:
            return None

    pat = r"([\d]+[\d\.,\s]*)"
    kw  = ["total a pagar", "total con iva", "importe total", "total factura",
           "a pagar", "total eur", "grand total", "amount due", "total:"]

    for linea in lineas:
        if any(k in linea.lower() for k in kw):
            for m in re.finditer(pat, linea):
                v = _parse(m.group(1))
                if v:
                    return v

    candidatos = []
    for linea in lineas:
        ll = linea.lower()
        if "subtotal" in ll or "base imp" in ll:
            continue
        if "total" in ll or "importe" in ll:
            for m in re.finditer(pat, linea):
                v = _parse(m.group(1))
                if v:
                    candidatos.append(v)
    if candidatos:
        return max(candidatos)

    todos = []
    for p in [r"([\d\.,]+)\s*€", r"\$\s*([\d\.,]+)"]:
        for m in re.finditer(p, " ".join(lineas), re.I):
            v = _parse(m.group(1))
            if v:
                todos.append(v)
    return max(todos) if todos else None


def _extraer_nif_tess(lineas: List[str]) -> str:
    texto = " ".join(lineas).upper()
    for pat in [
        r'\b[ABCDEFGHJKLMNPQRSUVW]\d{7}[A-J0-9]\b',
        r'\b\d{8}[A-HJ-NP-TV-Z]\b',
        r'\b[XYZ]\d{7}[A-HJ-NP-TV-Z]\b',
    ]:
        m = re.search(pat, texto)
        if m:
            return m.group(0)
    return ""


def _extraer_con_tesseract(ruta_imagen: str) -> Optional[Dict[str, Any]]:
    try:
        lineas = _extraer_lineas(ruta_imagen)
        if not lineas:
            return None
        logger.info("Tesseract — líneas: %s", lineas[:8])
        return {
            "proveedor": _extraer_proveedor_tess(lineas),
            "nif":       _extraer_nif_tess(lineas),
            "fecha":     _extraer_fecha_tess(lineas),
            "total":     _extraer_total_tess(lineas),
        }
    except Exception as exc:
        logger.error("Error pytesseract: %s", exc)
        return None


# ─────────────────────────────────────────────
#  Función pública principal
# ─────────────────────────────────────────────

def extraer_datos_factura(ruta_imagen: str) -> Dict[str, Any]:
    """
    Extrae nombre del negocio, NIF/CIF, fecha e importe con IVA.
    Usa Claude Vision si ANTHROPIC_API_KEY está configurada, si no pytesseract.
    """
    resultado: Dict[str, Any] = {
        "proveedor": "",
        "nif":       "",
        "fecha":     datetime.now().strftime("%Y-%m-%d"),
        "total":     None,
        "exito":     False,
        "texto_raw": [],
    }

    try:
        logger.info("OCR iniciado: %s", ruta_imagen)

        datos = _extraer_con_claude(ruta_imagen) or _extraer_con_tesseract(ruta_imagen)

        if not datos:
            logger.warning("OCR sin resultado.")
            return resultado

        resultado.update({
            "proveedor": datos.get("proveedor", ""),
            "nif":       datos.get("nif", ""),
            "fecha":     datos.get("fecha", "") or datetime.now().strftime("%Y-%m-%d"),
            "total":     datos.get("total"),
            "exito":     bool(datos.get("proveedor") and datos.get("total") is not None),
        })

        logger.info(
            "OCR → negocio='%s' | nif='%s' | fecha='%s' | total=%s",
            resultado["proveedor"], resultado["nif"],
            resultado["fecha"], resultado["total"],
        )

    except Exception as exc:
        logger.error("Error en OCR: %s", exc, exc_info=True)

    return resultado


def extraer_texto_crudo(ruta_imagen: str) -> List[str]:
    try:
        return _extraer_lineas(ruta_imagen)
    except Exception as exc:
        logger.error("Error extrayendo texto: %s", exc)
        return []
