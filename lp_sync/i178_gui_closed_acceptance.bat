@echo off
rem I-178 GUI-close acceptance probe (ASCII only).
rem Run directly from Windows (double-click or scheduled task). Starts its
rem own kernel process independent of any chat session, submits one probe
rem task, waits for completion, archives results. Idempotent per probe id.
setlocal enabledelayedexpansion
cd /d G:\Playground\isaac_flapper_sim\lp_sync
set PY=C:\Python313\python.exe
set PROBE=kt-i178-gui-probe-%RANDOM%
rem Unique synthetic item id per probe (test channel skips line
rem reads; avoids busy-reject from a previous still-open probe).
set /a ITEMNUM=900 + %RANDOM% %% 99
set PROBEITEM=I-9%ITEMNUM:~1%
set /a ITEMNUM2=50 + %RANDOM% %% 40
set PROBEITEM=I-9%ITEMNUM2%
set STAMP=%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%
set STAMP=%STAMP: =0%
set OUT=..\logs\i178_gui_probe_%STAMP%
mkdir "%OUT%" 2>nul

echo [%time%] probe id %PROBE% >> "%OUT%\probe.log"

rem 1) Start kernel if port 58779 not listening (own process tree).
%PY% -c "import socket;s=socket.socket();s.settimeout(1);exit(0 if s.connect_ex(('127.0.0.1',58779))==0 else 1)"
if errorlevel 1 (
  echo [%time%] starting kernel >> "%OUT%\probe.log"
  start "LP Kernel (gui probe)" /min cmd /c "%PY% orchestrator_kernel.py >> kernel_service.log 2>&1"
  timeout /t 3 /nobreak >nul
) else (
  echo [%time%] kernel already running >> "%OUT%\probe.log"
)

rem 2) Submit probe task (test channel: no roster binding, no index).
%PY% -c "import json,os;task={'schema_version':1,'kind':'task_submit','request_id':'%PROBE%','item_id':'%PROBEITEM%','lane':'L3','title':'I-178 GUI-close probe','message':'GUI probe: reply with two chars shou-dao only, no tools.','approved_by':'Beth','task_kind':'test'};p=os.path.join('kernel_state','inbox','%PROBE%.json');open(p+'.w','w',encoding='utf-8').write(json.dumps(task,ensure_ascii=False));os.replace(p+'.w',p)"
echo [%time%] probe task submitted >> "%OUT%\probe.log"

rem 3) Wait for dispatch chain completion, then archive (separate script:
rem     cmd cannot run multi-line python -c reliably).
%PY% i178_gui_probe_wait.py %PROBE% "%OUT%"
echo [%time%] probe finished >> "%OUT%\probe.log"
type "%OUT%\result.json"
endlocal
