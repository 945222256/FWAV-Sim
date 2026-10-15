@echo off
rem Replay GUI launcher (2026-09-28). ASCII-only: cmd.exe parses .bat in
rem ANSI codepage on Chinese Windows. Console STAYS OPEN by user request:
rem errors and prints from the GUI are visible in the background window.
cd /d "%~dp0"
python tools\flight_replay_gui.py
if errorlevel 1 pause
