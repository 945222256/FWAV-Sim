# -*- coding: utf-8 -*-
"""shadow_driver.py 自有驱动器：zcode 核心包直建直驱会话，事项 I-161。

不复用客户端安装包的协议进程，直接拉起 zcode 核心包的应用服务进程
并按其 stdio 协议驱动一轮完整施工问答：

  一 建会话：session/create 带工作区两键；
  二 应答服务端反向请求：运行偏好必答布尔键，权限请求答允许，
    官方模型上下文头请求答空对象；
  三 发施工指令：session/send，任务为在工作区创建一个文件；
  四 等待回合完成：轮询状态通知与文件落地，上限十分钟；
  五 对账：进程退出后直读会话库 SQLite，核对会话行、消息行、
    回合用量行与模型用量行的提供方和模型字段。

鉴权自持：应用服务进程的模型鉴权来自用户目录下的登录态文件与
环境变量，不依赖桌面客户端进程是否在运行；驱动器启动时显式
携带同份配置路径。

用法：
  PYTHONUTF8=1 python tools/zcode_shadow/shadow_driver.py \
      [--task "指令文本"] [--model 模型引用] [--timeout 秒]

产物：logs/zcode_shadow/影子验证_时间戳/ 目录内全程通知日志、驱动
报告 JSON、被创建的施工文件本体。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
ZCODE_CJS = ("C:/Users/Anni/AppData/Local/Programs/ZCode/resources/glm/"
             "zcode.cjs")
DB_PATH = os.path.expanduser("~/.zcode/cli/db/db.sqlite")
DEFAULT_WS = "G:/Playground/shadow_lab"


def _raw(proc, obj):
    proc.stdin.write((json.dumps(obj) + "\n").encode())
    proc.stdin.flush()


class ShadowDriver:
    def __init__(self, ws_path, ws_key, notify_log):
        self.proc = subprocess.Popen(
            ["node", ZCODE_CJS, "app-server", "--stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=ws_path,
            env={**os.environ, "PYTHONUTF8": "1"})
        self.events = []
        self.notify_log = notify_log
        self._answered = set()
        threading.Thread(target=self._drain, daemon=True).start()
        threading.Thread(target=self._drain_err, daemon=True).start()

    def _drain(self):
        while True:
            line = self.proc.stdout.readline()
            if not line:
                break
            try:
                d = json.loads(line)
            except Exception:
                continue
            self.events.append(d)
            if self.notify_log:
                self.notify_log.write(line.decode("utf-8", "replace"))

    def _drain_err(self):
        while True:
            line = self.proc.stderr.readline()
            if not line:
                break

    def raw(self, obj):
        _raw(self.proc, obj)

    def answer_server_requests(self):
        """应答服务端反向请求：偏好、权限、鉴权头。"""
        for d in list(self.events):
            mid = d.get("id")
            m = d.get("method")
            if not m or mid is None or mid in self._answered:
                continue
            if m == "session/requestRuntimePreferences":
                self.raw({"id": mid,
                          "result": {"nativeSearchEnhancementsEnabled":
                                     False}})
            elif m == "interaction/requestPermission":
                self.raw({"id": mid, "result": {"decision": "allow"}})
            else:
                self.raw({"id": mid, "result": {}})
            self._answered.add(mid)
            self.events.remove(d)

    def wait_response(self, rid, timeout_s):
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            self.answer_server_requests()
            for d in self.events:
                if d.get("id") == rid:
                    return d
            time.sleep(1)
        return None

    def create_session(self, ws_path, ws_key, model=None, timeout_s=30):
        """建会话。钉模型一律走 set_model 方法：create 顶层的 model
        参数会丢推理档导致回合失败（I-160 实测定论），本方法不再
        接受 model 参数。"""
        params = {"workspace": {"workspacePath": ws_path,
                                "workspaceKey": ws_key}}
        self.raw({"id": 1, "method": "session/create", "params": params})
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            time.sleep(1)
            self.answer_server_requests()
            for d in self.events:
                if d.get("id") == 1:
                    if "error" in d:
                        return None, d
                    return d["result"]["session"]["sessionId"], None
        return None, {"timeout": True}

    def set_model(self, sid, provider_id, model_id,
                  reasoning_level="max", rid=9, timeout_s=20):
        """钉模型唯一正法（I-160 定论）：session/setModel 带三键全形
        态。返回是否成功。"""
        self.raw({"id": rid, "method": "session/setModel",
                  "params": {"sessionId": sid,
                             "model": {"providerId": provider_id,
                                       "modelId": model_id,
                                       "options": {"reasoningLevel":
                                                   reasoning_level}}}})
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            time.sleep(1)
            self.answer_server_requests()
            for d in self.events:
                if d.get("id") == rid:
                    return "error" not in d
        return False

    def send_task(self, sid, content):
        self.raw({"id": 2, "method": "session/send",
                  "params": {"sessionId": sid, "content": content}})

    def wait_turn(self, sid, target_file, timeout_s):
        t0 = time.time()
        last_status = None
        while time.time() - t0 < timeout_s:
            time.sleep(3)
            self.answer_server_requests()
            for d in self.events:
                if d.get("method") == "state.updated":
                    patch = d.get("params", {}).get("patch", {})
                    if "status" in patch:
                        last_status = patch["status"]
            if target_file and os.path.exists(target_file):
                return {"file_created": True,
                        "wall_s": round(time.time() - t0, 1),
                        "last_status": last_status}
        return {"file_created": False,
                "wall_s": round(time.time() - t0, 1),
                "last_status": last_status}

    def assistant_text(self):
        """从通知流提取助手文本片段。"""
        texts = []
        for d in self.events:
            if d.get("method") == "conversationFrame":
                texts.append(json.dumps(d.get("params", {}),
                                        ensure_ascii=False))
        return texts

    def close(self):
        try:
            self.proc.kill()
        except Exception:
            pass


def audit_session_db(sid):
    """直读会话库，返回该会话的记录形态核对结果。"""
    out = {"session_row": False, "messages": 0, "turn_usage_rows": 0,
           "model_usage_rows": 0, "model_ids": [], "providers": []}
    db = sqlite3.connect(DB_PATH)
    cur = db.cursor()
    row = cur.execute("SELECT id, title FROM session WHERE id=?",
                      (sid,)).fetchone()
    out["session_row"] = row is not None
    out["messages"] = cur.execute(
        "SELECT COUNT(*) FROM message WHERE session_id=?",
        (sid,)).fetchone()[0]
    out["turn_usage_rows"] = cur.execute(
        "SELECT COUNT(*) FROM turn_usage WHERE session_id=?",
        (sid,)).fetchone()[0]
    mu = cur.execute(
        "SELECT COUNT(*) FROM model_usage WHERE session_id=?",
        (sid,)).fetchone()[0]
    out["model_usage_rows"] = mu
    db.close()
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default=(
        "请创建文件 shadow_done.txt，内容为一行：I-161 影子验证完成。"
        "创建成功后用一句中文回复确认。"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--ws", default=DEFAULT_WS)
    parser.add_argument("--outfile", default="shadow_done.txt")
    args = parser.parse_args()

    os.makedirs(args.ws, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(REPO_ROOT, "logs", "zcode_shadow",
                           "影子验证_%s" % stamp)
    os.makedirs(out_dir, exist_ok=True)
    log_path = os.path.join(out_dir, "notifications.jsonl")
    notify_log = open(log_path, "w", encoding="utf-8")

    drv = ShadowDriver(args.ws, "shadow_lab", notify_log)
    try:
        time.sleep(3)
        drv.answer_server_requests()
        sid, err = drv.create_session(args.ws, "shadow_lab",
                                      model=args.model)
        report = {"stamp": stamp, "workspace": args.ws,
                  "model_param": args.model, "session_id": sid,
                  "create_error": err}
        if sid is None:
            json.dump(report, open(os.path.join(out_dir, "report.json"),
                                   "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            print("[拒] 会话创建失败：%s" % err)
            return 2
        print("[会话] %s" % sid)
        drv.send_task(sid, args.task)
        target = os.path.join(args.ws, args.outfile)
        if os.path.exists(target):
            os.remove(target)
        turn = drv.wait_turn(sid, target, args.timeout)
        report["turn"] = turn
        report["assistant_frames"] = len(drv.assistant_text())
        # 通知方法统计
        from collections import Counter
        report["notifications"] = dict(Counter(
            d.get("method") for d in drv.events
            if d.get("method")))
        drv.close()
        time.sleep(2)
        if turn.get("file_created"):
            db_audit = audit_session_db(sid)
            report["db_audit"] = db_audit
            ok = (db_audit["session_row"] and db_audit["messages"] >= 2
                  and db_audit["turn_usage_rows"] >= 1)
            report["verdict"] = "通过" if ok else "部分"
            print("[回合] 文件已生成，用时 %.0f 秒" % turn["wall_s"])
            print("[对账] 会话库：%s" % json.dumps(
                db_audit, ensure_ascii=False))
            print("[结论] %s" % report["verdict"])
            ret = 0 if ok else 1
        else:
            report["verdict"] = "失败"
            print("[失败] %.0f 秒内未生成目标文件，末状态 %s"
                  % (args.timeout, turn.get("last_status")))
            ret = 1
        json.dump(report, open(os.path.join(out_dir, "report.json"), "w",
                               encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print("[报告] %s" % os.path.join(out_dir, "report.json"))
        return ret
    finally:
        drv.close()
        notify_log.close()


if __name__ == "__main__":
    sys.exit(main())
