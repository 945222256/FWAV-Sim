# -*- coding: utf-8 -*-
"""
ctrl_scope.py — 手动控制行程示波器
====================================
绑 UDP 9011（main_sim 30Hz 发 {t, ch, freq, tail_*}）：
  上窗：四通道杆量（throttle/elevator/aileron/rudder，[-1,1]）
  下窗：映射输出（拍频 Hz + 尾舵 rad）
用法：python tools/ctrl_scope.py [端口=9011]
"""
import collections
import json
import socket
import sys

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9011
WIN_S = 8.0

CH_KEYS = ["throttle", "elevator", "aileron", "rudder"]
CH_COLORS = {"throttle": "#ff9f43", "elevator": "#54a0ff",
             "aileron": "#1dd1a1", "rudder": "#f368e0"}
OUT_KEYS = ["freq", "tail_L", "tail_R", "tail_yaw"]
OUT_COLORS = {"freq": "#ee5253", "tail_L": "#48dbfb",
              "tail_R": "#0abde3", "tail_yaw": "#feca57"}

buf_t = collections.deque(maxlen=3000)
buf_ch = {k: collections.deque(maxlen=3000) for k in CH_KEYS}
buf_out = {k: collections.deque(maxlen=3000) for k in OUT_KEYS}

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("0.0.0.0", PORT))
sock.settimeout(0.0)
print(f"[ctrl-scope] 监听 udp:{PORT}，等 main_sim 数据…")

app = pg.mkQApp("fwav-ctrl-scope")
win = pg.GraphicsLayoutWidget(title="手动行程（上:杆量 下:映射输出）")
win.resize(1100, 700)
win.show()

p1 = win.addPlot(row=0, col=0, title="通道杆量 [-1,1]")
p2 = win.addPlot(row=1, col=0, title="映射输出（freq: Hz，尾舵: rad）")
for p in (p1, p2):
    p.addLegend(offset=(10, 4), labelTextSize="8pt")
    p.showGrid(x=True, y=True, alpha=0.25)
p2.setLabel("bottom", "t (s)")
curves = {}
for k in CH_KEYS:
    curves[k] = p1.plot(pen=pg.mkPen(CH_COLORS[k], width=2), name=k)
for k in OUT_KEYS:
    curves[k] = p2.plot(pen=pg.mkPen(OUT_COLORS[k], width=2), name=k)
p1.setYRange(-1.1, 1.1, padding=0)
status = pg.LabelItem(justify="left")
win.addItem(status, row=2, col=0)


def drain():
    while True:
        try:
            data, _ = sock.recvfrom(4096)
        except (BlockingIOError, socket.timeout):
            break
        try:
            d = json.loads(data.decode())
        except Exception:
            continue
        buf_t.append(float(d.get("t", 0.0)))
        for k in CH_KEYS:
            buf_ch[k].append(float((d.get("ch") or {}).get(k, 0.0)))
        for k in OUT_KEYS:
            buf_out[k].append(float(d.get(k, 0.0)))


def refresh():
    drain()
    if len(buf_t) < 2:
        status.setText("等待 main_sim …（先启动 python.bat main_sim.py）")
        return
    ts = list(buf_t)
    lo = next((i for i, t in enumerate(ts) if t >= ts[-1] - WIN_S), 0)
    ts_w = ts[lo:]
    for k in CH_KEYS:
        curves[k].setData(ts_w, list(buf_ch[k])[lo:])
    for k in OUT_KEYS:
        curves[k].setData(ts_w, list(buf_out[k])[lo:])
    for p in (p1, p2):
        p.setXRange(ts[-1] - WIN_S, ts[-1], padding=0)
    ch_now = {k: list(buf_ch[k])[-1] for k in CH_KEYS}
    status.setText(
        f"t={ts[-1]:7.2f}s  油={ch_now['throttle']:+.2f} 升={ch_now['elevator']:+.2f} "
        f"副={ch_now['aileron']:+.2f} 方={ch_now['rudder']:+.2f}  "
        f"freq={list(buf_out['freq'])[-1]:.2f}Hz")


timer = QtCore.QTimer()
timer.timeout.connect(refresh)
timer.start(33)

if __name__ == "__main__":
    pg.exec()
