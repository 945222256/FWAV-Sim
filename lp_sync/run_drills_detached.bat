@echo off
rem I-178 drills: detached run via scheduled task, ASCII only
set PYTHONIOENCODING=utf-8
cd /d G:\Playground\isaac_flapper_sim\lp_sync
echo. >> ..\logs\i178_drills_sch.log
echo ==== drills start ==== >> ..\logs\i178_drills_sch.log
python kernel_drills.py >> ..\logs\i178_drills_sch.log 2>&1
echo ==== drills end exit %ERRORLEVEL% ==== >> ..\logs\i178_drills_sch.log
