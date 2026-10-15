@echo off
rem Compatibility entry. The current-user, windowless four-service installer
rem is authoritative and refuses to replace tasks while they are running.
setlocal
set "DIR=G:\Playground\isaac_flapper_sim\lp_sync"
if "%1"=="install" goto install
if "%1"=="uninstall" goto uninstall
echo Usage: %0 install ^| uninstall
exit /b 1
:install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%DIR%\install_runtime_tasks.ps1" -Start
exit /b %errorlevel%
:uninstall
echo Remove the four services through Task Scheduler after authorized maintenance.
echo This compatibility entry does not stop or remove running production services.
exit /b 1
