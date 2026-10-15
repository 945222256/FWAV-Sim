@echo off
rem Installs the LP Board root CA into the current user's trusted store.
rem Run once per machine. After this, browsers trust https://101.96.207.208/
certutil -user -addstore Root "%~dp0lp-board-root-ca.cer"
echo.
echo Done. If a security confirmation dialog appeared, click Yes to trust.
pause
