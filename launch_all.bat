@echo off
echo Starting Flapping-Wing MAV Simulation System...

REM ============================================================
REM One-click launcher: Isaac Sim + joint scope + ctrl scope.
REM   - Scopes idle-wait for UDP data, safe to start first.
REM   - Manual input: joystick auto-detected, else keyboard
REM     (W/S throttle, arrows elevator/aileron, Q/E rudder).
REM   - Close: Ctrl-C the Isaac window, close scope windows.
REM ============================================================

REM --- 1. Joint waveform scope (9 joints, actual vs target) ---
echo [1/3] Joint scope ...
start "Joint Scope" /D "g:\playground\isaac_flapper_sim" python tools\joint_scope.py

REM --- 2. Manual control scope (sticks + mapped outputs) ---
echo [2/3] Ctrl scope ...
start "Ctrl Scope" /D "g:\playground\isaac_flapper_sim" python tools\ctrl_scope.py

REM --- 3. Isaac Sim main simulation (blocking window) ---
echo [3/3] Isaac Sim starting (~1 min to boot) ...
cd /d "g:\playground\isaac_flapper_sim"
call g:\Playground\isaac_sim_5.1.0\python.bat main_sim.py

echo.
echo Isaac Sim exited. Close the two scope windows manually.
pause
