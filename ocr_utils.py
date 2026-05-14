"""
ocr_utils.py — OCR gratuito con OpenCV + pytesseract.

OpenCV hace el preprocesamiento adaptativo (umbralado local, reducción de ruido,
escalado) antes de pasar la imagen a pytesseract, lo que mejora drásticamente
la precisión en fotos de móvil con iluminación irregular.

Campos extraídos: nombre del negocio, NIF/CIF/NIE, fecha, importe con IVA.
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from PIL import Image
import pytesseract

logger = logging.getLogger(__name__)

# PSM 4 = columna de texto de tamaño variable (mejor para tiquets)
_CFG = "--psm 4 --oem 3"


# ─────────────────────────────────────────────
#  Preprocesamiento con OpenCV
# ─────────────────────────────────────────────

def _preprocesar(ruta_imagen: str) -> Image.Image:
    """
    Pipeline de preprocesamiento para fotos de tiquets tomadas con móvil:
    1. Escala de grises
    2. Ampliación mínima a 1600 px de ancho
    3. Reducción de ruido (fastNlMeans)
    4. Umbralado adaptativo gaussiano → maneja iluminación irregular
    5. Conversión a PIL para pytesseract
    """
    img = cv2.imread(ruta_imagen)
    if img is None:
        # Fallback: abrir con PIL y convertir a numpy
        img = np.array(Image.open(ruta_imagen).convert("RGB"))
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Escalar si es pequeña
    h, w = gray.shape
    if w < 1600:
        scale = 1600 / w
        gray = cv2.resize(gray, None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_CUBIC)

    # Reducir ruido de cámara
    gray = cv2.fastNlMeansDenoising(gray, h=12,
                                    templateWindowSize=7,
                                    searchWindowSize=21)

    # Umbralado adaptativo: maneja sombras y gradientes de luz
    thresh = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=31,   # tamaño del vecindario local
        C=10,           # constante que se resta a la media
    )

    return Image.fromarray(thresh)


# ─────────────────────────────────────────────
#  Extracción de texto
# ─────────────────────────────────────────────

def _extraer_lineas(ruta_imagen: str) -> List[str]:
    imagen = _preprocesar(ruta_imagen)
    texto  = pytesseract.image_to_string(imagen, lang="spa+eng", config=_CFG)
    return [l.strip() for l in texto.split("\n") if l.strip()]


# ─────────────────────────────────────────────
#  Extracción de cada campo
# ─────────────────────────────────────────────

def _extraer_nombre_negocio(lineas: List[str]) -> str:
    """
    Estrategia:
    1. Busca líneas en MAYÚSCULAS en las primeras 10 líneas (encabezado del ticket).
    2. Excluye líneas que son solo números, fechas, NIF o muy cortas.
    3. Fallback: primera línea con ≥4 letras.
    """
    _excluir = re.compile(
        r"^[\d\s/\-.:,]+$"          # solo números y separadores
        r"|^\s*C\.?I\.?F\.?\s*:"     # etiqueta CIF
        r"|^\s*N\.?I\.?F\.?\s*:"     # etiqueta NIF
        r"|FACTURA|TICKET|RECIBO|ALBAR"
    )

    for linea in lineas[:10]:
        if len(linea) < 4:
            continue
        if _excluir.search(linea.upper()):
            continue
        letras = [c for c in linea if c.isalpha()]
        if len(letras) < 3:
            continue
        # Prioriza líneas con mayoría de mayúsculas (umbral 0.4 para nombres mixtos)
        if sum(1 for c in letras if c.isupper()) / len(letras) >= 0.4:
            return linea.strip("*-=_. |")

    # Fallback
    for linea in lineas[:10]:
        letras = [c for c in linea if c.isalpha()]
        if len(letras) >= 4:
            return linea.strip("*-=_. |")

    return lineas[0].strip("*-=_. |") if lineas else ""


def _extraer_nif(lineas: List[str]) -> str:
    """
    Busca NIF/CIF/NIE con tolerancia a errores OCR comunes (O↔0, I↔1).
    Normaliza O→0 e I→1 en posiciones numéricas antes de validar.
    """
    texto = " ".join(lineas).upper()

    # Normalizar confusiones OCR frecuentes solo en contexto de NIF
    def _normalizar(s: str) -> str:
        return (s.replace("O", "0").replace("I", "1")
                 .replace("L", "1").replace("S", "5"))

    patrones_raw = [
        r'\b[ABCDEFGHJKLMNPQRSUVW][\dOIL]{7}[A-J0-9]\b',  # CIF
        r'\b[\dOIL]{8}[A-HJ-NP-TV-Z]\b',                   # NIF
        r'\b[XYZ][\dOIL]{7}[A-HJ-NP-TV-Z]\b',              # NIE
    ]
    patrones_limpios = [
        r'^[ABCDEFGHJKLMNPQRSUVW]\d{7}[A-J0-9]$',
        r'^\d{8}[A-HJ-NP-TV-Z]$',
        r'^[XYZ]\d{7}[A-HJ-NP-TV-Z]$',
    ]

    for raw_pat, clean_pat in zip(patrones_raw, patrones_limpios):
        m = re.search(raw_pat, texto)
        if m:
            candidato = _normalizar(m.group(0))
            if re.match(clean_pat, candidato):
                return candidato

    return ""


def _extraer_fecha(lineas: List[str]) -> str:
    """
    Reconoce los formatos de fecha más comunes en tiquets españoles.
    """
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


def _extraer_total(lineas: List[str]) -> Optional[float]:
    """
    Extrae el importe TOTAL con IVA incluido.

    Estrategia en orden de prioridad:
    1. Líneas con keywords de total final (más específicas primero).
    2. Línea con "TOTAL" que NO sea subtotal → mayor importe.
    3. Último importe precedido por símbolo de moneda.
    4. Mayor importe del ticket (última defensa).

    Maneja formato español (15,90 €) y anglosajón (15.90).
    """
    def _parse(txt: str) -> Optional[float]:
        txt = re.sub(r"[€$£\s]", "", txt)
        # Formato español: 1.234,56 → 1234.56
        if re.search(r"\d\.\d{3},\d", txt):
            txt = txt.replace(".", "").replace(",", ".")
        else:
            txt = txt.replace(",", ".")
        partes = txt.split(".")
        if len(partes) > 2:
            txt = "".join(partes[:-1]) + "." + partes[-1]
        try:
            v = float(txt)
            return v if 0.01 <= v < 1_000_000 else None
        except ValueError:
            return None

    pat_num = r"([\d][0-9\.,\s]*)"

    # ── Prioridad 1: keywords de total final ─────────────────────────
    kw_alta = [
        "total a pagar", "total con iva", "total con impuesto",
        "importe total", "total factura", "a pagar", "total eur",
        "grand total", "amount due", "total due", "total:",
        "tot.:", "tot :", "imp. total", "importe:",
    ]
    for linea in lineas:
        ll = linea.lower()
        if any(kw in ll for kw in kw_alta):
            nums = [_parse(m.group(1)) for m in re.finditer(pat_num, linea)]
            nums = [n for n in nums if n]
            if nums:
                return max(nums)

    # Regex tolerante a errores OCR frecuentes: T0TAL, TOTAI, T07AL, etc.
    _pat_total_ocr = re.compile(r't[o0][t7][a4][il1]', re.IGNORECASE)
    _pat_importe_ocr = re.compile(r'imp[o0]rte?', re.IGNORECASE)
    for linea in lineas:
        if _pat_total_ocr.search(linea) or _pat_importe_ocr.search(linea):
            nums = [_parse(m.group(1)) for m in re.finditer(pat_num, linea)]
            nums = [n for n in nums if n]
            if nums:
                return max(nums)

    # ── Prioridad 2: "total" o "importe" (excluyendo subtotal) ───────
    candidatos = []
    for linea in lineas:
        ll = linea.lower()
        if any(x in ll for x in ("subtotal", "base imp", "base imponible", "antes de iva")):
            continue
        if "total" in ll or "importe" in ll or _pat_total_ocr.search(linea):
            nums = [_parse(m.group(1)) for m in re.finditer(pat_num, linea)]
            candidatos.extend(n for n in nums if n)

    if candidatos:
        return max(candidatos)

    # ── Prioridad 3: símbolo de moneda ───────────────────────────────
    texto = " ".join(lineas)
    con_moneda = []
    for pat in [r"([\d][0-9\.,]*)\s*€", r"€\s*([\d][0-9\.,]*)",
                r"\$([\d][0-9\.,]*)", r"([\d][0-9\.,]*)\s*EUR"]:
        for m in re.finditer(pat, texto, re.IGNORECASE):
            v = _parse(m.group(1))
            if v:
                con_moneda.append(v)
    if con_moneda:
        return max(con_moneda)

    # ── Prioridad 4: mayor importe del ticket ────────────────────────
    todos = [_parse(m.group(1)) for m in re.finditer(pat_num, texto)]
    todos = [v for v in todos if v and v > 0.5]
    return max(todos) if todos else None


# ─────────────────────────────────────────────
#  Función pública principal
# ─────────────────────────────────────────────

def extraer_datos_factura(ruta_imagen: str) -> Dict[str, Any]:
    """
    Extrae los 4 campos del tiquet/factura:
      - nombre_negocio / proveedor
      - nif (NIF/CIF/NIE del emisor)
      - fecha (YYYY-MM-DD)
      - total (importe con IVA)
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
        lineas = _extraer_lineas(ruta_imagen)

        if not lineas:
            logger.warning("OCR no detectó texto.")
            return resultado

        logger.info("Líneas (%d): %s", len(lineas), lineas[:10])

        resultado["texto_raw"] = lineas
        resultado["proveedor"] = _extraer_nombre_negocio(lineas)
        resultado["nif"]       = _extraer_nif(lineas)
        resultado["fecha"]     = _extraer_fecha(lineas)
        resultado["total"]     = _extraer_total(lineas)
        resultado["exito"]     = bool(
            resultado["proveedor"] and resultado["total"] is not None
        )

        logger.info(
            "OCR → negocio='%s' | nif='%s' | fecha='%s' | total=%s",
            resultado["proveedor"], resultado["nif"],
            resultado["fecha"],     resultado["total"],
        )

    except Exception as exc:
        logger.error("Error OCR: %s", exc, exc_info=True)

    return resultado


def extraer_texto_crudo(ruta_imagen: str) -> List[str]:
    try:
        return _extraer_lineas(ruta_imagen)
    except Exception as exc:
        logger.error("Error extrayendo texto: %s", exc)
        return []
