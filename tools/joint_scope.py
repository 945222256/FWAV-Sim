# -*- coding: utf-8 -*-
"""
joint_scope.py — 扑翼关节控制波形在线示波器
============================================
绑 UDP 9010（main_sim 以 30Hz 发 {t, names, pos, tgt}），pyqtgraph 实时画：
  上窗：翼关节 6 通道（flap/twist/fold × 左右）
  下窗：尾关节 3 通道（yaw + 左右舵面）
实线 = 实际关节角，虚线 = 目标角（同色配对）。滚动时间窗默认 8s。

用法（先跑 main_sim，再开本窗，随时开随时关）：
  python tools/joint_scope.py [端口=9010] [时间窗s=8]
"""
import collections
import json
import socket
import sys

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9010
WIN_S = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0

NAMES = ["flap_L", "flap_R", "twist_L", "twist_R", "fold_L", "fold_R",
         "tail_yaw", "tail_L", "tail_R"]
COLORS = {
    "flap_L": "#ff9f43", "flap_R": "#ff6b6b",
    "twist_L": "#54a0ff", "twist_R": "#48dbfb",
    "fold_L": "#1dd1a1", "fold_R": "#10ac84",
    "tail_yaw": "#f368e0", "tail_L": "#ff9ff3", "tail_R": "#feca57",
}

buf_t = collections.deque(maxlen=4000)
buf_pos = {n: collections.deque(maxlen=4000) for n in NAMES}
buf_tgt = {n: collections.deque(maxlen=4000) for n in NAMES}

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("0.0.0.0", PORT))
sock.settimeout(0.0)
print(f"[scope] 监听 udp:{PORT}，等 main_sim 数据…（本窗可随时关）")

app = pg.mkQApp("fwav-joint-scope")
win = pg.GraphicsLayoutWidget(title=f"fwav_15dof 关节波形（实线=实际 虚线=目标，窗{WIN_S:.0f}s）")
win.resize(1250, 760)
win.show()

pen_solid = lambda n: pg.mkPen(COLORS[n], width=2)
pen_dash = lambda n: pg.mkPen(COLORS[n], width=1, style=QtCore.Qt.DashLine)

plots = {}
curves = {}
WING_SET = ["flap_L", "flap_R", "twist_L", "twist_R", "fold_L", "fold_R"]
TAIL_SET = ["tail_yaw", "tail_L", "tail_R"]
for row, (title, keys) in enumerate(
        [("翼关节 (rad)", WING_SET), ("尾关节 (rad)", TAIL_SET)]):
    p = win.addPlot(row=row, col=0, title=title)
    p.addLegend(offset=(10, 4), labelTextSize="8pt")
    p.showGrid(x=True, y=True, alpha=0.25)
    p.setLabel("left", "角 (rad)")
    if row == 1:
        p.setLabel("bottom", "t (s)")
    plots[row] = p
    for n in keys:
        curves[(n, "pos")] = p.plot(pen=pen_solid(n), name=n)
        curves[(n, "tgt")] = p.plot(pen=pen_dash(n))

status = pg.LabelItem(justify="left")
win.addItem(status, row=2, col=0)


def drain():
    """非阻塞收尽 UDP 包，进环形缓冲。"""
    got = 0
    while True:
        try:
            data, _ = sock.recvfrom(8192)
        except (BlockingIOError, socket.timeout):
            break
        try:
            d = json.loads(data.decode())
        except Exception:
            continue
        t = float(d.get("t", 0.0))
        names = d.get("names") or []
        pos = d.get("pos") or []
        tgt = d.get("tgt") or []
        buf_t.append(t)
        for i, n in enumerate(names):
            if n in buf_pos:
                buf_pos[n].append(float(pos[i]) if i < len(pos) else 0.0)
                buf_tgt[n].append(float(tgt[i]) if i < len(tgt) else 0.0)
        got += 1
    return got


def refresh():
    drain()
    if len(buf_t) < 2:
        status.setText("等待 main_sim 数据 …（先启动 python.bat main_sim.py）")
        return
    ts = list(buf_t)
    t0 = ts[-1] - WIN_S
    lo = 0
    for i, t in enumerate(ts):
        if t >= t0:
            lo = i
            break
    ts_w = ts[lo:]
    for (n, kind), curve in curves.items():
        b = buf_tgt[n] if kind == "tgt" else buf_pos[n]
        vals = list(b)[lo:]
        if len(vals) == len(ts_w):
            curve.setData(ts_w, vals)
    for p in plots.values():
        p.setXRange(ts[-1] - WIN_S, ts[-1], padding=0)
    status.setText(
        f"t={ts[-1]:7.2f}s  通道={len(curves)}  "
        f"flap_L={list(buf_pos['flap_L'])[-1]:+.3f}rad  "
        f"twist_L={list(buf_pos['twist_L'])[-1]:+.3f}rad")


timer = QtCore.QTimer()
timer.timeout.connect(refresh)
timer.start(33)  # ~30fps 刷新

if __name__ == "__main__":
    pg.exec()
