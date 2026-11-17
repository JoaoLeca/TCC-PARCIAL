@echo off
setlocal
cd /d "%~dp0"

where ssh >nul 2>nul
if errorlevel 1 (
  echo O cliente SSH do Windows nao foi encontrado.
  echo Ative OpenSSH Client em Configuracoes - Aplicativos - Recursos opcionais.
  pause
  exit /b 1
)

echo Criando o link temporario para o site local na porta 8501...
echo Mantenha esta janela aberta enquanto quiser disponibilizar o site.
echo Se o SSH pedir senha, pressione Enter sem digitar nada.
echo.
ssh -o UserKnownHostsFile=ssh_known_hosts -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -p 443 -R0:127.0.0.1:8501 free.pinggy.io
echo.
echo O tunel foi encerrado. Pressione qualquer tecla para fechar esta janela.
pause >nul
