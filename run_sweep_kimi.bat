@echo off
rem 一次性扫频试验启动器（2026-09-18 Kimi 审计任务用）
cd /d g:\Playground\isaac_flapper_sim
g:\Playground\isaac_sim_5.1.0\python.bat main_sim.py > logs\launch_sweep_kimi.log 2>&1
