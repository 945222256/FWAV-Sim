@echo off
cd /d G:\Playground\isaac_flapper_sim
set FLAPPER_HEADED=1
set EAGLE_SHELL=1
set EAGLE_SHELL_AUTOSTOP=0
G:\Playground\isaac_sim_5.1.0\python.bat eagle_shell\run_eagle_shell.py
pause
