@echo off
rem Installs the I-126 resident host service auto-start entry (prototype).
set "DST=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\lp_host_service.bat"
> "%DST%" echo @echo off
>> "%DST%" echo cd /d G:\Playground\isaac_flapper_sim\lp_sync
>> "%DST%" echo start "LP HostSvc" /min cmd /c "C:\Python313\python.exe host_service.py >> host_service.log 2>&1"
echo Installed: %DST%
