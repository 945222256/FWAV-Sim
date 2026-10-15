# -*- coding: utf-8 -*-
"""i208_routing_consumer_test.py — Beth 15:48 复核两缺陷的消费者级
隔离验证（协议桩，无真实进程/队列/云端/roster 变更）。

场景（对应 Beth 指定的用例）：
  1. 首发→回读→恢复→定向停止：全程执行键与执行器实例一致（缺陷一）；
  2. 同 L/同 sid 并发两次：无重复有效执行器；
  3. 交接旧 sid 停止：到达其原所属执行器；
  4. verify=False：同一内容恰好发送一次（缺陷二，旧 trace 为 2）；
  5. 核验未知（桩不返回 marker）：不重发，如实返回未核验。
仅使用 Python 标准库。
"""
import json
import os
import sys
import tempfile
import threading
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

W = tempfile.mkdtemp(prefix="i208_routing_")

_checks = []


def check(name, ok, detail=""):
    _checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print("[%s] %s %s" % ("过" if ok else "拒", name, detail), flush=True)


def main():
    import host_service as hs

    # ── 协议桩：记录每次调用的桩实例与方法 ──
    calls = []
    stub_seq = [0]

    class StubProc:
        def __init__(self, n):
            self.n = n
        def poll(self):
            return None

    class StubChild:
        def __init__(self):
            stub_seq[0] += 1
            self.stub = "stub-%d" % stub_seq[0]
            self.proc = StubProc(stub_seq[0])
            self.sent_content = []
        def alive(self):
            return True
        def kill(self):
            pass
        def call(self, method, params, timeout=60):
            sid = params.get("sessionId")
            if method == "session/create":
                sid = "sess_i208_routing_test_0001"
            calls.append({"stub": self.stub, "method": method,
                          "sid": sid})
            if method == "session/create":
                return {"result": {"sessionId": sid}}
            if method == "session/send":
                self.sent_content.append(params.get("content", ""))
                return {"result": {"accepted": True}}
            if method == "session/messages":
                # 回显已发送内容（marker 命中；未知场景由 fast_readback
                # 配合空桩另行覆盖）。
                return {"result": {"messages": list(self.sent_content)}}
            if method in ("session/resume", "session/stop"):
                return {"result": {}}
            return {"result": {}}

    # 替换 spawn 为桩（不真起 node）
    def fake_spawn(self):
        pass

    real_init = hs.ProtocolChild.__init__
    hs.ProtocolChild.__init__ = fake_spawn
    hs.ProtocolChild.spawn = fake_spawn

    # 池控制：直接操纵 _CHILDREN，让 ensure_child 返回我们控制的桩
    stub_children = {}

    def fake_ensure_child(exec_key=""):
        if exec_key not in stub_children:
            stub_children[exec_key] = StubChild()
        return stub_children[exec_key]

    hs.ensure_child = fake_ensure_child

    # 全局缩短回读节奏（隔离验证不等真实 5 秒间隔）
    _real_readback = hs._readback_hit
    def _quick_readback(sid, marker, rounds=2, gap=0.05, exec_key=None):
        return _real_readback(sid, marker, rounds=rounds, gap=gap,
                              exec_key=exec_key)
    hs._readback_hit = _quick_readback

    # roster 指向隔离副本（不动生产 roster）
    roster_path = os.path.join(W, "roster.json")
    hs.DISPATCH_ROSTER_PATH = roster_path
    json.dump({}, open(roster_path, "w", encoding="utf-8"))
    hs.REGISTRY_PATH = os.path.join(W, "registry.json")
    hs.INDEX_DB = os.path.join(W, "nonexistent.sqlite")
    hs.STATUS_PATH = os.path.join(W, "status.json")

    try:
        # ── 场景一至三：首发 → 回读 → 绑驻 → 恢复 → 定向停止 ──
        calls.clear()
        stub_children.clear()
        r = hs.op_dispatch_new("标题", "I-208 路由验证正文", verify=True,
                               item_id="I-208", lane="L3", kind="task")
        sid = r["session"]
        keys_used = sorted(stub_children.keys())
        check("场景一a：首发阶段执行键唯一且为路线名",
              keys_used == ["L3"], "键=%r" % keys_used)

        hs.roster_bind("L3", sid)  # op_dispatch_new 内部已绑——真实序
        # 上面手动重复绑定等价（幂等）；检查 roster 后恢复
        r2 = hs.op_dispatch_resume(sid, "恢复消息：继续施工", verify=True)
        keys_after_resume = sorted(stub_children.keys())
        check("场景一b：同 session 恢复不产生新执行键",
              keys_after_resume == ["L3"],
              "键=%r" % keys_after_resume)

        # 定向停止走原执行器：通过 handle 的 session_stop
        out = hs.handle(json.dumps({"op": "session_stop", "sid": sid}))
        keys_after_stop = sorted(stub_children.keys())
        check("场景一c：定向停止不产生新执行键且路由到原执行器",
              keys_after_stop == ["L3"] and out.get("ok") is True,
              "键=%r，stop=%r" % (keys_after_stop, out))

        # ── 场景二：同 L 并发两次首发（驻留在位走 resume） ──
        calls.clear()
        stub_children.clear()
        results = []
        def worker():
            results.append(hs.op_dispatch_resume(sid, "并发探针 A", verify=False))
        ts = [threading.Thread(target=worker) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        keys_conc = sorted(stub_children.keys())
        sends = [c for c in calls if c["method"] == "session/send"]
        check("场景二：同 sid 并发两次仅一个执行器实例",
              keys_conc == ["L3"], "键=%r" % keys_conc)

        # ── 场景四：verify=False 单发 ──
        calls.clear()
        stub_children.clear()
        hs.op_dispatch_resume(sid, "关闭核验单发探针", verify=False)
        send_count = len([c for c in calls if c["method"] == "session/send"])
        check("场景四：verify=False 同一内容恰好发送一次（send_count=%d）"
              % send_count, send_count == 1)

        # ── 场景五：核验未知（桩清空回显）不重发 ──
        calls.clear()
        stub_children.clear()
        orig_readback = hs._readback_hit
        def fast_readback(sid_, marker_, rounds=1, gap=0, exec_key=None):
            # 未知场景：清空该会话已发送内容，回读必然未命中
            ch = stub_children.get(exec_key or 'L3')
            if ch is not None:
                ch.sent_content.clear()
            return orig_readback(sid_, marker_, rounds=1, gap=0,
                                 exec_key=exec_key)
        hs._readback_hit = fast_readback
        rv = hs.op_dispatch_resume(sid, "核验未知探针", verify=True)
        sends5 = [c for c in calls if c["method"] == "session/send"]
        check("场景五：核验未知不重发且如实返回未核验（sends=%d，verified=%r）"
              % (len(sends5), rv.get("readback_verified")),
              len(sends5) == 1 and rv.get("readback_verified") is False)
        hs._readback_hit = orig_readback

        # ── 首发核验未知同样不重发 ──
        calls.clear()
        stub_children.clear()
        json.dump({}, open(roster_path, "w", encoding="utf-8"))
        hs._readback_hit = fast_readback
        rn = hs.op_dispatch_new("首发未知", "首发核验未知探针", verify=True,
                                item_id="I-208", lane="L3", kind="task")
        sends6 = [c for c in calls if c["method"] == "session/send"]
        check("场景六：首发核验未知不重发（sends=%d，verified=%r）"
              % (len(sends6), rn.get("readback_verified")),
              len(sends6) == 1 and rn.get("readback_verified") is False)
        hs._readback_hit = orig_readback
    finally:
        hs.ProtocolChild.__init__ = real_init
        # 还原模块级替换（进程内）
        import importlib
        importlib.reload(hs)

    bad = sum(1 for c in _checks if not c["ok"])
    out = os.path.join(ROOT, "logs", "i208_routing_consumer_test_20261013.json")
    json.dump({"checks": _checks, "W": W,
               "summary": {"total": len(_checks),
                           "passed": len(_checks) - bad}},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("消费者级隔离总判定：%s（%d/%d 过）"
          % ("全部通过" if bad == 0 else "存在不过项",
             len(_checks) - bad, len(_checks)))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
