# -*- coding: utf-8 -*-
"""
wp_diag_ground.py — 桥接根因嫌疑②诊断：运动学直写对接地机体是否生效
====================================================================
背景：桥接联调第一/二跑失败时机体均处于坠地静止状态（z≈0.04-0.08，
GROUND_Z=0.3 接地联锁高度以下），第三跑成功时机体为 3 m 悬停态。本脚本
隔离检验：kinematic 速度直写（set_linear_velocities）对接地联锁怠速、
且可能已进入 PhysX 睡眠态的机体是否仍然生效。

实验序列（状态机）：
  1) 等场景就绪（t<60 即接管，复用 wp_sender --wait 口径）
  2) 保持段：发 (0,0,3) 悬停参考 10 s —— 对照组（已知能跟）
  3) 下降段：30 s 线性降到 (0,0,0.05) —— 制造接地态
  4) 接地观察段：15 s 不发指令，记录 z/vz/freq（联锁怠速特征）
  5) 恢复段：发 (0,0,3) 爬升参考 30 s —— 判定段
  6) 判定：恢复段内 z 曾 >0.5 m → 直写对接地机体生效（嫌疑②排除）；
         z 始终 <0.1 m → 嫌疑②成立；先不后能 → 记录时序特征
全程 2 s 采样 /state 写 logs/wp_diag_ground_<ts>.csv，事件行实时打印。
"""
from __future__ import annotations

import csv
import json
import math
import os
import socket
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_URL = "http://localhost:8090/state"
OUT_FMT = os.path.join(ROOT, "logs", "wp_diag_ground_%s.csv")

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch_state():
    try:
        with _OPENER.open(STATE_URL, timeout=0.8) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def send_cmd(sock, host, port, pos, vel=(0, 0, 0)):
    sock.sendto(json.dumps({
        "type": "pos_cmd", "t": round(time.time() % 10000, 2),
        "position": list(pos), "velocity": list(vel), "yaw": 0.0,
    }).encode(), (host, port))


def main():
    host, port = "127.0.0.1", 9002
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    csv_path = OUT_FMT % time.strftime("%m%d_%H%M%S")
    rows = []

    def sample(tag):
        st = fetch_state()
        if not st or not st.get("pose"):
            print(f"[diag] {tag}: /state 不可达", flush=True)
            return None
        p = st["pose"][:3]
        v = (st.get("vel") or [0, 0, 0])[:3]
        rec = [time.strftime("%H:%M:%S"), tag, st.get("t", -1),
               p[0], p[1], p[2], v[0], v[1], v[2], st.get("freq", -1)]
        rows.append(rec)
        print(f"[diag] {tag}: t={st.get('t', -1):7.1f} z={p[2]:+.3f} "
              f"vz={v[2]:+.3f} freq={st.get('freq', -1):.2f}", flush=True)
        return st

    # 1) 等就绪
    print("[diag] 等待场景就绪（t<60）…", flush=True)
    t0 = time.time()
    while time.time() - t0 < 300:
        st = fetch_state()
        if st and st.get("pose") and 0 < st.get("t", 0) < 60:
            break
        time.sleep(2)
    else:
        print("[diag] 就绪超时", flush=True)
        return 3

    def phase(name, dur, pos_fn, rate=20):
        print(f"[diag] ===== {name} ({dur}s) =====", flush=True)
        t0 = time.time()
        last = -1.0
        while time.time() - t0 < dur:
            el = time.time() - t0
            send_cmd(sock, host, port, pos_fn(el))
            if time.time() - last >= 2.0:
                last = time.time()
                sample(name)
            time.sleep(1.0 / rate)

    # 2) 保持段
    phase("hover3m", 10, lambda el: (0, 0, 3.0))
    # 3) 下降段
    phase("descend", 30, lambda el: (0, 0, max(0.05, 3.0 - 3.0 * el / 30)))
    # 4) 接地观察（不发指令）
    print("[diag] ===== ground_idle (15s, 不发指令) =====", flush=True)
    for _ in range(8):
        sample("ground_idle")
        time.sleep(2)
    # 5) 恢复段
    phase("recover", 30, lambda el: (0, 0, 3.0))

    # 6) 判定
    zs = [r[5] for r in rows if r[1] == "recover"]
    verdict = "未取到恢复段样本"
    if zs:
        zmax = max(zs)
        if zmax > 0.5:
            verdict = (f"嫌疑②排除：恢复段机体离地至 z={zmax:.2f}m，"
                       f"运动学直写对接地机体生效")
        elif max(zs) < 0.1:
            verdict = (f"嫌疑②成立：恢复段机体始终贴地（z_max={zmax:.2f}m），"
                       f"直写对接地/怠速/睡眠机体无效")
        else:
            verdict = (f"部分生效：恢复段 z_max={zmax:.2f}m（0.1~0.5 之间），"
                       f"需人工看时序")
    print(f"[diag] ===== 判定：{verdict} =====", flush=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["wallclock", "phase", "sim_t", "x", "y", "z",
                    "vx", "vy", "vz", "freq"])
        w.writerows(rows)
    print(f"[diag] 数据落盘 {csv_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
