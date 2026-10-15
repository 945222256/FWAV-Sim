"""Real ZCode protocol/PID isolation, independent of model quota.

This proves process and protocol isolation. Model-driven work has a separate
acceptance report and is never inferred from these RPC results.
"""
import datetime
import json
import os
import pathlib
import time


def main():
    root = pathlib.Path(__file__).resolve().parents[1]
    out = root / "logs/runtime_protocol_acceptance" / datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True)
    for key, path in {"LP_HOST_STATE_ROOT": out / "runtime", "LP_HOST_ROSTER_PATH": out / "roster.json",
                      "LP_HOST_REGISTRY_PATH": out / "registry.json", "LP_HOST_STATUS": out / "status.json",
                      "LP_TURN_RECOVERY_ROOT": out / "turn_recovery"}.items():
        os.environ[key] = str(path)
    import host_service as host
    children = []
    report = {"kind": "real protocol and process isolation; no model work", "checks": []}
    try:
        a, b = host.ensure_child("test-L3"), host.ensure_child("test-L5")
        children += [a, b]
        created_a = host.create_persistent_session(a, "isolated L3", "protocol-L3")
        created_b = host.create_persistent_session(b, "isolated L5", "protocol-L5")
        sa = host.sid_of(created_a)
        sb = host.sid_of(created_b)
        if not sa or not sb:
            raise RuntimeError("session create rejected: " + json.dumps([created_a.get("error"), created_b.get("error")], ensure_ascii=False))
        for sid, child in [(sa, a), (sb, b)]:
            child.loaded_sessions.add(sid)
            host.EXECUTION_STORE.bind(sid, child.executor_key, executor_pid=child.proc.pid, phase="idle")
        report["checks"].append({"name": "different actual executor PIDs", "passed": a.proc.pid != b.proc.pid,
                                  "L3_pid": a.proc.pid, "L5_pid": b.proc.pid})
        responses = []
        for number in range(20):
            if number == 5:a.kill()
            result = b.call("session/messages", {"sessionId": sb}, timeout=10)
            responses.append({"at": time.time(), "success": not result.get("error")})
            time.sleep(.1)
        report["checks"].append({"name": "L5 RPCs continue through L3 exit", "passed":
                   all(v["success"] for v in responses) and b.proc.poll() is None, "responses": responses})
        new = host.ensure_child("test-L3");children.append(new)
        resumed = new.call("session/resume", {**host.WS, "sessionId": sa})
        report["checks"].append({"name": "L3 protocol reuses original session after process restart",
                                  "passed": new.proc.pid != a.proc.pid and not resumed.get("error"),
                                  "old_pid": a.proc.pid, "new_pid": new.proc.pid, "session_id": sa,
                                  "resume_error": resumed.get("error")})
        report["checks"].append({"name": "one effective executor per key", "passed":
                   host.ensure_child("test-L3") is new and host.ensure_child("test-L5") is b})
        report["passed"] = all(v["passed"] for v in report["checks"])
    except Exception as exc:
        report.update(passed=False, error=repr(exc))
    finally:
        for child in children:
            if child.alive():child.kill()
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
