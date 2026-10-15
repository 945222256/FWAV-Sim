@echo off
rem I-178: install the orchestrator kernel auto-start entry (prototype).
rem HQ 决定启用时执行本脚本；卸载删除启动项即可。
set "DST=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\lp_orchestrator_kernel.bat"
> "%DST%" echo @echo off
>> "%DST%" echo cd /d G:\Playground\isaac_flapper_sim\lp_sync
>> "%DST%" echo start "LP Kernel" /min cmd /c "C:\Python313\python.exe orchestrator_kernel.py >> kernel_service.log 2>&1"
echo Installed: %DST%
