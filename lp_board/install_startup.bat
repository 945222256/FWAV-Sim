@echo off
rem Installs the LP board auto-start entry for the current user.
rem The entry file itself is ASCII-only and starts the board minimized.
set "DST=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\lp_board_startup.bat"
> "%DST%" echo @echo off
>> "%DST%" echo cd /d G:\Playground\isaac_flapper_sim\lp_board
>> "%DST%" echo netstat -ano ^| findstr ":8800 " ^| findstr "LISTENING" ^>nul 2^>^&1 ^&^& exit /b
>> "%DST%" echo start "LP Board" /min cmd /c "C:\Python313\python.exe server.py >> board.log 2>&1"
echo Installed startup entry:
echo   %DST%
