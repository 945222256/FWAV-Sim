@echo off
rem Stop the LP digital board. Ledger files are untouched.
set found=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8800 " ^| findstr "LISTENING"') do (
  taskkill /pid %%a /f >nul 2>&1
  set found=1
)
if %found%==1 (echo LP board stopped.) else (echo LP board is not running.)
