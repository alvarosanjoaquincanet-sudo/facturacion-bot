"""
database.py — Gestión de la base de datos PostgreSQL para el sistema de facturación.
"""

import os
import logging
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db() -> None:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    telegram_id    BIGINT PRIMARY KEY,
                    nombre         TEXT NOT NULL,
                    username       TEXT,
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS facturas (
                    id              SERIAL PRIMARY KEY,
                    numero          TEXT UNIQUE NOT NULL,
                    proveedor       TEXT NOT NULL,
                    fecha           TEXT NOT NULL,
                    total           REAL NOT NULL,
                    categoria       TEXT NOT NULL,
                    imagen_base64   TEXT,
                    nif             TEXT,
                    telegram_id     BIGINT,
                    telegram_nombre TEXT,
                    fecha_subida    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Compatibilidad con tablas ya existentes sin las nuevas columnas
            for col, tipo in [
                ("nif", "TEXT"),
                ("telegram_id", "BIGINT"),
                ("telegram_nombre", "TEXT"),
            ]:
                cur.execute(
                    f"ALTER TABLE facturas ADD COLUMN IF NOT EXISTS {col} {tipo}"
                )
        conn.commit()
    logger.info("PostgreSQL inicializado.")


def registrar_usuario(telegram_id: int, nombre: str, username: str = "") -> None:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO usuarios (telegram_id, nombre, username)
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_id) DO UPDATE
                    SET nombre = EXCLUDED.nombre, username = EXCLUDED.username
            """, (telegram_id, nombre, username or None))
        conn.commit()


def obtener_siguiente_numero() -> str:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(MAX(CAST(SUBSTRING(numero FROM 3) AS INTEGER)), 0) FROM facturas"
            )
            ultimo = cur.fetchone()[0]
    return f"F-{(ultimo + 1):04d}"


def guardar_factura(
    numero: str,
    proveedor: str,
    fecha: str,
    total: float,
    categoria: str,
    imagen_base64: str = "",
    nif: str = "",
    telegram_id: int = 0,
    telegram_nombre: str = "",
) -> bool:
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO facturas
                        (numero, proveedor, fecha, total, categoria,
                         imagen_base64, nif, telegram_id, telegram_nombre)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    numero, proveedor, fecha, float(total), categoria,
                    imagen_base64 or None, nif or None,
                    telegram_id or None, telegram_nombre or None,
                ))
            conn.commit()
        logger.info("Factura guardada → %s | %s | $%.2f", numero, proveedor, total)
        return True
    except Exception as exc:
        logger.error("Error guardando factura: %s", exc)
        raise


def obtener_todas_facturas() -> List[Dict[str, Any]]:
    try:
        with _get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, numero, proveedor, fecha, total, categoria,
                           imagen_base64, nif, telegram_id, telegram_nombre,
                           fecha_subida
                    FROM facturas ORDER BY fecha_subida DESC
                """)
                return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("Error obteniendo facturas: %s", exc)
        return []


def obtener_facturas_por_usuario(telegram_id: int) -> List[Dict[str, Any]]:
    try:
        with _get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, numero, proveedor, fecha, total, categoria,
                           nif, fecha_subida
                    FROM facturas
                    WHERE telegram_id = %s
                    ORDER BY fecha_subida DESC
                    LIMIT 20
                """, (telegram_id,))
                return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("Error obteniendo facturas usuario %s: %s", telegram_id, exc)
        return []


def obtener_usuarios() -> List[Dict[str, Any]]:
    try:
        with _get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT u.telegram_id, u.nombre, u.username,
                           COUNT(f.id)            AS total_facturas,
                           COALESCE(SUM(f.total), 0) AS total_gastado
                    FROM usuarios u
                    LEFT JOIN facturas f ON f.telegram_id = u.telegram_id
                    GROUP BY u.telegram_id, u.nombre, u.username
                    ORDER BY total_gastado DESC
                """)
                return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("Error obteniendo usuarios: %s", exc)
        return []


def obtener_factura_por_numero(numero: str) -> Optional[Dict[str, Any]]:
    try:
        with _get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM facturas WHERE numero = %s", (numero,))
                row = cur.fetchone()
        return dict(row) if row else None
    except Exception as exc:
        logger.error("Error buscando factura %s: %s", numero, exc)
        return None
