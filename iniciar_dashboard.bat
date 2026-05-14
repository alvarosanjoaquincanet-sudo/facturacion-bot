@echo off
cd /d "C:\Users\Jose Javier\.claude\facturacion_bot"
set TELEGRAM_TOKEN=8857627386:AAGxumhvyWOSzmwu93ZEzzAhfVsZrKMZ5Nk
echo Iniciando dashboard...
py -3.12 -m streamlit run dashboard.py
pause
