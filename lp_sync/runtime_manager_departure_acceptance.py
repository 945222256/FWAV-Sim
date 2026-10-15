"""A real disposable manager ends its model session before an OS-owned worker finishes."""
import datetime,json,os,pathlib,socket,subprocess,sys,time
ROOT=pathlib.Path(__file__).resolve().parents[1]
def port():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]
def shell(code):
    return subprocess.check_output(['powershell.exe','-NoProfile','-Command',code],encoding='utf-8')
def call(number,body):
    with socket.create_connection(('127.0.0.1',number),timeout=10) as sock:
        sock.settimeout(30);sock.sendall((json.dumps(body)+'\n').encode());return json.loads(sock.makefile('rb').readline())
def wait(test,seconds=180):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        try:
            value=test()
            if value:return value
        except (OSError,ValueError,KeyError):pass
        time.sleep(.5)
    raise TimeoutError('isolated manager departure condition timed out')
def main():
    stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    work=ROOT/'logs/runtime_real_acceptance'/('manager_departure_'+stamp);work.mkdir(parents=True)
    hp,kp=port(),port()
    env={'LP_HOST_STATE_ROOT':str(work/'runtime'),'LP_HOST_PORT':str(hp),'LP_HOST_ISOLATED':'1',
         'LP_HOST_ROSTER_PATH':str(work/'roster.json'),'LP_HOST_REGISTRY_PATH':str(work/'registry.json'),
         'LP_HOST_STATUS':str(work/'host_status.json'),'LP_DISPATCH_QUEUE_ROOT':str(work/'queue'),
         'LP_TURN_RECOVERY_ROOT':str(work/'turn_recovery'),'LP_ITEMS_REGISTRY':str(ROOT/'items_registry'),
         'LP_EXECUTOR_DEFAULTS':str(ROOT/'lp_sync/executor_defaults.json'),'FLAPPER_LEDGER_MODE':'cloud'}
    entry=work/'entry.py'
    entry.write_text('import os,sys,runpy,pathlib\nROOT=pathlib.Path('+repr(str(ROOT))+')\nWORK=pathlib.Path('+repr(str(work))+')\nos.environ.update('+repr(env)+')\nsys.path.insert(0,str(ROOT/"lp_sync"))\nmode=sys.argv[1]\nsys.stdout=sys.stderr=open(WORK/(mode+".log"),"a",encoding="utf-8",buffering=1)\nsys.argv=[str(ROOT/"lp_sync"/("host_service.py" if mode=="host" else "orchestrator_kernel.py"))]\nif mode=="kernel":sys.argv += ["--state-root",str(WORK/"kernel"),"--queue-root",str(WORK/"queue"),"--port",'+repr(str(kp))+',"--wake-s","0.5"]\nrunpy.run_path(sys.argv[0],run_name="__main__")\n',encoding='utf-8')
    run='run-manager-exit-'+stamp
    task={'schema_version':1,'kind':'task_submit','request_id':'manager-exit-'+stamp,'item_id':'I-213',
          'lane':'L3','title':'I-213 test: manager exited before completion','approved_by':'Beth','reviewer':'beth','task_kind':'test',
          'message':'这是 I-178 原冻结验收要求和已批准 I-213 专用验收的附加观察，只运行有限程序，不认领生产、不做仿真。\nC:/Python313/python.exe "'+str(ROOT/'lp_sync/runtime_acceptance_job.py').replace('\\','/')+'" --work "'+str(work/'job').replace('\\','/')+'" --state "'+str(work/'kernel').replace('\\','/')+'" --run '+run+'-a1 --item I-213 --steps 240 --outcome evidence_appended'}
    # The kernel derives its run from the request ID; retain exactly that run.
    task['message']=task['message'].replace(run+'-a1','run-'+task['request_id']+'-a1')
    (work/'approved_task.json').write_text(json.dumps(task,ensure_ascii=False,indent=2),encoding='utf-8')
    submit=work/'manager_submit.py'
    submit.write_text('import json,pathlib,os,time\nWORK=pathlib.Path(__file__).resolve().parent\ntask=json.loads((WORK/"approved_task.json").read_text(encoding="utf-8"))\nfolder=WORK/"kernel/inbox";folder.mkdir(parents=True,exist_ok=True)\ntmp=folder/(task["request_id"]+".writing");tmp.write_text(json.dumps(task,ensure_ascii=False),encoding="utf-8");os.replace(tmp,folder/(task["request_id"]+".json"))\n(WORK/"manager_submitted.json").write_text(json.dumps({"at":time.time(),"request_id":task["request_id"],"approved_item":"I-213","producer_pid":os.getpid()}),encoding="utf-8")\nprint("Approved task submitted. End this manager turn now; do not wait for worker.")\n',encoding='utf-8')
    names=['LP I178 Manager Exit Host '+stamp,'LP I178 Manager Exit Kernel '+stamp]
    for name,mode in zip(names,('host','kernel')):
        args='"'+str(entry)+'" '+mode
        code="$n='"+name+"';$u=[Security.Principal.WindowsIdentity]::GetCurrent().Name;$p=New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Limited;$a=New-ScheduledTaskAction -Execute 'C:\\Python313\\pythonw.exe' -Argument '"+args+"' -WorkingDirectory '"+str(ROOT)+"';$s=New-ScheduledTaskSettingsSet -Hidden -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15);Register-ScheduledTask -TaskName $n -InputObject (New-ScheduledTask -Action $a -Principal $p -Settings $s) | Out-Null;Start-ScheduledTask -TaskName $n"
        shell(code)
    host=wait(lambda:call(hp,{'op':'status'}),30)
    # Manager and worker stores are separate single-writer domains.
    os.environ.update(env,LP_HOST_STATE_ROOT=str(work/'manager_runtime'),LP_TURN_RECOVERY_ROOT=str(work/'manager_recovery'),
                      LP_HOST_ROSTER_PATH=str(work/'manager_roster.json'),LP_HOST_REGISTRY_PATH=str(work/'manager_registry.json'))
    sys.path.insert(0,str(ROOT/'lp_sync'));import host_service as hs
    message='你是本次独立验收的临时经理模型。批准任务已经保存，你只运行下面的提交入口一次，提交成功后立刻用一句话回复并结束本回合，不等待执行器，不改其他文件。\nC:/Python313/python.exe "'+str(submit).replace('\\','/')+'"'
    accepted=hs.op_dispatch_new('I-178 disposable manager departure test',message,lane='L9',kind='test',
             request_id='manager-send-'+stamp,run_id='manager-dialogue-'+stamp)
    sid=accepted['session'];child=hs.ensure_child(hs._exec_key_for_sid(sid))
    wait(lambda:(work/'manager_submitted.json').exists() and hs.EXECUTION_STORE.session(sid).get('phase')=='idle',180)
    assert not (work/'job/completed.json').exists(),'worker finished before manager departure; sample unsuitable'
    pid=child.proc.pid;child.proc.stdin.close();child.proc.wait(timeout=15)
    report={'work':str(work),'tasks':names,'host_port':hp,'kernel_port':kp,'host_pid':host['host_pid'],
            'manager_session':sid,'manager_pid':pid,'manager_exit_code':child.proc.returncode,
            'manager_departed_at':time.time(),'accepted':accepted,'worker_completed_before_manager_exit':False,
            'original_task':task,'claim_mode':'test; no production claim','controller_pid':os.getpid()}
    (work/'manager_departed.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'work':str(work),'manager_session':sid,'manager_pid':pid,'manager_exited':True,
                      'worker_not_finished_yet':True,'system_tasks':names},ensure_ascii=False))
    return 0
if __name__=='__main__':raise SystemExit(main())
