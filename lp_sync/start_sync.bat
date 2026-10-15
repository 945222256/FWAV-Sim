@echo off
rem LP sync agent launcher. Outbound-only connection to the cloud board.
cd /d "%~dp0"
python sync_agent.py >> sync_agent.log 2>&1
