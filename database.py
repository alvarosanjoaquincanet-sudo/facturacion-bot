"""
database.py — Gestión de la base de datos SQLite para el sistema de facturación.
Crea la tabla, genera números correlativos e inserta/consulta facturas.
"""

import os
import logging
import sqlite3
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "facturas.db"))


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Crea la tabla 'facturas' si no existe."""
    try:
        with _get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS facturas (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    numero        TEXT    UNIQUE NOT NULL,
                    proveedor     TEXT    NOT NULL,
                    fecha         DATE    NOT NULL,
                    total         REAL    NOT NULL,
                    categoria     TEXT    NOT NULL,
                    imagen_path   TEXT,
                    fecha_subida  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            logger.info("Base de datos lista: %s", DB_PATH)
    except sqlite3.Error as exc:
        logger.error("Error inicializando la base de datos: %s", exc)
        raise


def obtener_siguiente_numero() -> str:
    """
    Genera el siguiente número correlativo (F-0001, F-0002, …).
    Usa MAX del sufijo numérico para sobrevivir borrados sin colisiones.
    """
    try:
        with _get_connection() as conn:
            cursor = conn.execute(
                "SELECT MAX(CAST(SUBSTR(numero, 3) AS INTEGER)) FROM facturas"
            )
            max_num = cursor.fetchone()[0] or 0
            return f"F-{max_num + 1:04d}"
    except sqlite3.Error as exc:
        logger.error("Error generando número de factura: %s", exc)
        raise


def guardar_factura(
    numero: str,
    proveedor: str,
    fecha: str,
    total: float,
    categoria: str,
    imagen_path: str = "",
) -> int:
    """Inserta una factura y retorna el id del registro creado."""
    try:
        with _get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO facturas (numero, proveedor, fecha, total, categoria, imagen_path)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (numero, proveedor, fecha, float(total), categoria, imagen_path),
            )
            conn.commit()
            logger.info("Factura guardada → %s | %s | $%.2f", numero, proveedor, total)
            return cursor.lastrowid
    except sqlite3.IntegrityError as exc:
        logger.error("Número duplicado %s: %s", numero, exc)
        raise
    except sqlite3.Error as exc:
        logger.error("Error guardando factura: %s", exc)
        raise


def obtener_todas_facturas() -> List[Dict[str, Any]]:
    """Retorna todas las facturas ordenadas por fecha de subida descendente."""
    try:
        with _get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, numero, proveedor, fecha, total, categoria,
                       imagen_path, fecha_subida
                FROM facturas
                ORDER BY fecha_subida DESC
                """
            )
            return [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as exc:
        logger.error("Error obteniendo facturas: %s", exc)
        return []


def obtener_factura_por_numero(numero: str) -> Optional[Dict[str, Any]]:
    """Retorna una factura por su número único, o None si no existe."""
    try:
        with _get_connection() as conn:
            cursor = conn.execute("SELECT * FROM facturas WHERE numero = ?", (numero,))
            row = cursor.fetchone()
            return dict(row) if row else None
    except sqlite3.Error as exc:
        logger.error("Error buscando factura %s: %s", numero, exc)
        return None
