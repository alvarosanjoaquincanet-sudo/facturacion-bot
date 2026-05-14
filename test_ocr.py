"""
test_ocr.py — Script de prueba para el módulo OCR.

Uso:
  python test_ocr.py                    # genera imagen de prueba y la analiza
  python test_ocr.py ruta/a/imagen.jpg  # analiza imagen existente
"""

import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def _crear_imagen_prueba(ruta: str) -> None:
    """Genera un ticket de ejemplo con PIL para testear el OCR."""
    from PIL import Image, ImageDraw, ImageFont

    img  = Image.new("RGB", (440, 360), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Intentar usar una fuente del sistema; fallback a default
    try:
        fuente_grande  = ImageFont.truetype("arial.ttf", 18)
        fuente_normal  = ImageFont.truetype("arial.ttf", 15)
        fuente_pequena = ImageFont.truetype("arial.ttf", 13)
    except (IOError, OSError):
        fuente_grande  = ImageFont.load_default()
        fuente_normal  = fuente_grande
        fuente_pequena = fuente_grande

    lineas = [
        ("SUPERMERCADO EL AHORRO",            fuente_grande,  True),
        ("Calle Mayor 42, Madrid",             fuente_pequena, False),
        ("Tel: 91 555 12 34",                  fuente_pequena, False),
        ("",                                   fuente_normal,  False),
        ("Fecha: 13/05/2024    Hora: 10:32",   fuente_normal,  False),
        ("Ticket: #0042831",                   fuente_normal,  False),
        ("",                                   fuente_normal,  False),
        ("Leche Entera 1L x2         3,50 €",  fuente_normal,  False),
        ("Pan Integral              1,20 €",   fuente_normal,  False),
        ("Manzanas kg               2,80 €",   fuente_normal,  False),
        ("Agua 1.5L x6              4,20 €",   fuente_normal,  False),
        ("Queso Manchego 200g       3,60 €",   fuente_normal,  False),
        ("",                                   fuente_normal,  False),
        ("-" * 36,                             fuente_pequena, False),
        ("Subtotal:                15,30 €",   fuente_normal,  False),
        ("IVA (10%):                1,53 €",   fuente_pequena, False),
        ("-" * 36,                             fuente_pequena, False),
        ("TOTAL A PAGAR:           16,83 €",   fuente_grande,  True),
        ("",                                   fuente_normal,  False),
        ("Gracias por su compra",              fuente_pequena, False),
    ]

    y = 20
    for texto, fuente, negrita in lineas:
        color = (0, 0, 0)
        if negrita:
            # Simula negrita dibujando el texto dos veces desplazado 1 px
            draw.text((21, y + 1), texto, fill=color, font=fuente)
        draw.text((20, y), texto, fill=color, font=fuente)
        y += 17

    img.save(ruta)
    print(f"✅ Imagen de prueba generada: {ruta}")


def _analizar(ruta: str) -> None:
    from ocr_utils import extraer_datos_factura, extraer_texto_crudo

    print(f"\n{'─'*55}")
    print(f"  Imagen: {ruta}")
    print(f"{'─'*55}")

    print("\n📝 Texto bruto extraído por OCR:")
    lineas = extraer_texto_crudo(ruta)
    if lineas:
        for i, linea in enumerate(lineas, 1):
            print(f"  {i:3d}. {linea}")
    else:
        print("  (sin texto detectado)")

    print(f"\n{'─'*55}")
    print("📊 Datos estructurados:")
    datos = extraer_datos_factura(ruta)

    def _fmt(v, prefijo="$"):
        return f"{prefijo}{v:.2f}" if v is not None else "— no encontrado"

    print(f"  🏪 Proveedor : {datos['proveedor'] or '— no encontrado'}")
    print(f"  📅 Fecha     : {datos['fecha']}")
    print(f"  💰 Total     : {_fmt(datos['total'])}")
    print(f"  ✅ Éxito OCR : {'Sí' if datos['exito'] else 'No (datos incompletos)'}")
    print(f"{'─'*55}\n")


def main() -> None:
    if len(sys.argv) >= 2:
        ruta = sys.argv[1]
        if not Path(ruta).exists():
            print(f"❌ Archivo no encontrado: {ruta}")
            sys.exit(1)
        _analizar(ruta)
    else:
        ruta_prueba = "imagen_prueba.jpg"
        print("No se proporcionó imagen. Generando ticket de prueba…")
        try:
            _crear_imagen_prueba(ruta_prueba)
        except ImportError:
            print("❌ Pillow no instalado. Ejecuta: pip install Pillow")
            sys.exit(1)
        _analizar(ruta_prueba)


if __name__ == "__main__":
    main()
