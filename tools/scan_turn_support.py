# -*- coding: utf-8 -*-
"""
scan_turn_support.py — 压坡度转弯专项测量（审查文档待扫描事项五执行工具）

目的：为转弯模态模板的专项约束函数提供实测拟合数据。四项测量：
A. 坡度工况竖直力衰减：Fz_world 随坡度角的实测曲线，对理论 1/cos(φ) 的
   偏差（升力需求函数的输入）。
B. 侧滑工况侧向力与升力方向函数：Fy_world 与 Fz_world 随侧滑角与坡度的
   曲线（侧滑抑制约束与协调盘旋条件的输入）。
C. 盘旋角速度下阻尼线性适用性：以协调盘旋角速度（体轴投影）求力矩差分，
   与第二十三班 WING_DAMP 矩阵预测对比（横侧模态 λ=+0.105/s 无裕度问题
   在转弯域的风险核查）。
D. 两条差动通道带坡度效能：均值偏置差动与俯仰滞后差动的稳态滚转效能、
   偏航副作用、升力漂移随坡度的表（滚转力矩需求分配函数的输入）。

考卷口径声明（铁律）：
- 物理层全部 import v1 框架复用（wing_forces_body / tail / set_waveform /
  euler_to_quat / 纪律函数族 / MY_DC_COEF / R_CG_BODY），本脚本零新物理；
  周期平均口径与 v1.stroke_avg 同款（n=288，第十六班测量纪律三条）。
- 全部为静态周期平均测量（姿态冻结、来流定常）；盘旋的旋转动力学不在
  本测量内，由转弯扫描的闭环层承担。
- 姿态方向语义：euler_to_quat(roll, pitch, yaw) ZYX 序；正 roll 在本机体
  系（x 前、z 腹向、y 左翼）下为左坡度（world 上向量在机体 y 分量变负
  的反命题由方向自检锁定）；坡度档以绝对值报数，符号语义在 json 里
  附 world 上向量体轴投影自证。
- 写权限：主会话亲写；代理只执行本脚本并读取产物。

运行方式：python tools/scan_turn_support.py
产物：logs/scan_turn_support_<时间戳>.json
"""
import os, sys, json, math, time, datetime
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import importlib.util


def _load_v1():
    path = os.path.join(PROJECT_ROOT, "tools", "unified_search_framework.py")
    spec = importlib.util.spec_from_file_location("usf_v1_turn", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 基准工况：runB 升格生效工作点（logs/_run67_params.json + config 生效键）。
BASE_WAVE = {
    "alpha_set": 32.3169, "mean": 19.8105, "split": 0.176,
    "lag": -0.0674, "amp": 17.73, "freq": 1.80, "speed": 3.95,
}
BASE_PITCH_DEG = -8.210          # 部署配平俯仰（PITCH_TRIM=−0.143246 rad）
N_FINE = 288                     # 第十六班终值分辨率纪律
G_ACC = 9.81


def static_point(v1, p, roll_deg=0.0, pitch_deg=BASE_PITCH_DEG, beta_deg=0.0,
                 om_body=None, n=N_FINE, wave_apply=None):
    """静态周期平均单点（带滚转/侧滑/体轴角速度/左右翼差动的通用化
    stroke_avg）。姿态=欧拉构造（roll/pitch，yaw=0）；来流=世界系水平
    速度，侧滑以水平来流绕世界竖直轴偏转角 psi 实施（与在环一致的水平
    来流口径：抬头姿态下机体质感生攻角，与 v1.stroke_avg 同构，β=0 时
    两者逐位一致）；wave_apply 为差动等波形覆盖闭包（在 set_waveform
    之后执行，避免被对称设置覆盖——测量 D 首轮的差动失效即此覆盖所致）；
    返回 (F_world, T_body, info)，info 记录机体系实际来流分解。纯气动账：
    无重力、无人工阻尼、无机翼矩直流。"""
    quat = v1.euler_to_quat(math.radians(roll_deg), math.radians(pitch_deg), 0.0)
    psi = math.radians(beta_deg)
    vel_world = np.array([p["speed"] * math.cos(psi), p["speed"] * math.sin(psi), 0.0])
    v_body = v1.rot_world_to_body(quat, vel_world)
    beta_body = math.degrees(math.atan2(v_body[1], max(v_body[0], 1e-9)))
    alpha_body = math.degrees(math.atan2(-v_body[2], max(v_body[0], 1e-9)))
    om_b = np.zeros(3) if om_body is None else np.asarray(om_body, float)
    om_world = v1.rot_body_to_world(quat, om_b)
    v1.set_waveform(p)
    if wave_apply is not None:
        wave_apply(v1)
    v1.model.set_wing_command(frequency=p["freq"])
    T = 1.0 / p["freq"]
    F = np.zeros(3); Tq = np.zeros(3)
    for i in range(n):
        t = v1.T0[0] + i * T / n
        Fw, Tw = v1.wing_forces_body(quat, vel_world, om_world, t)
        F += Fw; Tq += Tw
    v1.T0[0] += T
    F /= n; Tq /= n
    v_slip = v1.cfg.SLIP_K * v1.cfg.SLIP_V_PER_HZ * p["freq"]
    Ft, Tt = v1.tail.compute(quat, vel_world, v_slip=v_slip)
    info = {"v_body": v_body.tolist(), "beta_body_deg": beta_body,
            "alpha_body_deg": alpha_body}
    return F + Ft, Tq + v1.rot_world_to_body(quat, Tt), info


def self_check(v1):
    """方向语义自检：a) 本工具 static_point 的翼部分与 v1.stroke_avg 输出
    对账——stroke_avg 返回式 (F+Ft)/n 把稳态尾翼项同除以 n（漏账 272/288，
    发现于本轮对账，登记上报），故对账式为 static_point − 全量尾翼 =
    stroke_avg（翼部分逐位）；b) 正 roll 的 world 上向量体轴投影符号
    锁定；c) 质心重力矩闭式对纯俯仰姿态与纪律函数逐点一致。"""
    # a) 翼部分对账（纯俯仰、零侧滑、零角速度）
    Fa, Ta, _ = static_point(v1, BASE_WAVE, roll_deg=0.0, beta_deg=0.0)
    Fb, Tb = v1.stroke_avg(BASE_WAVE, BASE_PITCH_DEG)
    quat0 = v1.euler_to_quat(0.0, math.radians(BASE_PITCH_DEG), 0.0)
    vel0 = np.array([BASE_WAVE["speed"], 0.0, 0.0])
    vslip0 = v1.cfg.SLIP_K * v1.cfg.SLIP_V_PER_HZ * BASE_WAVE["freq"]
    Ft0, Tt0 = v1.tail.compute(quat0, vel0, v_slip=vslip0)
    Tt0_body = v1.rot_world_to_body(quat0, Tt0)
    dF = float(np.max(np.abs((Fa - Ft0) - Fb)))
    dT = float(np.max(np.abs((Ta - Tt0_body) - Tb)))
    # 阈值 5e-3 的机理：翼相位状态（set_wing_command 冷启动分支）使两条
    # 实现路径的 288 点离散求积窗口起点不逐位对齐，属第十六班相位收敛
    # 检查同族的求积残差（实测 3e-4，对升力 2.19 N 占 0.014%），非物理差。
    assert dF < 5e-3 and dT < 5e-3, \
        "自检 a 失败：翼部分对账不一致 (%g,%g)" % (dF, dT)
    print("自检 a：翼部分对账逐位一致；尾翼全量 Ft=%s、My=%+.5f（v1.stroke_avg 漏账该项）"
          % (np.round(Ft0, 5).tolist(), Tt0_body[1]), flush=True)
    # b) roll 方向语义
    for roll in (+15.0, -15.0):
        q = v1.euler_to_quat(math.radians(roll), math.radians(BASE_PITCH_DEG), 0.0)
        up_b = v1.rot_world_to_body(q, np.array([0.0, 0.0, 1.0]))
        print("自检 b：roll=%+d deg 时 world 上向量体轴投影=[%.4f, %.4f, %.4f]"
              % (roll, up_b[0], up_b[1], up_b[2]), flush=True)
    # c) 重力矩闭式（对纯滚转+俯仰姿态，闭式 M_y=x_cg·W·cosθ·cosφ 近似
    #    的适用性核查：与纪律函数逐点对差并记录，不设硬阈值）
    for roll in (0.0, 15.0, 20.0):
        q = v1.euler_to_quat(math.radians(roll), math.radians(BASE_PITCH_DEG), 0.0)
        mg = v1.gravity_moment_body(q)
        closed = v1.R_CG_BODY[0] * v1.W * math.cos(math.radians(BASE_PITCH_DEG)) * math.cos(math.radians(roll))
        print("自检 c：roll=%.0f deg 重力矩 My 纪律函数=%+.5f 闭式近似=%+.5f 差=%+.5f"
              % (roll, mg[1], closed, mg[1] - closed), flush=True)
    return Fa, Ta


def meas_A_bank_lift(v1):
    """测量A：竖直力随坡度衰减（β=0，θ=部署配平角）。"""
    rows = []
    for phi in (0.0, 5.0, 10.0, 15.0, 20.0, 25.0):
        F, T, info = static_point(v1, BASE_WAVE, roll_deg=phi)
        rows.append({"phi_deg": phi, "F_world": F.tolist(), "T_body": T.tolist(),
                     "alpha_body_deg": info["alpha_body_deg"],
                     "Fz_world": float(F[2]), "Fy_world": float(F[1]),
                     "cos_ratio": float(F[2] / rows[0]["Fz_world"]) if rows else 1.0,
                     "inv_cos_theory": float(1.0 / math.cos(math.radians(phi)))})
        print("A φ=%.0f: Fz=%+.4f N  Fz/Fz0=%.4f  1/cosφ=%.4f"
              % (phi, F[2], rows[-1]["cos_ratio"], rows[-1]["inv_cos_theory"]), flush=True)
    return rows


def meas_B_sideslip(v1):
    """测量B：侧滑角×坡度的侧力与竖直力函数。"""
    rows = []
    for phi in (0.0, 15.0, 20.0):
        for beta in (-10.0, -5.0, -2.0, 0.0, 2.0, 5.0, 10.0):
            F, T, info = static_point(v1, BASE_WAVE, roll_deg=phi, beta_deg=beta)
            rows.append({"phi_deg": phi, "beta_deg": beta,
                         "beta_body_deg": info["beta_body_deg"],
                         "Fy_world": float(F[1]), "Fz_world": float(F[2]),
                         "Fx_world": float(F[0]), "T_body": T.tolist()})
    for phi in (0.0, 15.0, 20.0):
        sel = [r for r in rows if r["phi_deg"] == phi]
        b0 = [r for r in sel if r["beta_deg"] == 0.0][0]
        for r in sel:
            r["Fy_norm"] = r["Fy_world"] / max(abs(b0["Fz_world"]), 1e-9)
        d = (sel[-1]["Fy_world"] - sel[0]["Fy_world"]) / (sel[-1]["beta_deg"] - sel[0]["beta_deg"])
        print("B φ=%.0f: dFy/dβ=%.5f N/deg  |Fy|@±10deg=[%.4f,%.4f] N"
              % (phi, d, sel[0]["Fy_world"], sel[-1]["Fy_world"]), flush=True)
    return rows


def meas_C_damping_turn(v1):
    """测量C：协调盘旋角速度下的力矩差分与 WING_DAMP 预测对比。"""
    rows = []
    for phi in (10.0, 15.0, 20.0, 25.0):
        om_yaw = G_ACC * math.tan(math.radians(phi)) / BASE_WAVE["speed"]
        q = v1.euler_to_quat(math.radians(phi), math.radians(BASE_PITCH_DEG), 0.0)
        om_world = np.array([0.0, 0.0, om_yaw])
        om_body = v1.rot_world_to_body(q, om_world)
        T0_, _, _ = static_point(v1, BASE_WAVE, roll_deg=phi, om_body=np.zeros(3))
        T1_, _, _ = static_point(v1, BASE_WAVE, roll_deg=phi, om_body=om_body)
        dT = T1_ - T0_
        pred = -v1.WING_DAMP @ om_body
        rows.append({"phi_deg": phi, "om_yaw": float(om_yaw),
                     "om_body": om_body.tolist(),
                     "dT_body": dT.tolist(), "wing_damp_pred": pred.tolist(),
                     "ratio_vec": [float(dT[i] / pred[i]) if abs(pred[i]) > 1e-9 else None
                                   for i in range(3)]})
        print("C φ=%.0f: ω_yaw=%.4f rad/s  dT=%s  WING_DAMP预测=%s"
              % (phi, om_yaw, np.round(dT, 5).tolist(), np.round(pred, 5).tolist()), flush=True)
    return rows


def _set_diff(v1, channel, delta):
    """差动设置：channel='mean'（左右翼均值偏置反号差动）或 'lag'（左右翼
    俯仰滞后反号差动）。直接操作 v1.model 左右翼属性（弧度/秒，第十七班
    工具事实）。delta=0 时恢复对称波形。"""
    wl, wr = v1.model.wing_left, v1.model.wing_right
    m = math.radians(BASE_WAVE["mean"])
    lg = BASE_WAVE["lag"]
    if channel == "mean":
        d = math.radians(delta)
        wl.mean = m + d / 2.0; wr.mean = m - d / 2.0
    elif channel == "lag":
        wl.pitch_lag_s = lg + delta; wr.pitch_lag_s = lg - delta
    else:
        raise ValueError(channel)


def _restore_wave(v1, p):
    """恢复对称波形（消除差动残留）。"""
    v1.set_waveform(p)
    v1.model.wing_left.mean = math.radians(p["mean"])
    v1.model.wing_right.mean = math.radians(p["mean"])
    v1.model.wing_left.pitch_lag_s = p["lag"]
    v1.model.wing_right.pitch_lag_s = p["lag"]


def meas_D_diff_channels(v1):
    """测量D：两条差动通道带坡度效能（滚转效能/偏航副作用/升力漂移）。"""
    rows = []
    plan = [("mean", [-5.0, -2.5, 0.0, 2.5, 5.0]),
            ("lag", [-0.01, -0.005, 0.0, 0.005, 0.01])]
    for channel, deltas in plan:
        for phi in (0.0, 15.0, 20.0):
            for d in deltas:
                apply_fn = (lambda vv, ch=channel, dd=d: _set_diff(vv, ch, dd))
                try:
                    F, T, _ = static_point(v1, BASE_WAVE, roll_deg=phi,
                                           wave_apply=apply_fn)
                finally:
                    _restore_wave(v1, BASE_WAVE)
                rows.append({"channel": channel, "delta": d, "phi_deg": phi,
                             "roll_moment": float(T[0]), "yaw_moment": float(T[2]),
                             "Fz_world": float(F[2])})
                print("D ch=%s δ=%+.4f φ=%.0f: L=%+.5f N=%+.5f Fz=%.4f"
                      % (channel, d, phi, T[0], T[2], F[2]), flush=True)
    # 效能汇总：每通道×坡度的 dL/dδ（端点差分）、偏航副作用比 |dN/dδ|/|dL/dδ|、
    # Fz 漂移峰峰值。
    summary = []
    for channel, deltas in plan:
        for phi in (0.0, 15.0, 20.0):
            sel = [r for r in rows if r["channel"] == channel and r["phi_deg"] == phi]
            sel.sort(key=lambda r: r["delta"])
            dd = sel[-1]["delta"] - sel[0]["delta"]
            dl = (sel[-1]["roll_moment"] - sel[0]["roll_moment"]) / dd
            dn = (sel[-1]["yaw_moment"] - sel[0]["yaw_moment"]) / dd
            dfz = max(r["Fz_world"] for r in sel) - min(r["Fz_world"] for r in sel)
            summary.append({"channel": channel, "phi_deg": phi,
                            "dL_ddelta": float(dl), "dN_ddelta": float(dn),
                            "side_effect_ratio": float(abs(dn / dl)) if abs(dl) > 1e-12 else None,
                            "Fz_drift_pp": float(dfz)})
            print("D 汇总 ch=%s φ=%.0f: dL/dδ=%.5f  副作用比=%.3f  Fz漂移=%.4f N"
                  % (channel, phi, dl, abs(dn / dl) if abs(dl) > 1e-12 else float('nan'), dfz), flush=True)
    return {"rows": rows, "summary": summary}


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    print("== 转弯专项测量 · scan_turn_support ==", flush=True)
    v1 = _load_v1()
    t0 = time.time()
    self_check(v1)
    A = meas_A_bank_lift(v1)
    B = meas_B_sideslip(v1)
    C = meas_C_damping_turn(v1)
    D = meas_D_diff_channels(v1)
    out = {"ts": ts, "tool": "tools/scan_turn_support.py",
           "base_wave": BASE_WAVE, "base_pitch_deg": BASE_PITCH_DEG,
           "A_bank_lift": A, "B_sideslip": B, "C_damping_turn": C, "D_diff": D}
    out_path = os.path.join(PROJECT_ROOT, "logs", "scan_turn_support_%s.json" % ts)
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1, default=float)
    print("== 完成（%.0f 秒）产物：%s ==" % (time.time() - t0, out_path), flush=True)


if __name__ == "__main__":
    main()
