# Sistema de Facturación — Bot de Telegram + Dashboard

Bot de Telegram que extrae datos de facturas/tickets mediante OCR y los visualiza
en un dashboard web interactivo con Streamlit.

---

## Estructura del proyecto

```
facturacion_bot/
├── bot.py           ← Lógica del bot de Telegram
├── database.py      ← Operaciones SQLite
├── ocr_utils.py     ← Extracción de datos con EasyOCR
├── dashboard.py     ← Dashboard con Streamlit
├── test_ocr.py      ← Script de prueba OCR
├── requirements.txt ← Dependencias
└── imagenes/        ← Fotos guardadas (se crea automáticamente)
```

La base de datos `facturas.db` se genera automáticamente al arrancar.

---

## Requisitos previos

- Python 3.10 o superior
- Token de un bot de Telegram (obtenerlo con [@BotFather](https://t.me/BotFather))

> **Nota sobre EasyOCR:** la primera ejecución descarga los modelos de reconocimiento
> (~300 MB). Requiere conexión a Internet y ~1 GB de RAM libre.

---

## Instalación

```bash
# 1. Clonar o descomprimir el proyecto
cd facturacion_bot

# 2. (Recomendado) Crear entorno virtual
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

# 3. Instalar dependencias
pip install -r requirements.txt
```

---

## Configuración

### Token de Telegram

**Linux / macOS**
```bash
export TELEGRAM_TOKEN="tu_token_aquí"
```

**Windows (cmd)**
```cmd
set TELEGRAM_TOKEN=tu_token_aquí
```

**Windows (PowerShell)**
```powershell
$env:TELEGRAM_TOKEN = "tu_token_aquí"
```

### Variables opcionales

| Variable         | Valor por defecto        | Descripción                        |
|------------------|--------------------------|------------------------------------|
| `TELEGRAM_TOKEN` | —                        | **Obligatorio**                    |
| `DB_PATH`        | `./facturas.db`          | Ruta del archivo SQLite            |
| `DASHBOARD_URL`  | `http://127.0.0.1:8501`  | URL que el bot muestra con /dashboard |

---

## Ejecución

Abre **dos terminales** en la carpeta del proyecto:

**Terminal 1 — Bot:**
```bash
python bot.py
```

**Terminal 2 — Dashboard:**
```bash
streamlit run dashboard.py
```

Accede al dashboard en: `http://localhost:8501`

---

## Prueba del OCR

```bash
# Genera un ticket de prueba y lo analiza:
python test_ocr.py

# Analiza una imagen propia:
python test_ocr.py ruta/a/factura.jpg
```

---

## Flujo del bot

```
/start
  └─▶ Envía foto de factura/ticket
        └─▶ OCR extrae proveedor, fecha y total
              ├─▶ [Éxito] Muestra datos → elige categoría
              └─▶ [Fallo] Solicita datos manualmente
                    └─▶ Elige categoría
                          └─▶ Guarda con número F-0001, F-0002…
                                └─▶ Confirma con resumen
```

Comandos disponibles:
- `/start` — inicia o reinicia el flujo
- `/dashboard` — muestra la URL del panel web
- `/cancel` — cancela la operación en curso

---

## Categorías predefinidas

| Botón                    | Valor guardado |
|--------------------------|----------------|
| 🛒 Supermercado          | Supermercado   |
| 🍽️ Restaurante           | Restaurante    |
| ⚡ Servicios             | Servicios      |
| 📦 Otros                 | Otros          |
| ✏️ Escribir manualmente  | (texto libre)  |

---

## Despliegue en producción

### Bot en Railway / Render
1. Crea un nuevo proyecto y sube el código.
2. Añade la variable de entorno `TELEGRAM_TOKEN`.
3. Comando de inicio: `python bot.py`.

### Dashboard en Streamlit Cloud
1. Sube el repositorio a GitHub.
2. En [share.streamlit.io](https://share.streamlit.io), conecta el repo.
3. Archivo principal: `dashboard.py`.
4. Configura `DB_PATH` para apuntar a la misma BD que el bot (usa un volumen
   compartido o exporta/importa el SQLite periódicamente).

### Bot con webhook (alternativa a polling)
Añade al final de `bot.py`, dentro de `main()`, reemplazando `run_polling`:
```python
app.run_webhook(
    listen="0.0.0.0",
    port=int(os.getenv("PORT", 8443)),
    webhook_url=os.getenv("WEBHOOK_URL"),
)
```

---

## Copia de seguridad de la base de datos

```bash
# Linux/macOS
cp facturas.db facturas_backup_$(date +%Y%m%d).db

# Windows (PowerShell)
Copy-Item facturas.db "facturas_backup_$(Get-Date -Format yyyyMMdd).db"
```

Para automatizarlo, añade ese comando a un cron job (Linux) o Tarea programada
(Windows) con frecuencia diaria.

---

## Solución de problemas

| Síntoma | Causa probable | Solución |
|---------|---------------|----------|
| `SystemExit: No se encontró TELEGRAM_TOKEN` | Variable no configurada | Revisa `export`/`set` |
| OCR muy lento la primera vez | Descarga de modelos | Espera ~30 s; se cachean en `~/.EasyOCR/` |
| `ModuleNotFoundError: easyocr` | Dependencias no instaladas | `pip install -r requirements.txt` |
| Dashboard no carga datos | Bot y dashboard apuntan a distinto `DB_PATH` | Asegura que ambos usan la misma ruta |
| Error de GPU con EasyOCR | Driver CUDA ausente | Ya usa `gpu=False` por defecto |
