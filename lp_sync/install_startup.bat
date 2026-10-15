@echo off
rem Installs the LP sync agent auto-start entry for the current user.
set "DST=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\lp_sync_agent.bat"
> "%DST%" echo @echo off
>> "%DST%" echo cd /d G:\Playground\isaac_flapper_sim\lp_sync
>> "%DST%" echo start "LP Sync" /min cmd /c "C:\Python313\python.exe sync_agent.py >> sync_agent.log 2>&1"
echo Installed startup entry:
echo   %DST%
