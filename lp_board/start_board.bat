@echo off
rem LP digital board launcher. Binds 127.0.0.1:8800 only.
cd /d "%~dp0"
netstat -ano | findstr ":8800 " | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
  echo LP board already running on port 8800.
  start http://127.0.0.1:8800
  exit /b 0
)
echo Starting LP board at http://127.0.0.1:8800 ...
start "LP Board" /min cmd /c "python server.py >> board.log 2>&1"
timeout /t 2 /nobreak >nul
start http://127.0.0.1:8800
