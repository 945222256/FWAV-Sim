"""One bounded, system-hosted check; reload the host only when all work is idle."""
import datetime,hashlib,json,pathlib,shutil,socket,sqlite3,subprocess,sys,time,uuid
ROOT=pathlib.Path(__file__).resolve().parent.parent
HERE=ROOT/'logs/hq_intake/beth_lane_concurrency_20261014/lock_recovery_check'
PLAN=HERE/'deferred_host_upgrade_plan.json'
TASK='LP I217 Queue Cleanup Upgrade'
ACTIVE={'active','starting','sending','stopping','busy_wait'}
def shell(code):
    return subprocess.check_output(['powershell.exe','-NoProfile','-Command',code],encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW).strip()
def call():
    with socket.create_connection(('127.0.0.1',58771),timeout=5) as sock:
        sock.sendall(b'{"op":"status"}\n');return json.loads(sock.makefile('rb').readline())
def write(path,obj):
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
def record(status,**fields):
    write(HERE/'deferred_host_upgrade_state.json',{'at':datetime.datetime.now().astimezone().isoformat(),'status':status,**fields})
def idle(host):
    if any(v.get('phase') in ACTIVE for v in host.get('sessions',{}).values()):return False
    with sqlite3.connect('file:C:/Users/Anni/.zcode/cli/db/db.sqlite?mode=ro',uri=True) as db:
        return not any(db.execute('SELECT 1 FROM tool_usage WHERE session_id=? AND completed_at IS NULL AND started_at>? LIMIT 1',(sid,int(time.time()*1000)-24*3600*1000)).fetchone() for sid in host.get('sessions',{}))
def alive(pid):return shell('[bool](Get-Process -Id '+str(int(pid))+' -ErrorAction SilentlyContinue)').lower()=='true'
def main():
    plan=json.loads(PLAN.read_text(encoding='utf-8'));target=plan['target_sha256']
    expected=plan.get('source_hashes') or {'dispatch_file_queue.py':target}
    if any(hashlib.sha256((ROOT/'lp_sync'/name).read_bytes()).hexdigest()!=digest for name,digest in expected.items()):
        record('waiting_source_review',reason='runtime source changed after verification; hold upgrade');return
    before=call()
    if '--check' in sys.argv:
        print(json.dumps({'host_pid':before['host_pid'],'idle':idle(before),'current_hash':before.get('source_sha256',{}).get('dispatch_file_queue.py'),'target_hash':target}));return
    if before.get('source_sha256',{}).get('dispatch_file_queue.py')==target:
        record('complete',host_pid=before['host_pid'],target_sha256=target,already_loaded=True)
        shell("Disable-ScheduledTask -TaskName '"+TASK+"' | Out-Null");return
    if not idle(before):record('waiting_busy',host_pid=before['host_pid']);return
    paths=[ROOT/'lp_sync/dispatch_queue/control.json',ROOT/'lp_sync/kernel_state/control.json']
    old={str(p):json.loads(p.read_text(encoding='utf-8')) for p in paths}
    if any(v.get('mode')!='run' for v in old.values()):record('waiting_other_maintenance');return
    token='i217-queue-upgrade-'+uuid.uuid4().hex
    def restore():
        for p in paths:
            if json.loads(p.read_text(encoding='utf-8')).get('maintenance_owner')==token:write(p,old[str(p)])
    try:
        for p in paths:write(p,{'mode':'halt','maintenance_owner':token,'reason':'I-217 tested cleanup upgrade; idle-only bounded reload','changed_at':datetime.datetime.now().isoformat()})
        time.sleep(.5)
        drain_deadline=time.monotonic()+10
        while True:
            verified=call();processing=list((ROOT/'lp_sync/dispatch_queue/processing').glob('*.json'))
            if not idle(verified):record('waiting_activity_race');return
            if not processing:break
            if time.monotonic()>drain_deadline:record('waiting_request_drain');return
            time.sleep(.3)
        checkpoint=HERE/('host_checkpoint_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S'));checkpoint.mkdir()
        for relative in ('runtime/execution.json','kernel_state/state.json','kernel_state/lane_queues.json','dispatch_roster.json','dispatch_registry.json','lane_workers_roster.json'):
            src=ROOT/'lp_sync'/relative
            if src.exists():
                dst=checkpoint/relative;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
        record('reloading_idle_host',old_host_pid=verified['host_pid'],checkpoint=str(checkpoint))
        shell("Disable-ScheduledTask -TaskName 'LP Production Host I208' | Out-Null; Stop-ScheduledTask -TaskName 'LP Production Host I208'")
        previous=[verified['host_pid']]+[v['pid'] for v in verified.get('executors',{}).values()]
        deadline=time.monotonic()+20
        while any(alive(p) for p in previous):
            if time.monotonic()>deadline:raise RuntimeError('old idle processes remain; refuse duplicate startup')
            time.sleep(.2)
        shell("Enable-ScheduledTask -TaskName 'LP Production Host I208' | Out-Null; Start-ScheduledTask -TaskName 'LP Production Host I208'")
        deadline=time.monotonic()+20
        while True:
            try:
                after=call()
                if after['host_pid']!=verified['host_pid'] and after.get('source_sha256',{}).get('dispatch_file_queue.py')==target:break
            except OSError:pass
            if time.monotonic()>deadline:raise RuntimeError('new host code not confirmed')
            time.sleep(.3)
        record('complete',old_host_pid=verified['host_pid'],host_pid=after['host_pid'],target_sha256=target,no_active_work_at_switch=True,checkpoint=str(checkpoint))
        shell("Disable-ScheduledTask -TaskName '"+TASK+"' | Out-Null")
    finally:
        restore()
        shell("Enable-ScheduledTask -TaskName 'LP Production Host I208' | Out-Null")
if __name__=='__main__':
    try:main()
    except Exception as exc:
        record('retry_needed',error=str(exc)[:300]);raise
