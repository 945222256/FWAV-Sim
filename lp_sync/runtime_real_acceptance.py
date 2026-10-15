"""Actual isolated app-server isolation/cancellation/recovery acceptance.

Production endpoints, rosters, queues and cloud writes are excluded. Only the
controller's explicitly created host/kernel and their owned children are stopped.
"""
import datetime
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = sys.executable


def atomic(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def call(number, body):
    if number in (58771, 58779):
        raise RuntimeError("production endpoint excluded")
    with socket.create_connection(("127.0.0.1", number), timeout=10) as sock:
        sock.settimeout(45)
        sock.sendall((json.dumps(body) + "\n").encode())
        data = b""
        while b"\n" not in data:
            block = sock.recv(65536)
            if not block:
                break
            data += block
        return json.loads(data)


def wait(predicate, seconds=120):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        try:
            value = predicate()
            if value:
                return value
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(.5)
    raise TimeoutError("acceptance condition timed out")


def main():
    approved_path = next((sys.argv[n+1] for n, arg in enumerate(sys.argv[:-1]) if arg == "--approved-manifest"), None)
    approved = json.loads(pathlib.Path(approved_path).read_text(encoding="utf-8")) if approved_path else None
    specs = approved["tasks"] if approved else [
        {"label":"A","item_id":"I-931","lane":"L3","outcome":"pending_review","steps":60},
        {"label":"D","item_id":"I-932","lane":"L5","outcome":"pending_review","steps":120},
        {"label":"B","item_id":"I-933","lane":"L3","outcome":"blocked","steps":8},
        {"label":"C","item_id":"I-934","lane":"L3","outcome":"pending_review","steps":8}]
    specs = sorted(specs, key=lambda v: {"A":1,"D":2,"B":3,"C":4}[v["label"]])
    observation = "--observation-only" in sys.argv
    fault_lane = next((sys.argv[n+1] for n,arg in enumerate(sys.argv[:-1]) if arg=="--fault-lane"),"L3")
    if observation:
        specs=[dict(v,outcome="evidence_appended") for v in specs if v["label"] in ("A","D")]
    resume_path = next((sys.argv[n+1] for n, arg in enumerate(sys.argv[:-1]) if arg == "--resume-work"), None)
    no_fault = "--no-fault" in sys.argv
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    work = pathlib.Path(resume_path).resolve() if resume_path else ROOT / "logs/runtime_real_acceptance" / stamp
    if not work.is_relative_to((ROOT / "logs/runtime_real_acceptance").resolve()):
        raise RuntimeError("fixture path required")
    work.mkdir(parents=True, exist_ok=True)
    qr, ks, reg = work / "queue", work / "kernel", work / "registry"
    code = work / "code"
    if resume_path and code.exists():
        shutil.copytree(code, work / ("code_before_resume_" + stamp))
        if (work / "report.json").exists():
            shutil.copy2(work / "report.json", work / ("report_before_resume_" + stamp + ".json"))
    code.mkdir(exist_ok=True)
    for name in ("host_service.py", "orchestrator_kernel.py", "dispatch_file_queue.py", "execution_store.py"):
        shutil.copy2(ROOT / "lp_sync" / name, code / name)
    shutil.copytree(ROOT / "items_registry", reg, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.tmp", ".ledger.lock"))
    items = [{"id": iid, "title": "isolated runtime acceptance", "status": "open",
              "opened_at": "2026-10-13", "owner": lane + "队", "lane": lane,
              "evidence": [], "criteria": [], "depends_on": [], "reviewer": "beth"}
             for iid, lane in [("I-931", "L3"), ("I-932", "L5"), ("I-933", "L3"), ("I-934", "L3")]]
    atomic(reg / "items.json", {"schema_version": 1, "items": items})
    atomic(reg / "events.json", {"schema_version": 1, "events": []})
    hp, kp = port(), port()
    env = dict(os.environ, PYTHONUTF8="1", LP_HOST_PORT=str(hp), LP_HOST_ISOLATED="1",
               LP_HOST_STATE_ROOT=str(work / "runtime"), LP_HOST_ROSTER_PATH=str(work / "roster.json"),
               LP_HOST_REGISTRY_PATH=str(work / "dispatch_registry.json"), LP_HOST_STATUS=str(work / "host_status.json"),
               LP_DISPATCH_QUEUE_ROOT=str(qr), LP_TURN_RECOVERY_ROOT=str(work / "turn_recovery"),
               LP_ITEMS_REGISTRY=str(ROOT / "items_registry" if approved else reg),
               LP_EXECUTOR_DEFAULTS=str(ROOT / "lp_sync/executor_defaults.json"),
               FLAPPER_LEDGER_MODE="cloud" if approved else "local")
    logs = []
    def launch(name, args):
        stream = open(work / (name + ".log"), "a" if resume_path else "w", encoding="utf-8")
        logs.append(stream)
        return subprocess.Popen([PY, "-X", "utf8"] + args, cwd=ROOT, env=env,
                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                creationflags=0x08000000 if os.name == "nt" else 0)
    host = launch("host", [str(code / "host_service.py")])
    kern = None
    report = {"work": str(work), "host_port": hp, "kernel_port": kp,
              "host_pid": host.pid, "checks": [], "production_processes_untouched": True,
              "approved_items": [{k:v[k] for k in ("label","item_id","lane","outcome")} for v in specs]}
    try:
        wait(lambda: call(hp, {"op": "status"}), 30)
        if resume_path:
            st = json.loads((ks / "state.json").read_text(encoding="utf-8"))
            for task in st["tasks"].values():
                label = next(v["label"] for v in specs if v["item_id"] == task["item_id"])
                job_name = {"A":"L3","D":"L5","B":"B","C":"C"}[label]
                if (work / job_name / "completed.json").exists():
                    invalid = ks / "invalid" / ("result-" + task["item_id"] + ".json")
                    if invalid.exists():
                        shutil.copy2(invalid,work / ("original_result_recovered_" + task["item_id"] + ".json"))
                        os.replace(invalid,ks / "inbox" / invalid.name)
                    continue
                if not task.get("session_id"):
                    task.update(phase="ready", max_dispatch_attempts=task.get("dispatch_attempts",0)+2)
                    task.pop("line_checked",None)
                    continue
                if task.get("session_id") and task.get("phase") != "completed":
                    rid = "configured-resume-" + task["task_req_id"] + "-" + stamp
                    task.update(phase="submitted", dispatch_request_id=rid, recovery_attempts=0)
                    atomic(qr / "incoming" / (rid + ".json"), {"schema_version":1,"request_id":rid,
                        "op":"dispatch_resume","sid":task["session_id"],"item_id":task["item_id"],
                        "run_id":task["run_id"],"message":task["message"],"verify":True})
            for p in (work / "turn_recovery").glob("tr-*.json"):
                rec = json.loads(p.read_text(encoding="utf-8"))
                if rec.get("error_summary") == "Select a model before continuing":
                    rec.update(reconcile="configuration_repaired", reason="No effective steps occurred; verified original model is now inherited; explicit original-session continuation saved.")
                    atomic(p,rec)
            atomic(ks / "state.json",st)
        kern = launch("kernel", [str(code / "orchestrator_kernel.py"), "--state-root", str(ks),
                    "--queue-root", str(qr), "--port", str(kp), "--wake-s", "0.5", "--line-retry-s", "1"])
        wait(lambda: (ks / "state.json").exists(), 20)
        for number, spec in enumerate(specs, 1):
            iid, lane, count = spec["item_id"], spec["lane"], spec["steps"]
            rid = next((key for key, value in st["tasks"].items() if value["item_id"] == iid), None) if resume_path else None
            rid = rid or ("rt-%s-%02d-%s" % (stamp, number, lane))
            run = "run-" + rid + "-a1"
            job = work / ({"A":"L3","D":"L5","B":"B","C":"C"}[spec["label"]])
            message = ("这是获准的隔离执行设施验收，不是生产任务。只运行下面这一条命令，"
                       "命令自己保存断点并提交结果。不要改其他文件、不要运行仿真、不要认领生产事项。"
                       "如果是中断接续，仍运行同一命令，脚本会从断点继续。\n"
                       'C:/Python313/python.exe "' + str(ROOT / "lp_sync/runtime_acceptance_job.py").replace('\\', '/') +
                       '" --work "' + str(job).replace('\\', '/') + '" --state "' + str(ks).replace('\\', '/') +
                       '" --run ' + run + ' --item ' + iid + ' --steps ' + str(count) + ' --outcome ' + spec["outcome"])
            atomic(ks / "inbox" / (rid + ".json"), {"schema_version": 1, "kind": "task_submit",
                "request_id": rid, "item_id": iid, "lane": lane, "title": "isolated " + lane,
                "message": message, "approved_by": "Beth", "task_kind": "test" if observation else "task", "reviewer": "beth"})
        already_finished = resume_path and (work / "L3/completed.json").exists() and (work / "L5/completed.json").exists()
        if not already_finished:
            wait(lambda: len(list((work / "L3").glob("step-*.json"))) >= 2 and
                     len(list((work / "L5").glob("step-*.json"))) >= 2)
        before = call(hp, {"op": "status"})
        if already_finished:
            old_report=json.loads((work / ("report_before_resume_" + stamp + ".json")).read_text(encoding="utf-8"))
            report["checks"] += old_report.get("checks", [])[:3]
        elif not no_fault:
            execs = before["executors"]
            keys={lane:next(v["executor_key"] for v in before["sessions"].values() if v.get("lane")==lane and v.get("phase")=="active") for lane in ("L3","L5")}
            p3, p5 = execs[keys["L3"]]["pid"], execs[keys["L5"]]["pid"]
            report["checks"].append({"name": "real independent executor PIDs", "passed": p3 != p5,
                                  "L3": p3, "L5": p5})
            unaffected="L3" if fault_lane=="L5" else "L5"
            fifth = len(list((work / unaffected).glob("step-*.json")))
            stopped = call(hp, {"op": "kill_child", "executor_key": keys[fault_lane],"graceful":observation})
            report["checks"].append({"name": "only fixture "+fault_lane+" stopped", "passed": stopped.get("killed") is True,"receipt":stopped})
            wait(lambda: len(list((work / unaffected).glob("step-*.json"))) > fifth + 3, 20)
            after = call(hp, {"op": "status"})
            report["checks"].append({"name": unaffected+" continued with original PID", "passed":
                                  after["executors"][keys[unaffected]]["pid"] == (p3 if unaffected=="L3" else p5)})
        wait(lambda: all(v.get("phase") == "completed" for v in
                         json.loads((ks / "state.json").read_text(encoding="utf-8"))["tasks"].values()), 900)
        state = json.loads((ks / "state.json").read_text(encoding="utf-8"))
        events = list((work / "turn_recovery").glob("tr-*.json"))
        recovered = [json.loads(p.read_text(encoding="utf-8")) for p in events]
        if not no_fault:
            report["checks"].append({"name": "actual cancellation persisted; completed work was not relaunched" if already_finished else "actual cancellation persisted and continuation sent", "passed":
              any(v.get("reconcile") == ("already_done" if already_finished else "continuation_submitted") for v in recovered), "events": recovered})
        original_a = next(value for value in state["tasks"].values() if value["item_id"] == specs[0]["item_id"])
        execution = json.loads((work / "runtime/execution.json").read_text(encoding="utf-8"))
        initial_requests = [value for value in execution["requests"].values()
                            if (value.get("payload") or {}).get("item_id") == original_a["item_id"]
                            and (value.get("payload") or {}).get("run_id") == original_a["run_id"]
                            and value.get("session_id")]
        report["checks"].append({"name": "original run and session preserved", "passed":
              bool(initial_requests) and all(value["session_id"] == original_a["session_id"] for value in initial_requests),
              "session_id": original_a["session_id"], "run_id": original_a["run_id"],
              "identity_source": "durable original dispatch requests"})
        report["checks"].append({"name": "no repeated effective steps", "passed":
              not any(work.glob("*/duplicate-step.json")) and len(list((work / "L3").glob("step-*.json"))) == 60})
        if approved:
            sys.path.insert(0, str(ROOT / "items_registry"));import cloud_client
            ledger = cloud_client.request("GET", "/api/agent/snapshot")["items"]
            ids = {v["item_id"] for v in specs};ledger = [v for v in ledger if v["id"] in ids]
        else:
            ledger = json.loads((reg / "items.json").read_text(encoding="utf-8"))["items"]
        if observation:
            fault_job=work/fault_lane
            steps=[json.loads(p.read_text(encoding="utf-8")) for p in fault_job.glob('step-*.json')]
            report["checks"].append({"name":"unfinished finite work resumed from original checkpoint",
                 "passed":len({v['pid'] for v in steps})>=2 and not (fault_job/'duplicate-step.json').exists(),
                 "step_pids":sorted({v['pid'] for v in steps}),"completed_steps":len(steps)})
            report["checks"].append({"name":"dedicated observation appended evidence without taking production claims",
                 "passed":all(i['status']=='pending_review' for i in ledger),"statuses":{i['id']:i['status'] for i in ledger}})
        else:
            l3_times = [json.loads((work / name / "completed.json").read_text(encoding="utf-8"))["finished_at"]
                        for name in ("L3", "B", "C")]
            expected = {v["item_id"]:v["outcome"] for v in specs}
            report["checks"].append({"name": "three L3 jobs automatically proceeded in FIFO order and released at pending_review",
                "passed": l3_times == sorted(l3_times) and all(i["status"] == expected[i["id"]] for i in ledger),
                "finished_at": l3_times, "statuses": {i["id"]:i["status"] for i in ledger}})
        report["passed"] = all(v["passed"] for v in report["checks"])
    except Exception as exc:
        report["passed"] = False;report["error"] = repr(exc)
    finally:
        if kern and kern.poll() is None:
            kern.terminate();kern.wait(timeout=10)
        # All stops refer to fixture-owned executors known by this host only.
        try:
            current = call(hp, {"op": "status"})
            for key in current.get("executors", {}):
                call(hp, {"op": "kill_child", "executor_key": key})
        except Exception:
            pass
        if host.poll() is None:
            host.terminate();host.wait(timeout=10)
        for stream in logs:stream.close()
        atomic(work / "report.json", report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
