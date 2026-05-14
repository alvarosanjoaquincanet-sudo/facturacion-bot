"""
dashboard.py — Panel de control web del sistema de facturación.

Muestra métricas, gráficos interactivos y tabla filtrable de facturas.

Ejecución:
  streamlit run dashboard.py
"""

import logging
import os
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components

from database import init_db, obtener_todas_facturas

logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="Facturación",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ─── Carga de datos ────────────────────────────────────────────────────────────

@st.cache_data(ttl=30)  # caché de 30 s para no golpear SQLite en cada widget
def _cargar_datos() -> pd.DataFrame:
    init_db()
    registros = obtener_todas_facturas()
    if not registros:
        return pd.DataFrame(
            columns=["id", "numero", "proveedor", "fecha", "total",
                     "categoria", "imagen_path", "fecha_subida"]
        )
    df = pd.DataFrame(registros)
    df["fecha"]       = pd.to_datetime(df["fecha"],       errors="coerce")
    df["fecha_subida"] = pd.to_datetime(df["fecha_subida"], errors="coerce")
    df["mes_anio"]    = df["fecha"].dt.to_period("M").astype(str)
    return df


# ─── Componentes ───────────────────────────────────────────────────────────────

def _metricas(df: pd.DataFrame) -> None:
    total_facturas = len(df)
    total_gastado  = df["total"].sum()  if not df.empty else 0.0
    promedio       = df["total"].mean() if not df.empty else 0.0

    c1, c2, c3 = st.columns(3)
    c1.metric("📋 Total de facturas", f"{total_facturas:,}")
    c2.metric("💰 Total gastado",     f"${total_gastado:,.2f}")
    c3.metric("📊 Promedio / factura", f"${promedio:,.2f}")


def _grafico_mensual(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("Sin datos para el gráfico mensual.")
        return

    resumen = (
        df.groupby("mes_anio")
          .agg(Cantidad=("id", "count"), Total=("total", "sum"))
          .reset_index()
          .rename(columns={"mes_anio": "Mes"})
          .sort_values("Mes")
    )

    fig = px.bar(
        resumen,
        x="Mes",
        y="Cantidad",
        color="Total",
        color_continuous_scale="Blues",
        text="Cantidad",
        title="Facturas por mes",
        labels={"Cantidad": "N.º de facturas", "Total": "Total ($)"},
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(
        coloraxis_showscale=False,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=40, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)


def _grafico_categorias(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("Sin datos para el gráfico de categorías.")
        return

    por_cat = (
        df.groupby("categoria")["total"]
          .sum()
          .reset_index()
          .rename(columns={"categoria": "Categoría", "total": "Total"})
          .sort_values("Total", ascending=False)
    )

    fig = px.pie(
        por_cat,
        names="Categoría",
        values="Total",
        title="Gastos por categoría",
        hole=0.42,
        color_discrete_sequence=px.colors.qualitative.Set2,
    )
    fig.update_traces(textposition="inside", textinfo="percent+label")
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=40, b=0),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)


def _tabla(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("No hay facturas registradas aún.")
        return

    vista = df[["numero", "proveedor", "fecha", "total", "categoria"]].copy()
    vista.columns = ["Número", "Proveedor", "Fecha", "Total ($)", "Categoría"]
    vista["Fecha"]     = vista["Fecha"].dt.strftime("%d/%m/%Y")
    vista["Total ($)"] = vista["Total ($)"].apply(lambda x: f"${x:,.2f}")

    st.caption("👆 Haz clic en una fila para ver la foto de la factura")

    seleccion = st.dataframe(
        vista,
        use_container_width=True,
        hide_index=True,
        selection_mode="single-row",
        on_select="rerun",
        column_config={
            "Número":    st.column_config.TextColumn(width="small"),
            "Total ($)": st.column_config.TextColumn(width="small"),
            "Categoría": st.column_config.TextColumn(width="medium"),
        },
    )

    # Mostrar imagen de la factura seleccionada
    filas = seleccion.selection.rows if seleccion and seleccion.selection else []
    if filas:
        idx      = filas[0]
        fila_df  = df.iloc[idx]
        imagen   = fila_df.get("imagen_path", "")

        st.markdown("---")
        col_info, col_img = st.columns([1, 1])

        with col_info:
            st.subheader(f"📋 {fila_df['numero']}")
            st.markdown(f"**🏪 Proveedor:** {fila_df['proveedor']}")
            st.markdown(f"**📅 Fecha:** {fila_df['fecha'].strftime('%d/%m/%Y') if hasattr(fila_df['fecha'], 'strftime') else fila_df['fecha']}")
            st.markdown(f"**💰 Total:** ${fila_df['total']:,.2f}")
            st.markdown(f"**🏷️ Categoría:** {fila_df['categoria']}")

        with col_img:
            if imagen and os.path.exists(imagen):
                st.image(imagen, caption=f"Factura {fila_df['numero']}", use_container_width=True)
            else:
                st.info("📷 No hay imagen disponible para esta factura.")


def _sidebar(df: pd.DataFrame) -> pd.DataFrame:
    st.sidebar.title("🔍 Filtros")

    if df.empty:
        st.sidebar.info("Sin datos disponibles.")
        return df

    # — Categorías —
    cats_disponibles = sorted(df["categoria"].dropna().unique().tolist())
    cats_sel = st.sidebar.multiselect(
        "Categoría", options=cats_disponibles, default=cats_disponibles
    )

    # — Rango de fechas —
    st.sidebar.subheader("📅 Rango de fechas")
    fecha_min = df["fecha"].min().date()
    fecha_max = df["fecha"].max().date()

    desde = st.sidebar.date_input("Desde", value=fecha_min,
                                  min_value=fecha_min, max_value=fecha_max)
    hasta = st.sidebar.date_input("Hasta", value=fecha_max,
                                  min_value=fecha_min, max_value=fecha_max)

    filtrado = df[
        df["categoria"].isin(cats_sel)
        & (df["fecha"].dt.date >= desde)
        & (df["fecha"].dt.date <= hasta)
    ]

    st.sidebar.markdown("---")
    st.sidebar.caption(f"Mostrando {len(filtrado):,} de {len(df):,} facturas")
    return filtrado


# ─── App principal ─────────────────────────────────────────────────────────────

def main() -> None:
    st.title("🧾 Sistema de Facturación")
    st.caption("Panel de control · visualiza y analiza tus facturas")

    # Botón de recarga manual
    col_btn, _ = st.columns([1, 7])
    with col_btn:
        if st.button("🔄 Actualizar"):
            st.cache_data.clear()
            st.rerun()

    df_completo = _cargar_datos()
    df          = _sidebar(df_completo)

    st.markdown("---")
    st.subheader("📈 Resumen")
    _metricas(df)

    st.markdown("---")
    col_izq, col_der = st.columns(2)
    with col_izq:
        st.subheader("📅 Evolución mensual")
        _grafico_mensual(df)
    with col_der:
        st.subheader("🏷️ Por categoría")
        _grafico_categorias(df)

    st.markdown("---")
    st.subheader("📋 Listado de facturas")
    _tabla(df)

    st.markdown("---")
    st.caption(f"Actualizado: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")

    # Auto-refresco cada 15 segundos
    components.html(
        '<meta http-equiv="refresh" content="15">',
        height=0,
    )


if __name__ == "__main__":
    main()
