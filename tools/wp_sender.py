# -*- coding: utf-8 -*-
"""
wp_sender.py — 航点文件生成 + 桥接联调发送器（work/track-prep 交付件）
====================================================================
两种用法：

  1) 生成航点序列（不占仿真器）：
     python tools/wp_sender.py --generate
     → data/wp_circle_r5.json
       航线：起飞点(0,0,0) → 爬升至(0,0,3.0) → 直线过渡到圆进入点(5,0,3)
             → 逆时针绕半径5m/高度3m圆两圈（切向速度1.5m/s）
             → 直线切出返回(0,0,3) → 下降回(0,0,0)。总时长72s，0.5s一档。

  2) 桥接联调（需 main_sim 已启动，TRACKING_MODE=kinematic）：
     python tools/wp_sender.py --send
     → 按墙钟调度把航点线性插值成 pos_cmd JSON 发往 127.0.0.1:9002
       （字段 position/velocity/yaw/t，与 TrajectoryTracker.set_reference
        及 main_sim _recv_commands 的桥协议一致）；
       同步以 2s 周期采样 http://localhost:8090/state 记录实际位置，
       全程写 logs/wp_bridge_check.log。

字段约定（与 modules/trail_viz.py 期望轨迹接口对齐）：
  waypoints[].position 点列即 TrailViz.set_path(points) 的输入；
  waypoints[].velocity 为前向速度的三轴矢量表达（沿轨迹切向）。
坐标约定：Isaac 世界系（x前 y左 z上），与 demo_cruise_path 同系。

边界纪律：本脚本只新增（tools/wp_* 前缀），不改 config/ 与 modules/。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WP_PATH = os.path.join(ROOT, "data", "wp_circle_r5.json")
LOG_PATH = os.path.join(ROOT, "logs", "wp_bridge_check.log")
STATE_URL = "http://localhost:8090/state"

# 绕过系统代理：localhost 走代理会每次阻塞到 timeout（run_ident.py 2026-09-18 实测坑）；
# 采样 timeout 压到 0.5s：/state 响应慢时宁可丢一次采样也不能阻塞发送节拍
# （联调首跑实测：2s timeout 把 20Hz 发送拖成 ~0.5Hz）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

SAMPLE_EVERY = 4.0        # /state 采样周期 (s)


# ---------------------------------------------------------------- 航线定义
RADIUS = 5.0          # 巡航圆半径 (m)
HEIGHT = 3.0          # 巡航高度 (m)
V_CLIMB = 0.375       # 爬升/下降竖直速度 (m/s)
V_CRUISE = 1.5        # 圆巡航切向速度 (m/s)
T_TRANS = 7.0         # 过渡段时长 (s)
N_LOOPS = 2           # 巡航圈数
DT_WP = 0.5           # 航点采样间隔 (s)


def _build_waypoints():
    """按五阶段拼航点序列，返回 (waypoints, total_duration)。"""
    t_climb = HEIGHT / V_CLIMB                    # 8.0 s
    omega = V_CRUISE / RADIUS                     # 0.3 rad/s
    t_cruise = 2.0 * math.pi * N_LOOPS / omega    # ≈41.89 s
    t0_trans_in = t_climb
    t0_cruise = t_climb + T_TRANS
    t0_trans_out = t0_cruise + t_cruise
    t0_desc = t0_trans_out + T_TRANS
    total = t0_desc + t_climb

    wps = []
    n = int(round(total / DT_WP))
    for i in range(n + 1):
        t = i * DT_WP
        if t <= t0_trans_in:                       # 1 爬升
            z = V_CLIMB * t
            p = [0.0, 0.0, z]
            v = [0.0, 0.0, V_CLIMB]
            ph = "climb"
        elif t <= t0_cruise:                       # 2 直线进入圆
            s = (t - t0_trans_in) / T_TRANS
            p = [RADIUS * s, 0.0, HEIGHT]
            v = [RADIUS / T_TRANS, 0.0, 0.0]
            ph = "transition_in"
        elif t <= t0_trans_out:                    # 3 巡航圆（逆时针）
            th = omega * (t - t0_cruise)
            p = [RADIUS * math.cos(th), RADIUS * math.sin(th), HEIGHT]
            v = [-V_CRUISE * math.sin(th), V_CRUISE * math.cos(th), 0.0]
            ph = "cruise"
        elif t <= t0_desc:                         # 4 直线切出
            s = (t - t0_trans_out) / T_TRANS
            p = [RADIUS * (1.0 - s), 0.0, HEIGHT]
            v = [-RADIUS / T_TRANS, 0.0, 0.0]
            ph = "transition_out"
        else:                                      # 5 下降
            z = HEIGHT - V_CLIMB * (t - t0_desc)
            p = [0.0, 0.0, max(0.0, z)]
            v = [0.0, 0.0, -V_CLIMB]
            ph = "descent"
        yaw = math.degrees(math.atan2(v[1], v[0])) if (v[0] or v[1]) else 0.0
        wps.append({
            "t": round(t, 3),
            "phase": ph,
            "position": [round(c, 4) for c in p],
            "velocity": [round(c, 4) for c in v],
            "yaw": round(yaw, 2),
        })
    return wps, total


def generate():
    wps, total = _build_waypoints()
    doc = {
        "meta": {
            "name": "wp_circle_r5",
            "description": "起飞→r=5m/h=3.0m巡航圆×2圈→返回 航点序列",
            "frame": "Isaac world (x fwd, y left, z up)",
            "radius_m": RADIUS,
            "height_m": HEIGHT,
            "cruise_speed_mps": V_CRUISE,
            "n_loops": N_LOOPS,
            "duration_s": round(total, 2),
            "dt_s": DT_WP,
            "trail_viz_align": "waypoints[].position 点列 = TrailViz.set_path(points)",
            "pos_cmd_fields": ["t", "position", "velocity", "yaw"],
        },
        "waypoints": wps,
    }
    os.makedirs(os.path.dirname(WP_PATH), exist_ok=True)
    with open(WP_PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print(f"[wp] 已生成 {WP_PATH}: {len(wps)} 点, 总时长 {total:.1f}s")


# ---------------------------------------------------------------- 发送联调
def _interp(wps, t):
    """相邻航点线性插值（位置/速度），yaw 取最近点。"""
    if t <= wps[0]["t"]:
        w = wps[0]
        return list(w["position"]), list(w["velocity"]), w["yaw"], w["phase"]
    if t >= wps[-1]["t"]:
        w = wps[-1]
        return list(w["position"]), list(w["velocity"]), w["yaw"], w["phase"]
    for i in range(len(wps) - 1):
        a, b = wps[i], wps[i + 1]
        if a["t"] <= t <= b["t"]:
            s = (t - a["t"]) / (b["t"] - a["t"])
            p = [a["position"][k] + s * (b["position"][k] - a["position"][k])
                 for k in range(3)]
            v = [a["velocity"][k] + s * (b["velocity"][k] - a["velocity"][k])
                 for k in range(3)]
            return p, v, (a["yaw"] if s < 0.5 else b["yaw"]), \
                (a["phase"] if s < 0.5 else b["phase"])
    w = wps[-1]
    return list(w["position"]), list(w["velocity"]), w["yaw"], w["phase"]


def _fetch_state():
    try:
        with _OPENER.open(STATE_URL, timeout=0.5) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def wait_ready(max_s=240.0):
    """等场景就绪：8090可答且物理时 t∈(0,60)。
    注意不能等"机体在原点"——215g 在环配置在无位置指令空窗期会自由飞
    失控漂移（联调第三跑实测：干净启动后机体数分钟漂出百米），监听出现
    后越早发送漂移越小；t<60 判据把空窗压到物理时 1 分钟内。"""
    t0 = time.time()
    while time.time() - t0 < max_s:
        st = _fetch_state()
        if st and st.get("pose") and 0 < st.get("t", 0) < 60:
            p = st["pose"][:3]
            print(f"[wp] 场景就绪: t={st['t']:.1f}s "
                  f"pose=({p[0]:+.2f},{p[1]:+.2f},{p[2]:+.2f})，立即发送",
                  flush=True)
            return True
        time.sleep(2.0)
    print("[wp] 等待超时：场景未就绪（若 t≥60 说明空窗过长机体已漂远，"
          "建议重启 main_sim 后立即重跑本脚本）", flush=True)
    return False


def send(host, port, rate, t0):
    with open(WP_PATH, encoding="utf-8") as f:
        wps = json.load(f)["waypoints"]
    total = wps[-1]["t"]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    n_sent = 0
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    log_f = open(LOG_PATH, "a", encoding="utf-8")

    def log(msg):
        line = f"[{time.strftime('%m%d_%H%M%S')}] {msg}"
        print(line, flush=True)
        log_f.write(line + "\n")
        log_f.flush()

    log(f"=== 桥接联调开始: {len(wps)}航点/{total:.1f}s, 发往 {host}:{port}, "
        f"rate={rate}Hz, t0={t0}s ===")
    log("口径: TRACKING_MODE=kinematic（运动学直写，绕过气动）——本结果只证"
        "桥接通畅，不构成飞行动力学验收")
    t_start = time.time()
    last_sample = -1.0
    last_pos = None
    _moved_samples = []
    while True:
        t_traj = (time.time() - t_start) + t0
        if t_traj > total + 2.0:
            break
        p, v, yaw, ph = _interp(wps, t_traj)
        msg = json.dumps({
            "type": "pos_cmd",
            "t": round(t_traj, 3),
            "position": [round(c, 4) for c in p],
            "velocity": [round(c, 4) for c in v],
            "yaw": yaw,
        }).encode()
        sock.sendto(msg, (host, port))
        n_sent += 1
        if time.time() - last_sample >= SAMPLE_EVERY:
            last_sample = time.time()
            st = _fetch_state()
            if st and st.get("pose"):
                last_pos = st["pose"][:3]
                _moved_samples.append(list(last_pos))
                log(f"t={t_traj:6.2f}s [{ph:15s}] wp=({p[0]:+.2f},{p[1]:+.2f},"
                    f"{p[2]:+.2f}) actual=({last_pos[0]:+.2f},{last_pos[1]:+.2f},"
                    f"{last_pos[2]:+.2f}) simT={st.get('t', -1):.1f}")
            else:
                log(f"t={t_traj:6.2f}s [{ph:15s}] wp=({p[0]:+.2f},{p[1]:+.2f},"
                    f"{p[2]:+.2f}) /state不可达")
        time.sleep(1.0 / rate)
    log(f"=== 发送完毕: 共{n_sent}条 pos_cmd, 末态actual={last_pos} ===")
    if last_pos is not None:
        p_end, _, _, _ = _interp(wps, total)
        drift = math.dist(last_pos, p_end)
        # kinematic 口径通畅判据：末段机体跟到终点邻域（P环kp=1.6/vmax=5，
        # 稳态滞后量级 ~v/kp≈1m，放宽到 2.5m）；"机体确实被驱动走完全程"用
        # 采样里的最大离原点距离（末态已返回原点，不能用末位置当位移——
        # 联调第四跑实测该误判把成功的跟踪判成 FAIL）
        moved = max((math.dist(q, [0, 0, 0]) for q in _moved_samples),
                    default=0.0)
        verdict = "BRIDGE OK" if (drift < 2.5 and moved > 2.0) else "BRIDGE FAIL"
        log(f"判据: 末段偏差{drift:.2f}m(<2.5) 且 最大离原点{moved:.2f}m(>2.0)"
            f" → {verdict}（人工复核 logs/track_error.csv 与本日志）")
    log_f.close()
    sock.close()


def main():
    ap = argparse.ArgumentParser(description="航点生成/桥接联调发送器")
    ap.add_argument("--generate", action="store_true", help="生成航点JSON")
    ap.add_argument("--send", action="store_true", help="发送pos_cmd联调")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9002)
    ap.add_argument("--rate", type=float, default=20.0, help="发送频率Hz")
    ap.add_argument("--t0", type=float, default=0.0, help="轨迹起始时刻偏移")
    ap.add_argument("--wait", action="store_true",
                    help="发送前等待场景就绪（8090+t>0+机体在原点邻域）")
    args = ap.parse_args()
    if args.generate:
        generate()
    elif args.send:
        if args.wait and not wait_ready():
            return 4
        send(args.host, args.port, args.rate, args.t0)
    else:
        ap.print_help()


if __name__ == "__main__":
    sys.exit(main())
