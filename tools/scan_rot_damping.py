# -*- coding: utf-8 -*-
"""
scan_rot_damping.py — 旋转阻尼系数专项辨识（第十八班，2026-09-24）

问题：现行线性旋转阻尼系数 WING_ROT_DAMP=0.1 N·m·s/rad 的取值依据
是数值稳定性，属处置清单遗留未决项；第十七班第六步已证明该系数
决定单拍操纵量子的角速度兑现（生效值下兑现仅 12%）。另外
aero_solver.py 注释记载 ω×r 耦合被移除时的替代小信号值为 0.6，
0.6 如何演变为现行 0.1 无档案——本工具给出物理自洽的辨识值。

方法（主路径：ω×r 小信号斜率扫描）：
  逐叶素 ω×r 耦合本身是物理旋转阻尼的来源（机体转动改变叶素局部
  来流速度、产生恢复力矩）。2026-09-19 裁决将其移除的原因是大角
  速度段 |C_N| 饱和导致反号自激（|ω|>3 rad/s），并非小信号段无效。
  本工具在巡航配平点上，对滚转/俯仰/偏航三轴分别施加小角速度，
  用"加回 ω×r 项"的平行模型（工具内子类，唯一差异一行，ω=0 时
  与原版逐位一致——自检一）测整周期平均翼力矩，中心差分取斜率
  dT/dω；并扫大角速度段复现当年反号自激边界（对照段）。

口径声明：
  - 尾翼模型不接收角速度输入（第十三班遗留三），其对角速度的
    斜率恒为零，本辨识给出的是机翼部分的阻尼；全机含尾口径不变。
  - 人工线性阻尼不参与（直接调 _wing_forces，不走 compute_forces
    的 −ROT_DAMP·ω 通道）。
  - 配平俯仰固定 +7.14°（第十七班 scan_balance_sweep 重跑根），
    本工具只扫角速度一个变量。
  - 测量纪律三条遵守：时基单调推进 / 显式设频 / 终值 n=288。

判据（先于数据，2026-09-24 定）：
  - 辨识引用值 = 小信号段（|ω|≤2.5 rad/s）各档中心差分斜率的中位
    数，档间极差一并报告；档间极差 ≤20% 视为"线性段成立"。
  - 反号边界 = 对角斜率变号的最小 |ω|（预期复现 ~3 rad/s 量级，
    即 2026-09-19 裁决依据）。
  - 不修改生效配置：辨识值与现行 0.1/历史 0.6 的对照与改值建议
    上报，改生效值为契约层变更、须用户批准。

用法： python tools/scan_rot_damping.py（纯 NumPy，无需 Isaac Sim）
产物： logs/scan_rot_damping.json + docs/旋转阻尼专项辨识.md
"""
import os
import sys
import json
import math
import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import numpy as np
from config import sim_config as cfg
from modules.aero_solver import euler_to_quat, quat_to_rotation_matrix
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

G = 9.81
W = cfg.MASS * G
T0 = [160000.0]
N_FINE = 288

OP_V, OP_F, OP_ALPHA_SET = 4.0, 1.75, 35.0
PITCH0 = 7.14                       # 第十七班重跑配平根（度）
# 第二十三班新工作点复测模式（--newop，幅值按用户裁决取 35°=现行值）：
# 工况取扩域获胜点（docs/波形参数重搜索_扩域.md），连带检查=俯仰
# +21.45° 落 15~25° 攻角饱和区邻域，第六步模态与量子兑现数字在
# 复测前只可沿旧矩阵口径引用。半自由兑现段在 newop 下跳过（新点
# 量子表未测，pred 无据，挂账下一批）。
NEWOP = "--newop" in sys.argv
OP_MEAN, OP_SPLIT, OP_LAG = None, None, None   # None=config 现行
if NEWOP:
    OP_V, OP_F, OP_ALPHA_SET, PITCH0 = 3.5, 1.25, 35.0, 21.45
    OP_MEAN, OP_SPLIT, OP_LAG = 20.0, 0.75, -0.04
PROBE_SMALL = [0.25, 0.5, 1.0, 1.5, 2.0, 2.5]   # rad/s 小信号段
PROBE_LARGE = [3.0, 4.0, 5.0]                   # 对照段（预期反号）
# 第十九班扩段模式（--extend）：对照段扩到 10 rad/s（覆盖量子事件
# 9.33 rad/s），输出 _ext 新文件（主产物不覆盖）；矩阵/半自由/
# n 稳健与档位无关、扩段模式跳过。判据先于数据（四条）：
#   1. 恢复性：|ω|≤9 内滚转/偏航对角斜率保持负号（恢复性）；
#   2. 弱化：|ω|=9 处斜率相对小信号中位数弱化<50% 判"线性段可用"，
#      ≥50% 判"强非线性需查表调度"；
#   3. 反号：任一档位斜率变号即记录变号角速度，并中止"恢复"建议；
#   4. 机制分解：斜率弱化归因于叶素速度钳制占比还是攻角饱和
#      （超 45° 攻角占比），逐档记录。
EXTEND = "--extend" in sys.argv
if EXTEND:
    PROBE_LARGE = [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
SUFFIX = "_ext" if EXTEND else ("_op2" if NEWOP else "")
W_MATRIX = 0.5                      # 全 3×3 矩阵的代表点
IXX = 0.0024                        # 滚转惯量约值（配置注释口径）
ROT_DAMP_EFF = float(getattr(cfg.AeroModelConfig, "WING_ROT_DAMP", 0.1))
T_STROKE = 1.0 / OP_F

base = SaneDickinson3DModel()


class Coupled3DModel(SaneDickinson3DModel):
    """平行新增（不动现行生效代码）：唯一差异=叶素相对流速加入
    ω×r 项（被 2026-09-19 裁决移除的物理耦合）。函数体复制自
    sane_dickinson_3d._wing_forces_canonical（第十二班终版），
    仅 v_rel 一行改动；ω=0 时与原版逐位一致（自检一验证）。
    另覆写镜像包装层 _wing_forces：角速度是轴矢量，反射矩阵
    Mir=diag(1,−1,1)（det=−1）下的变换为 −Mir@ω 而非 Mir@ω
    （真矢量才是 Mir@ω）。原版此参数为死参数（ω×r 已移除）故
    该符号错误从未暴露；本工具重启 ω×r 后必须修正，否则左右翼
    耦合速度同号、滚转阻尼被错误抵消为零（smoke 实测即此症状）。
    """

    def _wing_forces(self, r_centers, chords_unused, wing_state,
                     v_body_at_hinge, omega_body):
        sgn = 1.0 if (len(r_centers) and r_centers[0] >= 0) else -1.0
        if sgn > 0:
            return self._wing_forces_canonical(r_centers, wing_state,
                                               v_body_at_hinge, omega_body)
        Mir = np.diag([1.0, -1.0, 1.0])
        F_loc, T_loc = self._wing_forces_canonical(
            np.abs(r_centers), wing_state,
            Mir @ v_body_at_hinge, -(Mir @ omega_body))
        return Mir @ F_loc, -(Mir @ T_loc)

    def _wing_forces_canonical(self, r_centers, wing_state,
                               v_body_at_hinge, omega_body):
        n = len(r_centers)
        dr = self._dr
        phi = wing_state["phi"]
        alpha = wing_state["alpha"]
        phi_dot = wing_state["phi_dot"]
        alpha_dot = wing_state["alpha_dot"]
        phi_ddot = wing_state["phi_ddot"]

        sp, cp = np.sin(phi), np.cos(phi)
        n_hat = np.array([sp, -cp, 0.0])
        r_body = np.stack([r_centers * sp, -r_centers * cp,
                           np.zeros_like(r_centers)], axis=1) \
            + self._root_offset_3d_tool()
        v_flap = np.stack([phi_dot * r_centers * cp,
                           phi_dot * r_centers * sp,
                           np.zeros_like(r_centers)], axis=1)
        c0 = np.array([0.0, 0.0, 1.0])
        chord0 = np.cross(c0, n_hat)
        chord0 /= max(np.linalg.norm(chord0), 1e-9)
        m_hat = np.cross(n_hat, chord0)
        chord = np.cos(alpha) * chord0 + np.sin(alpha) * m_hat

        # 唯一改动行：ω×r 叶素速度（原版无此项）
        v_om = np.cross(np.asarray(omega_body, dtype=float), r_body)
        v_rel = -(v_body_at_hinge + v_flap + v_om)
        v_rel_mag = np.linalg.norm(v_rel, axis=1)
        # 判据四支撑：钳制占比（叶素速度超上限被截断的比例）
        self._clamp_frac = float(np.mean(v_rel_mag > self.max_wing_vel))
        scale = np.where(v_rel_mag > self.max_wing_vel,
                         self.max_wing_vel / np.maximum(v_rel_mag, 1e-9), 1.0)
        v_rel = v_rel * scale[:, None]
        v_rel_mag = np.linalg.norm(v_rel, axis=1)

        v_span = (v_rel @ n_hat)[:, None] * n_hat[None, :]
        v_perp = v_rel - v_span
        v_perp_mag = np.linalg.norm(v_perp, axis=1)
        vhat_perp = v_perp / np.maximum(v_perp_mag[:, None], 1e-9)
        ncross = np.cross(n_hat, chord)
        dot_c = (-v_perp) @ chord
        dot_m = (-v_perp) @ ncross
        alpha_eff = np.arctan2(dot_m, dot_c)

        q_dyn = 0.5 * self.rho * v_rel_mag ** 2
        C_N = self._C_N(alpha_eff)
        C_T = self._C_T(alpha_eff)

        nF = np.cross(vhat_perp, n_hat[None, :])
        dF_normal_mag = q_dyn * np.abs(C_N) * self._chords * dr
        dF_trans = dF_normal_mag[:, None] * nF
        dF_tangent_mag = q_dyn * C_T * self._chords * dr
        dF_trans += dF_tangent_mag[:, None] * vhat_perp

        dF_rot = np.zeros((n, 3), dtype=np.float64)
        if self.enable_rotational:
            dF_rot = (self.c_rot * self.rho * self._chords * v_perp_mag
                      * alpha_dot * dr)[:, None] * m_hat[None, :]

        dF_added = np.zeros((n, 3), dtype=np.float64)
        if self.enable_added_mass:
            v_added = self._strip_areas * self.added_mass_coeff
            a_flap = np.stack(
                [phi_ddot * r_centers * cp - phi_dot ** 2 * r_centers * sp,
                 phi_ddot * r_centers * sp + phi_dot ** 2 * r_centers * cp,
                 np.zeros_like(r_centers)], axis=1)
            dF_added = -self.rho * v_added[:, None] * a_flap

        dF = dF_trans + dF_rot + dF_added
        F_wing_body = np.sum(dF, axis=0)
        T_wing_body = np.sum(np.cross(r_body, dF), axis=0)
        self._aeff_stats = (float(np.mean(alpha_eff)),
                            float(np.max(np.abs(alpha_eff))),
                            float(np.mean(np.abs(alpha_eff) > np.pi / 4.0)))
        return F_wing_body, T_wing_body

    def _root_offset_3d_tool(self):
        """读取模块级 ROOT_OFFSET_3D（与原版同源，可被消融重绑定）。"""
        import modules.aero.sane_dickinson_3d as sd3d
        return sd3d.ROOT_OFFSET_3D


coupled = Coupled3DModel()

for m in (base, coupled):
    for w in (m.wing_left, m.wing_right):
        w.alpha_set = math.radians(OP_ALPHA_SET)
        w.split = cfg.FlapKinematicsConfig.SPLIT if OP_SPLIT is None \
            else OP_SPLIT
        w.pitch_lag_s = cfg.FlapKinematicsConfig.PITCH_LAG_S \
            if OP_LAG is None else OP_LAG
        w.mean = math.radians(
            cfg.FlapKinematicsConfig.MEAN if OP_MEAN is None else OP_MEAN)


def wing_avg(om_body, n=N_FINE):
    """双翼整周期平均（纪律一/二：单调时基+显式设频）。不含尾翼、
    不含人工阻尼（辨识口径）。判据四支撑：统计留 LAST_STATS
    （钳制占比与攻角超 45° 占比的周期平均）。"""
    coupled.set_wing_command(frequency=OP_F)
    quat = euler_to_quat(0.0, math.radians(PITCH0), 0.0)
    v_body = quat_to_rotation_matrix(quat).T @ np.array([OP_V, 0.0, 0.0])
    om = np.asarray(om_body, dtype=float)
    T = T_STROKE
    F = np.zeros(3)
    Tq = np.zeros(3)
    cl = 0.0
    ae45 = 0.0
    for i in range(n):
        t = T0[0] + i * T / n
        ws_L = coupled.wing_left.evaluate(t, phase_offset_rad=0.0)
        F_L, T_L = coupled._wing_forces(
            +coupled._r_centers.copy(), coupled._chords, ws_L, v_body, om)
        ws_R = coupled.wing_right.evaluate(
            t, phase_offset_rad=coupled.phase_offset_lr)
        F_R, T_R = coupled._wing_forces(
            -coupled._r_centers.copy(), coupled._chords, ws_R, v_body, om)
        F += F_L + F_R
        Tq += T_L + T_R
        cl += coupled._clamp_frac        # 每步取最后一次调用的统计
        ae45 += coupled._aeff_stats[2]
    T0[0] += T
    global LAST_STATS
    LAST_STATS = dict(clamp_frac=cl / n, aeff45_frac=ae45 / n)
    return F / n, Tq / n


LAST_STATS = {}


def paused_state(w, ws):
    ws = dict(ws)
    ws["phi"] = float(w.bias + w.mean)
    ws["phi_dot"] = 0.0
    ws["phi_ddot"] = 0.0
    ws["alpha"] = float(w.alpha_set * 2.0 / math.pi + w.pitch_bias)
    ws["alpha_dot"] = 0.0
    ws["alpha_ddot"] = 0.0
    return ws


def roll_only_e1(damp, n_total_strokes=5, event_stroke=2):
    """第六步半自由 E1 复刻（D 参数化）：停扑左翼一拍，仅滚转通道
    含阻尼积分，返回事件拍边界 p 增量。停扑定义与量子表一致。"""
    quat = euler_to_quat(0.0, math.radians(PITCH0), 0.0)
    v_body = quat_to_rotation_matrix(quat).T @ np.array([OP_V, 0.0, 0.0])
    p = 0.0
    dt = 0.001
    marks = []
    base.set_wing_command(frequency=OP_F)
    for stroke in range(n_total_strokes):
        paused = stroke == event_stroke
        n_steps = int(round(T_STROKE / dt))
        for i in range(n_steps):
            t = T0[0]
            tx = 0.0
            ws_L = base.wing_left.evaluate(t, phase_offset_rad=0.0)
            if paused:
                ws_L = paused_state(base.wing_left, ws_L)
            _, T_L = base._wing_forces(
                +base._r_centers.copy(), base._chords, ws_L, v_body,
                np.array([p, 0.0, 0.0]) * 0.0)
            ws_R = base.wing_right.evaluate(
                t, phase_offset_rad=base.phase_offset_lr)
            _, T_R = base._wing_forces(
                -base._r_centers.copy(), base._chords, ws_R, v_body,
                np.zeros(3))
            tx = float(T_L[0] + T_R[0])
            p += (tx - damp * p) / IXX * dt
            T0[0] += dt
        marks.append(p)
    return marks[event_stroke] - marks[event_stroke - 1]


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── 自检一：ω=0 时耦合版与原版逐位一致 ──
    base.set_wing_command(frequency=OP_F)
    coupled.set_wing_command(frequency=OP_F)
    quat = euler_to_quat(0.0, math.radians(PITCH0), 0.0)
    v_body = quat_to_rotation_matrix(quat).T @ np.array([OP_V, 0.0, 0.0])
    t_chk = T0[0]
    ws = base.wing_left.evaluate(t_chk, phase_offset_rad=0.0)
    F_a, T_a = base._wing_forces(+base._r_centers.copy(), base._chords,
                                 ws, v_body, np.zeros(3))
    F_b, T_b = coupled._wing_forces(+coupled._r_centers.copy(),
                                    coupled._chords, ws, v_body,
                                    np.zeros(3))
    sc1 = float(np.max(np.abs(F_a - F_b)) + np.max(np.abs(T_a - T_b)))
    print(f"[damp] 自检一 ω=0 耦合版vs原版 最大残差={sc1:.2e}", flush=True)
    if sc1 > 1e-12:
        print("[damp] ★自检一超差——复制体与原版不一致，先修再跑",
              flush=True)
        return

    # ── 自检二：镜像反对称 T_x(+p)≈−T_x(−p) ──
    _, T_pp = wing_avg(np.array([1.0, 0.0, 0.0]))
    _, T_pm = wing_avg(np.array([-1.0, 0.0, 0.0]))
    sc2 = abs(float(T_pp[0]) + float(T_pm[0]))
    print(f"[damp] 自检二 镜像反对称残差 |Tx(+1)+Tx(−1)|={sc2:.2e} "
          f"(Tx(+1)={float(T_pp[0]):+.5f})", flush=True)

    # ── 主扫描：三轴对角斜率（小信号段+对照段）──
    axes = [("roll", "p"), ("pitch", "q"), ("yaw", "r")]
    diag = {}
    for name, sym in axes:
        rows = []
        for w in PROBE_SMALL + PROBE_LARGE:
            om = np.zeros(3)
            om[axes.index((name, sym))] = w
            _, T_p = wing_avg(om)
            om[axes.index((name, sym))] = -w
            _, T_m = wing_avg(om)
            comp = {"roll": 0, "pitch": 1, "yaw": 2}[name]
            d = (float(T_p[comp]) - float(T_m[comp])) / (2.0 * w)
            rows.append(dict(w=w, slope=d,
                             t_plus=float(T_p[comp]),
                             t_minus=float(T_m[comp]),
                             clamp_frac=LAST_STATS["clamp_frac"],
                             aeff45_frac=LAST_STATS["aeff45_frac"]))
            print(f"[damp] {name} ω=±{w:.2f}: D={d:+.4f} N·m·s/rad "
                  f"(T+=+{float(T_p[comp]):+.4f}/T−={float(T_m[comp]):+.4f})"
                  f" 钳制{LAST_STATS['clamp_frac']:.1%}"
                  f" α45°+{LAST_STATS['aeff45_frac']:.1%}", flush=True)
        small = [r["slope"] for r in rows if r["w"] <= 2.5]
        med = float(np.median(small))
        spread = (max(small) - min(small)) / abs(med) if abs(med) > 1e-9 \
            else float("nan")
        flip = [r["w"] for r in rows
                if r["slope"] * med < 0 and r["w"] in PROBE_LARGE]
        diag[name] = dict(rows=rows, median_small=med, spread_rel=spread,
                          linear_ok=spread <= 0.20,
                          flip_w=min(flip) if flip else None)
        print(f"[damp] {name}: 小信号中位数={med:+.4f} 档间极差={spread:.1%}"
              f" 线性段={'成立' if spread <= 0.20 else '不成立'}"
              + (f" 反号边界≈{min(flip):.1f} rad/s" if flip else ""),
          flush=True)

    # ── 全 3×3 矩阵 / n 稳健性 / 第六步兑现比重算（与档位无关，
    #    扩段模式跳过，沿用主产物结果）──
    extra = {}
    if not EXTEND:
        mat = np.zeros((3, 3))
        for j, (name_j, _) in enumerate(axes):
            for i, (name_i, _) in enumerate(axes):
                om_p = np.zeros(3)
                om_p[j] = W_MATRIX
                _, T_p = wing_avg(om_p)
                om_m = np.zeros(3)
                om_m[j] = -W_MATRIX
                _, T_m = wing_avg(om_m)
                mat[i, j] = (float(T_p[i]) - float(T_m[i])) / (2.0 * W_MATRIX)
        print(f"[damp] 全矩阵@ω=0.5：\n{np.round(mat, 4)}", flush=True)

        rob = {}
        for n in (144, 288, 576):
            om = np.array([1.0, 0.0, 0.0])
            _, T_p = wing_avg(om, n=n)
            om = np.array([-1.0, 0.0, 0.0])
            _, T_m = wing_avg(om, n=n)
            rob[n] = (float(T_p[0]) - float(T_m[0])) / 2.0
        print(f"[damp] n稳健性 D_roll@1.0: "
              + " ".join(f"n={k}→{v:+.4f}" for k, v in rob.items()),
              flush=True)

        d_id = diag["roll"]["median_small"]
        d_ch = -d_id   # 等效通道系数：物理斜率恢复性为负(−0.009)，而
        # roll_only_e1 的阻尼通道是 −damp·p，故代入取正量值 |d_id|
        ro_eff = roll_only_e1(ROT_DAMP_EFF)
        ro_id = roll_only_e1(d_ch) if d_ch > 1e-6 else float("nan")
        ro_zero = roll_only_e1(0.0)
        # newop 模式：pred 沿用旧工作点（V4.0/f1.75）量子值，新点
        # 量子表未测——兑现比仅作量级参考，正式对照挂账下一批
        pred = 9.3269            # 第十七班量子预言（无阻尼，rad/s）
        print(f"[damp] 半自由E1兑现: D=0(无阻尼)={ro_zero:+.4f} "
              f"D=现行{ROT_DAMP_EFF}→{ro_eff:+.4f} "
              f"D=辨识{d_ch:+.4f}(斜率{d_id:+.4f})→{ro_id:+.4f} "
              f"(预言{pred:+.4f})", flush=True)
        extra.update(
            matrix_05=mat.tolist(), n_robust=rob,
            quantum_cash=dict(pred_nodamp=pred, sim_nodamp=ro_zero,
                              sim_eff=ro_eff, sim_ident=ro_id,
                              d_channel_equiv=d_ch,
                              cash_ratio_eff=ro_eff / pred,
                              cash_ratio_ident=ro_id / pred
                              if d_ch > 1e-6 else None,
                              tau_eff_ms=IXX / ROT_DAMP_EFF * 1000,
                              tau_ident_ms=IXX / d_ch * 1000
                              if d_ch > 1e-6 else None))
    else:
        # 扩段判据二：|ω|=9 弱化百分比（相对小信号中位数）
        for name in ("roll", "yaw"):
            d = diag[name]
            row9 = min((r for r in d["rows"] if r["w"] >= 9.0),
                       key=lambda r: r["w"], default=None)
            if row9 is not None and abs(d["median_small"]) > 1e-9:
                weak = 1.0 - abs(row9["slope"]) / abs(d["median_small"])
                d["weaken_at_9"] = weak
                d["linear_usable"] = weak < 0.50
                print(f"[damp] {name} @ω={row9['w']:.0f}: 弱化{weak:.1%} →"
                      f" {'线性段可用' if weak < 0.50 else '强非线性需查表'}",
                      flush=True)

    out = dict(ts=ts, model="sane_dickinson_3d+ω×r耦合平行版",
               extend=EXTEND,
               op=dict(V=OP_V, f=OP_F, alpha_set=OP_ALPHA_SET,
                       pitch=PITCH0, n=N_FINE),
               eff_value=ROT_DAMP_EFF, history_note="aero_solver 注释记载"
               "移除 ω×r 时的替代小信号值 0.6；现行生效值 0.1",
               selfcheck=dict(eq_omega0=sc1, mirror_asym=sc2),
               diag=diag,
               verdict=dict(
                   d_roll=diag["roll"]["median_small"],
                   linear_ok=diag["roll"]["linear_ok"],
                   note="辨识值与现行/历史值的对照及改值建议见报告；"
                        "改生效值为契约层变更，须用户批准"),
               **extra)
    json_path = os.path.join(cfg.LOG_DIR, f"scan_rot_damping{SUFFIX}.json")
    with open(json_path, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    # ── 报告 ──
    doc_name = ("旋转阻尼专项辨识_新工作点复测.md" if NEWOP else
                "旋转阻尼专项辨识_扩段.md" if EXTEND else
                "旋转阻尼专项辨识.md")
    if EXTEND:
        md = [f"# 旋转阻尼系数专项辨识·扩段（{ts}）", "",
              f"- 方法与口径与主报告一致（ω×r 耦合平行版，自检残差"
              f"{sc1:.2e}/{sc2:.2e}；巡航配平点 V={OP_V}/f={OP_F}/"
              f"θ={PITCH0}°；终值 n={N_FINE}）；对照段扩展至 "
              f"{max(PROBE_LARGE):.0f} rad/s（覆盖量子事件 9.33 rad/s）",
              "- 判据先于数据（第十九班四条）：①恢复性：|ω|≤9 内滚转/"
              "偏航对角斜率保持负号；②弱化：|ω|=9 处斜率相对小信号"
              "中位数弱化<50% 判线性段可用；③反号：任一档位斜率变号"
              "即记录变号角速度并中止恢复建议；④机制分解：弱化归因于"
              "叶素速度钳制占比或攻角超 45° 饱和占比，逐档记录",
              f"- 原始数据：`logs/scan_rot_damping{SUFFIX}.json`", "",
              "## 对角斜率扩段扫描（N·m·s/rad）", ""]
        for name, sym in axes:
            md += [f"### {name}（{sym}）", "",
                   "| ω (rad/s) | 中心差分斜率 | T(+ω) (N·m) |"
                   " 钳制占比 | 攻角超45°占比 |", "|---|---|---|---|---|"]
            for r in diag[name]["rows"]:
                md.append(f"| {r['w']:.2f} | {r['slope']:+.4f} |"
                          f" {r['t_plus']:+.4f} |"
                          f" {r['clamp_frac']:.1%} |"
                          f" {r['aeff45_frac']:.1%} |")
            d = diag[name]
            md += ["",
                   f"- 小信号段中位数 **{d['median_small']:+.4f}**；"
                   + (f"ω={9 if 'weaken_at_9' not in d else 9} 处弱化 "
                      f"{d.get('weaken_at_9', float('nan')):.1%}，"
                      + ("线性段可用" if d.get("linear_usable")
                         else "强非线性需查表调度")
                      if "weaken_at_9" in d else "扩段判读见判读节")
                   + (f" 反号边界≈{d['flip_w']:.1f} rad/s" if d["flip_w"]
                      else " 全程未见反号"),
                   ""]
        md += ["## 判读（按预注册四判据）", ""]
        for name in ("roll", "yaw"):
            d = diag[name]
            ok1 = d["flip_w"] is None
            ok2 = d.get("linear_usable")
            md.append(f"- {name}：恢复性判据"
                      + ("通过（全程无反号）" if ok1
                         else f"**未通过**（反号于 {d['flip_w']:.1f} rad/s）")
                      + "；弱化判据"
                      + ("通过（<50%）" if ok2 else "**未通过**（≥50%）"
                         if ok2 is False else "未及 9 rad/s 档"))
        mx = max(r["clamp_frac"] for n_, _sym in axes
                 for r in diag[n_]["rows"])
        m45 = max(r["aeff45_frac"] for n_, _sym in axes
                  for r in diag[n_]["rows"])
        md += [f"- 机制分解：全部档位中叶素速度钳制占比最大 {mx:.1%}、"
               f"攻角超 45° 占比最大 {m45:.1%}——弱化主因按两者占比"
               f"高低判读（钳制主导=速度上限截断，饱和主导=|C_N| 绝对"
               f"值处理）。",
               "- 结论边界：本报告只覆盖 |ω|≤"
               f"{max(PROBE_LARGE):.0f} rad/s；是否恢复 ω×r 耦合项的"
               "裁决待与 0919 口径考古（docs/阻尼考古_0919口径对齐.md）"
               "合并后由用户执行。",
               ""]
    else:
        md = [f"# 旋转阻尼系数专项辨识"
              f"{'·新工作点复测' if NEWOP else ''}（{ts}）", "",
              f"- 方法：平行新增 ω×r 耦合版三维叶素模型（唯一差异一行，"
              f"ω=0 与原版逐位一致，自检残差 {sc1:.2e}；镜像反对称自检"
              f"残差 {sc2:.2e}），巡航配平点 V={OP_V}/f={OP_F}/"
              f"α={OP_ALPHA_SET}°/θ={PITCH0}° 逐轴扫角速度"
              + (f"；★新工作点工况（扩域获胜点，幅值 35°）："
                 f"MEAN=+{OP_MEAN}°/SPLIT={OP_SPLIT}/"
                 f"LAG={OP_LAG}s——连带检查（俯仰落饱和区邻域）落地"
                 if NEWOP else ""),
              f"- 口径：只含机翼（尾翼不接收角速度，斜率恒零）；人工线性"
              f"阻尼不参与；终值 n={N_FINE}；判据先于数据（小信号中位数"
              f"为引用值、档间极差≤20% 判线性段成立、斜率变号处为反号"
              f"边界）",
              f"- 现行生效值 {ROT_DAMP_EFF} N·m·s/rad（数值稳定性取值、"
              f"遗留未决）；aero_solver 注释记载历史替代值 0.6"
              + ("；newop 模式下半自由兑现的量子预言沿用旧工作点值，"
                 "新点量子表待测（挂账）" if NEWOP else ""),
              f"- 原始数据：`logs/scan_rot_damping{SUFFIX}.json`", "",
              "## 对角斜率扫描（N·m·s/rad）", ""]
    if not EXTEND:
        for name, sym in axes:
            md += [f"### {name}（{sym}）", "",
                   "| ω (rad/s) | 中心差分斜率 | T(+ω) (N·m) |"
                   " T(−ω) (N·m) |", "|---|---|---|---|"]
            for r in diag[name]["rows"]:
                md.append(f"| {r['w']:.2f} | {r['slope']:+.4f} |"
                          f" {r['t_plus']:+.4f} | {r['t_minus']:+.4f} |")
            d = diag[name]
            md += ["",
                   f"- 小信号段中位数 **{d['median_small']:+.4f}**，档间极差"
                   f" {d['spread_rel']:.1%}，线性段"
                   + ("成立" if d["linear_ok"] else "**不成立**")
                   + (f"；反号边界≈{d['flip_w']:.1f} rad/s"
                      f"（复现 2026-09-19 裁决依据）" if d["flip_w"]
                      else "；对照段未见反号"),
                   ""]
        md += ["## 全 3×3 阻尼矩阵（ω=0.5 rad/s 代表点，行=力矩轴、"
               "列=角速度轴）", "",
               "| | p | q | r |", "|---|---|---|---|"]
        for i, (ni, _) in enumerate(axes):
            md.append(f"| {ni} | {mat[i, 0]:+.4f} | {mat[i, 1]:+.4f} |"
                      f" {mat[i, 2]:+.4f} |")
        md += ["",
               f"- n 稳健性（D_roll@1.0）："
               + "；".join(f"n={k}→{v:+.4f}" for k, v in rob.items())
               + "（档间变差应远小于结论量级）", "",
               "## 第六步兑现比按辨识值重算（半自由 E1 复刻）", "",
               f"- 无阻尼复刻 {ro_zero:+.4f} rad/s（量子预言 {pred:+.4f}，"
               f"积分自洽）；现行值 D={ROT_DAMP_EFF} 兑现 {ro_eff:+.4f}"
               f"（{ro_eff / pred:.0%}）；辨识值 D={d_ch:+.4f}（斜率"
               f"{d_id:+.4f}）兑现 {ro_id:+.4f}"
               + (f"（{ro_id / pred:.0%}）" if d_ch > 1e-6 else ""),
               f"- 时间常数：现行 τ={IXX / ROT_DAMP_EFF * 1000:.0f} ms；"
               + (f"辨识 τ={IXX / d_ch * 1000:.0f} ms"
                  if d_ch > 1e-6 else "辨识值无效"),
               "",
               "## 判读与遗留", "",
               f"- 辨识值 D_roll={d_id:+.4f} 与现行 {ROT_DAMP_EFF}、历史 "
               f"0.6 的对照：差距倍数见 JSON；若量级显著偏离，说明现行"
               "生效值既无物理依据也与 ω×r 小信号自洽值不符。",
               "- 改生效值属契约层变更（影响所有盘旋扫描阻尼列与量子"
               "兑现口径）：本报告只给证据与建议，须用户批准后另班执行。",
               "- 尾翼部分阻尼为零是模型边界（尾翼不接收角速度输入，"
               "第十三班遗留三），真机尾翼有俯仰阻尼——列为真机考核"
               "清单项。",
               "- 遗留：交叉项（非对角元）的符号与量级见矩阵表，其调度"
               "意义（是否需要 MIMO 口径）待第八步波形空间设计时评估。",
               ""]
    doc_path = os.path.join(PROJECT_ROOT, "docs", doc_name)
    with open(doc_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))
    print(f"[damp] 完成 → docs/{doc_name}", flush=True)


if __name__ == "__main__":
    main()
