# -*- coding: utf-8 -*-
"""
flight_replay_gui.py — 飞行记录回放台（全自由度可视化，2026-09-28 用户令）

用途：强制"每次验证假设的测试都看得见"。数据流（ident_stream_flight_*.csv）
双击即播：机体六自由度（位置+四元数全分量）、翅膀拍动/扭转/折叠关节
（实测列）、逐拍闸门与每翼频率（2026-09-28 起的新列，老记录取默认）、
全关节 JSON 快照（新记录）。侧栏自动挂载 logs 目录，测试后可给记录
加 tag 与 description（存同目录 <文件名>.meta.json），无 tag 的记录
红标——流程上强制补注。

运行：python tools/flight_replay_gui.py
自检：python tools/flight_replay_gui.py --selftest <csv> <out.png>
      （不开窗口渲染三帧出图，供无显示环境验证）
写权限：主会话亲写；代理与自动流程禁改本文件。
"""
import os, sys, json, glob, math, time, traceback
import numpy as np

import matplotlib
matplotlib.use("Agg") if "--selftest" in sys.argv else matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection, Line3D

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")

# 升力比归一用的体重（runB 契约 215g×9.81=2.109N；升力列取 Frz——
# K=8 周期平均总气动力 z 分量的真值口径，Fz 列是÷2 簿记口径禁用）
W_REF = 0.215 * 9.81

# 翼面简化几何（URDF 口径：半展 0.32=内板 0.20+外板 0.12，平均弦 0.17）
SPAN_IN, SPAN_OUT, CHORD = 0.20, 0.12, 0.17
TAIL_ARM, TAIL_SPAN, TAIL_CHORD = 0.30, 0.09, 0.06


def quat_to_R(q):
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def euler_zyx(q):
    w, x, y, z = q
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - x * z))))
    return roll, pitch


class FlightRecord:
    """一条飞行记录：CSV 数据 + 元数据（tag/description）。"""

    def __init__(self, csv_path):
        self.path = csv_path
        self.name = os.path.basename(csv_path)
        self.meta_path = csv_path + ".meta.json"
        self.meta = {"tags": "", "description": ""}
        self._load_meta()
        self._load_csv()

    def _load_meta(self):
        try:
            with open(self.meta_path, encoding="utf-8") as fp:
                m = json.load(fp)
            self.meta["tags"] = str(m.get("tags", ""))
            self.meta["description"] = str(m.get("description", ""))
        except (OSError, ValueError):
            pass

    def save_meta(self):
        self.meta["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(self.meta_path, "w", encoding="utf-8") as fp:
            json.dump(self.meta, fp, ensure_ascii=False, indent=1)

    def _load_csv(self):
        rows = []
        with open(self.path, encoding="utf-8") as fp:
            import csv as _csv
            rd = _csv.DictReader(fp)
            for r in rd:
                rows.append(r)
        self.cols = set(rd.fieldnames or [])
        self.n = len(rows)
        f = lambda c, d=0.0: np.array(
            [float(r.get(c, d)) if r.get(c, d) not in (None, "") else d
             for r in rows], dtype=float) if rows else np.zeros(0)
        self.t = f("t")
        self.pos = np.stack([f("x"), f("y"), f("z")], axis=1) if rows else np.zeros((0, 3))
        self.quat = np.stack([f("qw"), f("qx"), f("qy"), f("qz")], axis=1) \
            if rows else np.zeros((0, 4))
        # 四元数双覆盖符号连续化（插值/显示稳定）
        for i in range(1, len(self.quat)):
            if float(np.dot(self.quat[i - 1], self.quat[i])) < 0:
                self.quat[i] = -self.quat[i]
        self.flapL = f("flapLa"); self.flapR = f("flapRa")
        self.twL = f("twistLa"); self.twR = f("twistRa")
        self.foldL = f("foldLa"); self.foldR = f("foldRa")
        self.elev = f("elev"); self.freq = f("freq"); self.dA = f("dA")
        self.ail = f("ail"); self.rud = f("rud")
        self.Frz = f("Frz")
        self.roll_des = f("roll_des")
        self.pitch_des = f("pitch_des")
        self.vy = f("vy"); self.vx = f("vx"); self.wz = f("wz")
        self.gateL = f("gateL", 1.0); self.gateR = f("gateR", 1.0)
        self.freqL = f("freqL"); self.freqR = f("freqR")
        rp = np.array([euler_zyx(q) for q in self.quat]) if rows else np.zeros((0, 2))
        self.roll, self.pitch = (rp[:, 0], rp[:, 1]) if rows else (np.zeros(0), np.zeros(0))
        # 偏航角（ZYX yaw，2026-09-29 用户令补）：四元数直接算，列不在
        # 档也能画实际偏航；期望偏航列数据流不存在（现行控制律偏航通道
        # 为纯阻尼无指令列，缺口登记），在档即画
        if rows:
            self.yaw = np.array([
                math.atan2(2 * (q[0] * q[3] + q[1] * q[2]),
                           1 - 2 * (q[2] * q[2] + q[3] * q[3]))
                for q in self.quat])
        else:
            self.yaw = np.zeros(0)
        # 期望偏航（2026-09-29 用户令"从指令反推"）：数据流无偏航指令列，
        # 但坡度指令经协调盘旋律 ψ̇=g·tan(roll_des)/V 隐含期望偏航率——
        # 正常模式 roll_des 本身由航向差反推（flight_controller 392行），
        # 科目一模式为钉死坡度指令（399行），两口径统一用记录的 roll_des
        # 与水平速度积分反推期望航向，锚定记录起点实际偏航；速度下限一
        # 米每秒防启动段除零放大。真列在档则优先真列（弧度同口径）。
        if "yaw_des" in self.cols:
            self.yaw_des = f("yaw_des")
        elif rows and "roll_des" in self.cols and len(self.t) > 2:
            _V = np.maximum(np.hypot(self.vx, self.vy), 1.0)
            _tan = np.tan(np.clip(self.roll_des, -1.2, 1.2))
            # 符号自校准（2026-09-29 用105343实测：本项目姿态约定下坡度
            # 到偏航率系数为负）：协调盘旋 ψ̇=k·tan(roll_des)/V，k 取本记录
            # 实测偏航率对 tan/V 比值的中位数（物理上应约等于负的重力加
            # 速度）；无显著坡度段（巡航直飞记录）回退理论符号
            _dyaw = np.gradient(np.unwrap(self.yaw)) / np.gradient(self.t)
            _m = np.abs(_tan) > 0.05
            if int(_m.sum()) > 20:
                _k = float(np.median(_dyaw[_m] / (_tan[_m] / _V[_m])))
            else:
                _k = -9.81
            self.yaw_des = self.yaw[0] + np.cumsum(
                _k * _tan / _V * np.gradient(self.t))
        else:
            self.yaw_des = np.zeros(0)
        # 每翼实际拍动周期（2026-09-28 用户问"实际拍打周期用时看不见"）：
        # 从关节实测拍角上升过零点直接测周期（任何有 flapLa/Ra 列的记录
        # 都可得，不依赖每翼频率指令列；停拍时关节照拍故此量恒可测）
        def stroke_period(sig):
            s = np.asarray(sig, dtype=float)
            if len(s) < 10:
                return np.zeros(0), np.zeros(0)
            cross = np.where((s[:-1] < 0.0) & (s[1:] >= 0.0))[0] + 1
            if len(cross) < 2:
                return np.zeros(0), np.zeros(0)
            tc = self.t[cross]
            per = np.diff(tc)
            return tc[1:], per
        self.perL_t, self.perL_v = stroke_period(self.flapL)
        self.perR_t, self.perR_v = stroke_period(-self.flapR)

    def wing_quads_body(self, i):
        """第 i 帧的翼面四边形（体坐标系）。拍角绕 x 轴（z 腹向下，
        抬翼=尖向 −z）；右翼关节角镜像取负（对称拍动时目标 flap_R=
        −flap_L 的既有约定）。折叠（肘）在外板再折一角。扭转画在弦向。
        """
        out = []
        for sgn, flap, tw, fold in ((+1, self.flapL[i], self.twL[i], self.foldL[i]),
                                    (-1, self.flapR[i], self.twR[i], self.foldR[i])):
            phys = flap if sgn > 0 else -flap        # 右翼关节角镜像
            fold_p = fold if sgn > 0 else -fold
            def dir_vec(a):
                return np.array([0.0, sgn * math.cos(a), -math.sin(a)])
            d_in = dir_vec(phys)
            d_out = dir_vec(phys + fold_p)
            root = np.array([0.0, 0.03 * sgn, 0.0])
            hinge = root + d_in * SPAN_IN
            tip = hinge + d_out * SPAN_OUT
            span_dir = d_in
            chord_dir = np.array([1.0, 0.0, 0.0])
            # 扭转：弦向绕展向轴转 tw（左翼正向；右翼镜像视觉对称即可）
            k = np.cross(span_dir, chord_dir)
            c_t = chord_dir * math.cos(tw * sgn) + k * math.sin(tw * sgn)
            c_t = c_t / max(np.linalg.norm(c_t), 1e-9) * CHORD * 0.5
            out.append([root - c_t, root + c_t, hinge + c_t, hinge - c_t])
            c_t2 = c_t  # 外板弦向沿用
            out.append([hinge - c_t2, hinge + c_t2, tip + c_t2, tip - c_t2])
        return out

    def tail_quads_body(self, i):
        """尾面绑定补全（2026-09-28 用户问"尾舵到底动没动"）：左右面
        各自按 升降舵±差动 偏转（与主仿真尾目标同式），整组再绕机体竖
        轴偏方向舵角——尾的控制活动从此在动画可见。"""
        eL = self.elev[i] + self.ail[i]
        eR = self.elev[i] - self.ail[i]
        r = self.rud[i]
        rz = np.array([[math.cos(r), math.sin(r), 0.0],
                       [-math.sin(r), math.cos(r), 0.0],
                       [0.0, 0.0, 1.0]])
        quads = []
        for sgn, e in ((+1, eL), (-1, eR)):
            root = np.array([-TAIL_ARM, 0.03 * sgn, 0.0])
            tip = root + np.array([0.0, sgn * TAIL_SPAN * math.cos(e),
                                   -TAIL_SPAN * math.sin(e)])
            c = np.array([TAIL_CHORD * 0.5, 0.0, 0.0])
            quads.append([rz @ (root - c), rz @ (root + c),
                          rz @ (tip + c), rz @ (tip - c)])
        return quads


class ReplayView:
    """一图三窗：左 3D 场景（跟随机体）+ 右三条时间条带 + 光标。"""

    def __init__(self, fig):
        self.fig = fig
        gs = fig.add_gridspec(7, 2, width_ratios=[2.1, 1.0], hspace=0.8, wspace=0.25)
        self.ax3 = fig.add_subplot(gs[:, 0], projection="3d")
        self.ax_z = fig.add_subplot(gs[0, 1])
        self.ax_rp = fig.add_subplot(gs[1, 1], sharex=self.ax_z)
        self.ax_pt = fig.add_subplot(gs[2, 1], sharex=self.ax_z)
        # 偏航独立带（2026-09-29 用户令"偏航和期望偏航的波形图还没看见"）：
        # 偏航量程（持续盘旋可扫百度级）与滚转（正负一二十度）差一个量级，
        # 同带必被压扁或挤出视野，独立成栏
        self.ax_yaw = fig.add_subplot(gs[3, 1], sharex=self.ax_z)
        self.ax_f = fig.add_subplot(gs[4, 1], sharex=self.ax_z)
        self.ax_w = fig.add_subplot(gs[5, 1], sharex=self.ax_z)
        # 左右翼通道对比带：左轴=每翼实际拍动周期（过零实测），副轴=
        # 两翼闸门停拍带（列在档且有停拍才画）
        self.ax_ch = fig.add_subplot(gs[6, 1], sharex=self.ax_z)
        self._static_done = False

    def setup_static(self, rec):
        a = self.ax3
        a.clear()
        a.set_box_aspect((1, 1, 0.6))
        # 地面网格
        cx, cy = float(rec.pos[0, 0]), float(rec.pos[0, 1])
        for xx in np.arange(cx - 2, cx + 2.01, 1.0):
            a.plot([xx, xx], [cy - 2, cy + 2], zs=0, color="0.88", lw=0.5)
        for yy in np.arange(cy - 2, cy + 2.01, 1.0):
            a.plot([cx - 2, cx + 2], [yy, yy], zs=0, color="0.88", lw=0.5)
        # 期望参考转弯圆（2026-09-28 用户令"拿期望和红色真实尾迹对比"）：
        # 数据流未记录控制器期望路径（登记缺口），此处由期望坡度指令
        # 几何重构——r=V²/(g·tanφ_des)，圆心取指令起始位置沿初始转弯
        # 方向法向偏移 r；恒指令画圆，无指令不画。红色尾迹=实际，黑色
        # 全程历史线撤除（红尾迹既在，全程线赘余）
        i0 = None
        for i in range(rec.n):
            if abs(rec.roll_des[i]) > 0.02:
                i0 = i; break
        if i0 is not None and i0 < rec.n - 10:
            seg = slice(i0, min(i0 + int(5.0 / max(np.median(np.diff(rec.t)), 1e-3)),
                                rec.n))
            V = float(np.median(np.hypot(rec.vx[seg], rec.vy[seg])))
            phi = float(np.median(rec.roll_des[seg]))
            wz = float(np.median(rec.wz[seg]))
            if abs(phi) > 0.02 and V > 0.5 and abs(wz) > 1e-3:
                r_des = V * V / (9.81 * abs(math.tan(phi)))
                turn = math.copysign(1.0, wz)
                p0 = rec.pos[i0]
                tangent = np.array([rec.vx[i0], rec.vy[i0]])
                t_hat = tangent / max(np.linalg.norm(tangent), 1e-6)
                n_hat = turn * np.array([-t_hat[1], t_hat[0]])
                c = p0[:2] + n_hat * r_des
                th = np.linspace(0, 2 * math.pi, 96)
                ang0 = math.atan2(p0[1] - c[1], p0[0] - c[0])
                dth = -turn
                xs = c[0] + r_des * np.cos(ang0 + dth * th)
                ys = c[1] + r_des * np.sin(ang0 + dth * th)
                zs = np.full_like(xs, p0[2])
                a.plot(xs, ys, zs, color="k", lw=1.0, ls="--", alpha=0.55)
                a.text(c[0], c[1], p0[2] + 0.1,
                       "期望参考圆 r≈%.1fm（坡度指令几何重构）" % r_des,
                       fontsize=6, color="0.25")
        self.trail = a.plot([], [], [], color="crimson", lw=1.8)[0]
        self.body = Line3D([], [], [], color="k", lw=2.0)
        a.add_line(self.body)
        self.triad = Line3D([], [], [], color="0.2", lw=1.2)
        a.add_line(self.triad)
        self.wings = Poly3DCollection([], facecolors=("dodgerblue", 0.55),
                                      edgecolors=("navy", 0.6), lw=0.5)
        a.add_collection3d(self.wings)
        self.tailc = Poly3DCollection([], facecolors=("salmon", 0.55),
                                      edgecolors=("darkred", 0.6), lw=0.5)
        a.add_collection3d(self.tailc)
        for ax, y, lab in ((self.ax_z, None, "高度 z [m]"),
                           (self.ax_rp, None, "滚转/期望滚转 [°]"),
                           (self.ax_pt, None, "俯仰/期望俯仰 [°]"),
                           (self.ax_yaw, None, "偏航/期望偏航 [°]"),
                           (self.ax_f, None, "拍频/差动"),
                           (self.ax_w, None, "左右翼拍角 [°]"),
                           (self.ax_ch, None, "每翼频率 | 闸门")):
            ax.clear()
            ax.set_ylabel(lab, fontsize=8)
            ax.grid(alpha=0.3)
            ax.tick_params(labelsize=7)
        self.ax_z.plot(rec.t, rec.pos[:, 2], color="tab:blue", lw=1)
        # 高度+升力同带双轴（2026-09-28 用户令）：左轴高度，右轴升力比
        # Frz/W（悬线=1.0 体重线）；twin 惰性建一次、重载只清不叠
        if getattr(self, "ax_lift", None) is None:
            self.ax_lift = self.ax_z.twinx()
        self.ax_lift.clear()
        self.ax_lift.plot(rec.t, rec.Frz / W_REF, color="tab:green",
                          lw=0.9, alpha=0.85)
        self.ax_lift.axhline(1.0, color="tab:green", ls=":", lw=0.7)
        self.ax_lift.set_ylabel("升力比 Frz/W", fontsize=8, color="tab:green")
        self.ax_lift.tick_params(labelsize=7, colors="tab:green")
        self.ax_z.set_ylabel("高度 z | 升力比", fontsize=8)
        self.ax_rp.plot(rec.t, np.degrees(rec.roll), color="tab:green", lw=1, label="滚转")
        # 期望滚转虚线：区分指令与实际；加粗防大摆幅段被压扁不可见
        self.ax_rp.plot(rec.t, np.degrees(rec.roll_des), color="k", lw=1.6,
                        ls="--", alpha=0.8, label="期望滚转")
        # 偏航独立带（2026-09-29 用户令）：实际偏航解卷平滑（持续盘旋不
        # 再正负百度跳变）；期望偏航为坡度指令反推（真列在档优先），无坡
        # 度指令列的旧记录以右角注标明缺席
        self.ax_yaw.plot(rec.t, np.degrees(np.unwrap(rec.yaw)), color="0.45",
                         lw=0.9, alpha=0.9, label="偏航(实际)")
        if len(rec.yaw_des):
            self.ax_yaw.plot(rec.t, np.degrees(np.unwrap(rec.yaw_des)),
                             color="0.15", lw=1.4, ls="--", alpha=0.85,
                             label="期望偏航(指令反推)")
        else:
            self.ax_yaw.set_title("期望偏航不可反推（无坡度指令列）",
                                  fontsize=6, color="0.4", loc="right")
        self.ax_yaw.legend(fontsize=6, loc="best")
        self.ax_rp.legend(fontsize=6, loc="best")
        # 俯仰独立带（2026-09-28 用户"橙色虚线一直不可见"）：俯仰指令
        # 虚线（列在档即画）与实际俯仰实线分带对比，不再与滚转叠画
        self.ax_pt.plot(rec.t, np.degrees(rec.pitch), color="tab:orange",
                        lw=1, label="俯仰")
        if "pitch_des" in rec.cols:
            self.ax_pt.plot(rec.t, np.degrees(rec.pitch_des),
                            color="tab:orange", lw=1.6, ls="--",
                            alpha=0.85, label="期望俯仰")
        self.ax_pt.legend(fontsize=6, loc="best")
        self.ax_f.plot(rec.t, rec.freq, color="tab:purple", lw=1, label="拍频")
        self.ax_f.plot(rec.t, np.abs(rec.dA), color="tab:red", lw=1, label="|差动|")
        self.ax_f.legend(fontsize=6, loc="best")
        # 左右翼波形：关节实测拍角，右翼镜像取负后对称拍动两线重合
        self.ax_w.plot(rec.t, np.degrees(rec.flapL), color="tab:blue", lw=0.8, label="左翼")
        self.ax_w.plot(rec.t, -np.degrees(rec.flapR), color="tab:red", lw=0.8,
                       alpha=0.7, label="右翼(镜像)")
        self.ax_w.legend(fontsize=6, loc="upper left")
        # 每翼实际拍动周期（过零实测，任何记录可得）：阶梯线两翼对比；
        # 副轴停拍带（占空的诚实口径=闸门列；无列/无停拍时标注缺席）
        if len(rec.perL_t):
            self.ax_ch.step(rec.perL_t, rec.perL_v, where="post",
                            color="tab:blue", lw=1.2, label="左翼周期")
        if len(rec.perR_t):
            self.ax_ch.step(rec.perR_t, rec.perR_v, where="post",
                            color="tab:red", lw=1.2, ls="--",
                            alpha=0.85, label="右翼周期")
        if len(rec.perL_t) or len(rec.perR_t):
            self.ax_ch.legend(fontsize=6, loc="upper left")
            # 周期带纵轴限幅（2026-09-29 用户令"L型后看不见波形"）：启动
            # 斜坡段首个过零间隔可达秒级巨值，把纵轴撑爆后健康段（亚秒）
            # 被压成直线；限幅取两侧周期合并中位数的二点五倍，越幅尖峰
            # 画出窗外（斜坡段异常本就不属巡航口径），健康波形恢复可见
            _per_all = np.concatenate(
                [v for v in (rec.perL_v, rec.perR_v) if len(v)] or
                (np.zeros(0),))
            _med = float(np.median(_per_all)) if len(_per_all) else 0.0
            if _med > 0:
                self.ax_ch.set_ylim(0.0, max(2.5 * _med, 1e-3))
        if "gateL" in rec.cols and bool(np.any(rec.gateL < 0.5)
                                       or np.any(rec.gateR < 0.5)):
            if getattr(self, "ax_gate", None) is None:
                self.ax_gate = self.ax_ch.twinx()
            self.ax_gate.clear()
            self.ax_gate.fill_between(rec.t, 0, 1 - rec.gateL,
                                      step="post", color="tab:blue", alpha=0.25)
            self.ax_gate.fill_between(rec.t, 0, -(1 - rec.gateR),
                                      step="post", color="tab:red", alpha=0.25)
            self.ax_gate.set_ylim(-1.2, 1.2)
            self.ax_gate.set_yticks([-1, 0, 1])
            self.ax_gate.set_yticklabels(["右停", "", "左停"], fontsize=6)
            self.ax_gate.set_ylabel("停拍带", fontsize=7)
        elif "gateL" not in rec.cols:
            self.ax_ch.set_title("此记录无每翼闸门列（旧记录，占空不可回放）",
                                 fontsize=6, color="0.4", loc="right")
        self.cur = [ax.axvline(0, color="k", lw=0.8, alpha=0.7)
                    for ax in (self.ax_z, self.ax_rp, self.ax_pt, self.ax_yaw,
                               self.ax_f, self.ax_w, self.ax_ch)]
        self.ax_z.set_xlim(float(rec.t[0]), float(rec.t[-1]))
        self._static_done = True

    def apply_window(self, rec, i):
        """波形带视窗：win=None 全程；win=秒数滑动局部窗。窗口边缘取整
        到固定刻度步（2026-09-28 用户"时间戳老是抖"：不取整时刻度每帧
        重排、标签跳动）。sharex 联动全部带。"""
        from matplotlib.ticker import MultipleLocator
        if getattr(self, "win", None):
            t0 = float(rec.t[i]); T = float(rec.t[-1])
            # 刻度步取窗口长度的约五分之一，归到 1/2/5 良值
            raw = self.win / 5.0
            mag = 10.0 ** math.floor(math.log10(raw))
            step = mag * min((1, 2, 5, 10), key=lambda k: abs(k * mag - raw))
            # 平滑渐进滑窗（2026-09-29 用户令"窗口末尾不要瞬移"）：光标
            # 恒处窗口六成位，边缘随帧连续推进；刻度钉在绝对整步上，标签
            # 为绝对时刻不逐帧抖（此前边缘取整导致窗口按步瞬移，撤除）
            lo = max(0.0, t0 - self.win * 0.6)
            hi = min(T, lo + self.win)
            if hi - lo < self.win:
                lo = hi - self.win
            self.ax_z.set_xlim(lo, hi)
            self.ax_z.xaxis.set_major_locator(MultipleLocator(step))
        else:
            self.ax_z.set_xlim(float(rec.t[0]), float(rec.t[-1]))
            self.ax_z.xaxis.set_major_locator(
                MultipleLocator(max(1.0, (rec.t[-1] - rec.t[0]) / 8.0)))

    def render(self, rec, i):
        if not self._static_done:
            self.setup_static(rec)
        self.apply_window(rec, i)
        q = rec.quat[i]; p = rec.pos[i]
        R = quat_to_R(q)
        # 机体线框（机身盒）
        box = np.array([[x, y, z] for x in (0.16, -0.20) for y in (0.035, -0.035)
                        for z in (0.03, -0.03)])
        edges = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7),
                 (0, 4), (1, 5), (2, 6), (3, 7)]
        seg = []
        for a_, b_ in edges:
            seg += [p + R @ box[a_], p + R @ box[b_], [np.nan] * 3]
        self.body.set_data_3d([s[0] for s in seg], [s[1] for s in seg], [s[2] for s in seg])
        tri = []
        for vec, col in ((np.array([0.3, 0, 0]), 0), (np.array([0, 0.3, 0]), 1),
                         (np.array([0, 0, 0.3]), 2)):
            tp = p + R @ vec
            tri += [p, tp, [np.nan] * 3]
        # 机背姿态竖片（2026-09-28 用户"看不出滚转"）：机体长轴近视线
        # 时绕轴滚转屏幕上难辨，背鳍小竖片让滚转变成明显的左右摆
        tri += [p + R @ np.array([0.02, 0.0, -0.03]),
                p + R @ np.array([0.02, 0.0, -0.14]), [np.nan] * 3]
        self.triad.set_data_3d([s[0] for s in tri], [s[1] for s in tri], [s[2] for s in tri])
        wq_quads = rec.wing_quads_body(i)
        self.wings.set_verts([[p + R @ c for c in quad] for quad in wq_quads])
        self.tailc.set_verts([[p + R @ c for c in quad] for quad in rec.tail_quads_body(i)])
        _k = max(1, (i + 1) // 600)          # 显示端抽稀，防长记录拖慢
        _sl = slice(0, i + 1, _k)
        self.trail.set_data_3d(rec.pos[_sl, 0], rec.pos[_sl, 1], rec.pos[_sl, 2])
        for c in self.cur:
            c.set_xdata([rec.t[i]])
        roll, pitch = euler_zyx(q)
        beta = math.degrees(math.atan2(rec.vy[i], max(abs(rec.vx[i]), 0.1)))
        self.ax3.set_title(
            "t=%.2fs  z=%.2fm  滚转%+.0f° 俯仰%+.0f° 侧滑%+.0f°  f=%.2fHz  "
            "闸门L/R=%.0f/%.0f" % (rec.t[i], p[2], math.degrees(roll),
                                   math.degrees(pitch), beta, rec.freq[i],
                                   rec.gateL[i], rec.gateR[i]), fontsize=8)
        m = 1.0
        self.ax3.set_xlim(p[0] - m, p[0] + m); self.ax3.set_ylim(p[1] - m, p[1] + m)
        self.ax3.set_zlim(0, max(2.0, p[2] + 0.8))
        self.ax3.set_box_aspect((1, 1, 0.6))
        # 侧视视角（2026-09-28）：原斜前视角下滚转轴近似顺着视线，滚转
        # 在屏幕上几乎不可见（用户"全程没看出滚转"实录）——改正侧视
        # 使滚转成为屏幕内摆动
        self.ax3.elev, self.ax3.azim = 15, -90


def selftest(csv_path, out_png):
    rec = FlightRecord(csv_path)
    fig = plt.figure(figsize=(15, 6), dpi=110)
    fig.suptitle("回放自检：%s（首/中/末三帧）" % rec.name, fontsize=10)
    idxs = [0, rec.n // 2, rec.n - 1]
    view = ReplayView(fig)
    # 三帧并排：复用同一 view 连续渲染三帧（验证动态更新路径）
    for k, i in enumerate(idxs):
        view.render(rec, i)
        fig.canvas.draw()
    # 单独再画首末两帧小图核对翼形对称性
    fig2, axs = plt.subplots(1, 3, figsize=(15, 4.5), dpi=110,
                             subplot_kw={"projection": "3d"})
    for ax, i, tag in zip(axs, idxs, ("首帧", "中帧", "末帧")):
        q = rec.quat[i]; p = rec.pos[i]; R = quat_to_R(q)
        ax.add_collection3d(Poly3DCollection(
            [[p + R @ c for c in quad] for quad in rec.wing_quads_body(i)],
            facecolors=("dodgerblue", 0.55), edgecolors=("navy", 0.7)))
        ax.add_collection3d(Poly3DCollection(
            [[p + R @ c for c in quad] for quad in rec.tail_quads_body(i)],
            facecolors=("salmon", 0.55)))
        ax.scatter(*p, color="k", s=10)
        ax.set_title("%s t=%.1fs" % (tag, rec.t[i]), fontsize=9)
        ax.set_xlim(p[0] - 0.5, p[0] + 0.5); ax.set_ylim(p[1] - 0.5, p[1] + 0.5)
        ax.set_zlim(p[2] - 0.5, p[2] + 0.5)
    fig2.tight_layout()
    fig2.savefig(out_png, bbox_inches="tight")
    print("[自检] 帧数=%d 末帧 t=%.2f z=%.2f → %s" % (rec.n, rec.t[-1], rec.pos[-1, 2], out_png))


class ReplayGUI:
    """侧栏自动挂载 + 双击播放 + tag/description 编辑。"""

    def __init__(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        self.root = tk.Tk()
        self.root.title("飞行记录回放台 — 每次假设验证都要看得见")
        self.root.geometry("1500x860")
        self.rec = None
        self.idx = 0.0
        self.playing = False
        self.speed = 0.5
        self.filter_text = ""
        # ── 左：侧栏 ──
        side = tk.Frame(self.root, width=360)
        side.pack(side="left", fill="y", padx=4, pady=4)
        tk.Label(side, text="记录（双击播放；红=未加tag）",
                 font=("Microsoft YaHei", 10, "bold")).pack(anchor="w")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self.refresh_list())
        tk.Entry(side, textvariable=self.filter_var).pack(fill="x", pady=2)
        self.listbox = tk.Listbox(side, width=52, font=("Consolas", 9))
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<Double-Button-1>", self.on_play)
        tk.Button(side, text="刷新列表", command=self.refresh_list).pack(fill="x", pady=2)
        # ── 左下：tag / description ──
        tk.Label(side, text="tag（空格分隔）", font=("Microsoft YaHei", 9)).pack(anchor="w")
        self.tags_var = tk.StringVar()
        tk.Entry(side, textvariable=self.tags_var).pack(fill="x")
        tk.Label(side, text="description（本次测试验证了什么假设、结论）",
                 font=("Microsoft YaHei", 9)).pack(anchor="w")
        self.desc_text = tk.Text(side, height=6, font=("Microsoft YaHei", 9))
        self.desc_text.pack(fill="x")
        tk.Button(side, text="保存 tag/description", fg="white", bg="#b33",
                  command=self.save_meta).pack(fill="x", pady=3)
        # ── 右：画布 + 控制条 ──
        right = tk.Frame(self.root)
        right.pack(side="left", fill="both", expand=True)
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        self.fig = plt.figure(figsize=(10.5, 7.9), dpi=92)
        self.view = ReplayView(self.fig)
        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        bar = tk.Frame(right)
        bar.pack(fill="x", pady=3)
        self.btn_play = tk.Button(bar, text="播放", width=8, command=self.toggle_play)
        self.btn_play.pack(side="left", padx=2)
        tk.Button(bar, text="⏮", width=3, command=lambda: self.seek(0)).pack(side="left")
        self.slider = tk.Scale(bar, from_=0, to=100, orient="horizontal", length=520,
                               command=self.on_slider)
        self.slider.pack(side="left", fill="x", expand=True, padx=4)
        ttk_obj = ttk.Combobox(bar, values=["0.1×", "0.25×", "0.5×", "1×", "2×"],
                               width=5, state="readonly")
        ttk_obj.set("0.5×")
        ttk_obj.bind("<<ComboboxSelected>>",
                     lambda e: self.set_speed(ttk_obj.get()))
        ttk_obj.pack(side="left", padx=2)
        # 波形视窗选择（2026-09-28 用户令）：播放时自动切局部滑动窗，
        # 暂停回全程；也可手动强制任一档
        tk.Label(bar, text="波形窗", font=("Microsoft YaHei", 9)).pack(side="left")
        self.win_var = tk.StringVar(value="全程")
        win_box = ttk.Combobox(bar, values=["全程", "8s", "15s", "30s"],
                               width=5, state="readonly", textvariable=self.win_var)
        win_box.bind("<<ComboboxSelected>>",
                     lambda e: self.set_window(self.win_var.get()))
        win_box.pack(side="left", padx=2)
        self.status_var = tk.StringVar(value="就绪——双击左侧记录开始回放")
        tk.Label(right, textvariable=self.status_var, anchor="w",
                 font=("Microsoft YaHei", 9), fg="#357").pack(fill="x")
        self.refresh_list()
        self.status("回放台已启动（记录目录 %s）" % LOG_DIR)
        self._tick_id = None
        self._refr_id = None
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._refr_id = self.root.after(10000, self.auto_refresh)

    def on_close(self):
        """关窗先撤销定时回调再销毁：否则排队中的 after 触发对已销毁
        控件报 invalid command name 刷屏控制台（用户盯控制台，噪音即误报）"""
        for attr in ("_tick_id", "_refr_id"):
            aid = getattr(self, attr, None)
            if aid is not None:
                try:
                    self.root.after_cancel(aid)
                except Exception:
                    pass
        self.root.destroy()

    # ---- 列表（轻量：只读文件名与 sidecar 元数据，不解析 CSV——
    # 全量解析会让每次刷新阻塞主循环数秒，界面假死） ----
    def refresh_list(self):
        self.items = []
        for p in sorted(glob.glob(os.path.join(LOG_DIR, "ident_stream_flight_*.csv")),
                        key=os.path.getmtime, reverse=True):
            name = os.path.basename(p)
            if self.filter_text and self.filter_text.lower() not in name.lower():
                continue
            meta = {"tags": "", "description": ""}
            try:
                with open(p + ".meta.json", encoding="utf-8") as fp:
                    m = json.load(fp)
                meta["tags"] = str(m.get("tags", ""))
                meta["description"] = str(m.get("description", ""))
            except (OSError, ValueError):
                pass
            self.items.append({"path": p, "name": name, "meta": meta})
        self.listbox.delete(0, "end")
        for it in self.items:
            tag = it["meta"]["tags"].strip()
            mark = "●" if tag else "○"
            line = "%s %s  %s" % (mark, it["name"][:34], (tag[:22] or "无tag"))
            self.listbox.insert("end", line)
            if not tag:
                self.listbox.itemconfig("end", fg="#c22")
        self.status("列表刷新：%d 条记录（目录 %s）" % (len(self.items), LOG_DIR))

    def status(self, msg):
        """状态同时上 GUI 与控制台（用户令：后台要看得见）。"""
        print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)
        try:
            self.status_var.set(msg)
        except Exception:
            pass

    def auto_refresh(self):
        try:
            if not self.playing and self.rec is None:
                self.refresh_list()
        finally:
            self._refr_id = self.root.after(10000, self.auto_refresh)

    # ---- 播放 ----
    def on_play(self, _ev=None):
        sel = self.listbox.curselection()
        if not sel:
            self.status("未选中记录：请先在左侧列表双击一条记录")
            return
        entry = self.items[sel[0]]
        self.status("加载中：%s（%s）" % (entry["name"],
                                        "%.1f MB" % (os.path.getsize(entry["path"]) / 1e6)))
        t0 = time.time()
        try:
            rec = FlightRecord(entry["path"])
        except Exception:
            self.status("加载失败：%s（回溯见控制台）" % entry["name"])
            traceback.print_exc()
            return
        self.status("加载完成：%d 帧 / %.1f 秒，耗时 %.2f s → 播放"
                    % (rec.n, rec.t[-1] if rec.n else 0.0, time.time() - t0))
        self.load(rec)

    def load(self, rec):
        self.rec = rec
        self.idx = 0.0
        self.view.setup_static(rec)
        self.view.render(rec, 0)
        self.canvas.draw_idle()
        self.tags_var.set(rec.meta["tags"])
        self.desc_text.delete("1.0", "end")
        self.desc_text.insert("1.0", rec.meta["description"])
        self.set_playing(True)

    def set_playing(self, on):
        self.playing = on
        self.btn_play.config(text="暂停" if on else "播放")
        if on:
            # 播到末尾后按播放=回卷重播（2026-09-28 用户"播放键形同虚设"
            # 根因：末尾处 idx 已在终点，tick 立即再触发播放结束）
            if self.rec is not None and self.idx >= self.rec.n - 1.5:
                self.idx = 0.0
            # 播放自动切局部滑动窗（仅当前处于"全程"档时；手动选定的
            # 窗口档永远粘住，暂停不再自动回全程）
            if self.win_var.get() == "全程":
                self.win_var.set("15s")
                self.set_window("15s")
            self._tick_id = self.root.after(85, self.tick)
        else:
            self._tick_id = None

    def toggle_play(self):
        if self.rec is None:
            self.status("未加载记录：请先在左侧列表双击一条记录（播放按钮不会自动选记录）")
            return
        self.set_playing(not self.playing)
        self.status("%s：%s @ %.2fs" % ("暂停" if not self.playing else "继续",
                                       self.rec.name, self.t_current()))

    def t_current(self):
        return self.rec.t[min(int(self.idx), self.rec.n - 1)] if self.rec is not None and self.rec.n else 0.0

    def tick(self):
        if not self.playing or self.rec is None:
            return
        # 帧间隔 85ms（约 12 帧/秒）：matplotlib 三维整图重绘每帧约
        # 数十至上百毫秒，间隔过短会让 after 回调挤爆事件队列、界面
        # 假死（首版 33ms 双重绘卡死实录）
        rate = 119.0 * self.speed
        self.idx += rate * 0.085
        if self.idx >= self.rec.n - 1:
            self.idx = self.rec.n - 1
            self.set_playing(False)
            self.status("播放结束：%s（%.1f 秒，末高 %.2f m）"
                        % (self.rec.name, self.rec.t[-1], self.rec.pos[-1, 2]))
        self.view.render(self.rec, int(self.idx))
        self.canvas.draw_idle()
        # 进度条程序化置值会触发 on_slider 再画一遍（双重绘），用
        # 标志位旁路；仅在进度漂移超半格时回写滑条
        self._updating = True
        try:
            self.slider.set(int(100.0 * self.idx / max(self.rec.n - 1, 1)))
        finally:
            self._updating = False
        self._tick_id = self.root.after(85, self.tick)

    def on_slider(self, val):
        if getattr(self, "_updating", False) or self.rec is None:
            return
        self.idx = float(val) / 100.0 * max(self.rec.n - 1, 1)
        self.view.render(self.rec, int(self.idx))
        self.canvas.draw_idle()

    def seek(self, i):
        self.idx = float(i)
        if self.rec is not None:
            self.view.render(self.rec, int(self.idx))
            self.canvas.draw_idle()

    def set_speed(self, s):
        self.speed = float(s.rstrip("×"))

    def set_window(self, s):
        """s∈{全程,8s,15s,30s}；写入 ReplayView.win（None=全程）并即时
        重渲染一帧（视窗切换立即可见，不等下一拍）"""
        self.view.win = None if s == "全程" else float(s.rstrip("s"))
        if self.rec is not None:
            self.view.render(self.rec, int(self.idx))
            self.canvas.draw_idle()

    # ---- 元数据 ----
    def save_meta(self):
        if self.rec is not None:
            self.rec.meta["tags"] = self.tags_var.get().strip()
            self.rec.meta["description"] = self.desc_text.get("1.0", "end").strip()
            self.rec.save_meta()
            self.status("已保存 tag/description → %s.meta.json" % self.rec.name)
            self.refresh_list()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    try:
        if "--selftest" in sys.argv:
            a = sys.argv.index("--selftest")
            selftest(sys.argv[a + 1], sys.argv[a + 2])
        else:
            ReplayGUI().run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
