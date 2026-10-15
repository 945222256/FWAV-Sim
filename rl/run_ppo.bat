@echo off
rem PPO 训练后台启动器（2026-09-19）
cd /d g:\Playground\isaac_flapper_sim
C:\Python313\python.exe -u rl\train_ppo.py > rl\data\ppo_train.log 2>&1
