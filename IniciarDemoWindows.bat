@echo off
setlocal
set "PROJECT_DIR=%~dp0"
set "PYTHON=%PROJECT_DIR%.venv312\Scripts\python.exe"

if not exist "%PYTHON%" (
  echo Python do ambiente do projeto nao foi encontrado.
  echo Confira se a pasta .venv312 existe dentro do projeto.
  pause
  exit /b 1
)

where ssh >nul 2>nul
if errorlevel 1 (
  echo O cliente SSH do Windows nao foi encontrado.
  echo Ative OpenSSH Client em Configuracoes - Aplicativos - Recursos opcionais.
  pause
  exit /b 1
)

start "API Barbearia Imperial" powershell.exe -NoExit -Command "Set-Location -LiteralPath '%PROJECT_DIR%'; & '%PYTHON%' -m uvicorn barber_bot.api:app --app-dir src --host 127.0.0.1 --port 8000"
timeout /t 5 /nobreak >nul
start "Site Barbearia Imperial" powershell.exe -NoExit -Command "Set-Location -LiteralPath '%PROJECT_DIR%'; $env:STREAMLIT_CONFIG_DIR = Join-Path $PWD '.streamlit'; $env:STREAMLIT_BROWSER_GATHER_USAGE_STATS = 'false'; & '%PYTHON%' -m streamlit run src/streamlit_app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false"
start "Bot Telegram Barbearia Imperial" powershell.exe -NoExit -Command "Set-Location -LiteralPath '%PROJECT_DIR%'; & '%PYTHON%' -m barber_bot.telegram_bot"
timeout /t 3 /nobreak >nul
start "Link publico Pinggy" powershell.exe -NoExit -Command "Set-Location -LiteralPath '%PROJECT_DIR%'; ssh -o UserKnownHostsFile=ssh_known_hosts -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -p 443 -R0:127.0.0.1:8501 free.pinggy.io"

echo.
echo API, site, bot e tunel foram iniciados em janelas separadas.
echo A URL publica aparece na janela Link publico Pinggy.
echo Mantenha as janelas abertas enquanto o sistema estiver em uso.
echo.
pause
