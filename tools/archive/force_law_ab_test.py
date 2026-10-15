# -*- coding: utf-8 -*-
"""
force_law_ab_test.py — |C_N| 绝对值折叠通道 A/B 鉴别（堵墙定性的裁决实验）

问题（第七十一班§8 遗留）：侧滑-滚转发散梯度（+0.0004 N·m/deg 量级）
的本体已判为拍动×侧滑的非线性整流交叉项；|C_N| 绝对值处理（力律登记
缺陷，FIXLOG 点名病根）是整流的首要嫌疑通道。本实验裁决：梯度是物理
真值还是力律缺陷假象。

设计：
A 组=原版力律（np.abs(C_N)，现行生效）。
B 组=有符号变体（同一条 C_N(α)=3.59·sin(2α+9.1°) 曲线，幅值不取绝对
值，负系数时法向力沿 −nF 方向）。实现=SaneDickinson3DModel 子类复制
_wing_forces_canonical 仅改一行（第十八班 Coupled3DModel 平行版先例）。
注意与第十九班登记的"变体 B"（有符号 C_N 重新拟合系数 −4.04）不同：
本实验不重新拟合，只去掉折叠，用于通道归因而非替代力律。

自检（两道门）：
S1 无折叠区逐位一致：构造叶素攻角恒在正区的静态工况，A/B 输出必须
   逐位相同（证明子类除该行外零改动）。
S2 折叠区占比统计：拍动中 α_eff<−4.55°（C_N 首个负零点，sin(2α+9.1°)
   在 α=−4.55° 过零）的叶素相位占比——该占比即折叠通道的活跃度。

测量：mean=0 与 mean=+19.81 两档全波形（梯度最大两档）的 dTx/dβ
（A/B 各测），β 扫描 ±1/±2/±5/±10° 曲线。

判定：折叠通道贡献份额 = |g_A − g_B| / |g_A|。份额>50% 判"梯度主体
为折叠假象"；<20% 判"梯度主体独立于折叠通道"（物理真值地位升格）；
其间判"混合"。

写权限：主会话亲写；代理只执行。运行：python tools/force_law_ab_test.py
产物：logs/force_law_ab_test_<ts>.json
"""
import os, sys, json, math, datetime
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import importlib.util


def _load_v1():
    path = os.path.join(PROJECT_ROOT, "tools", "unified_search_framework.py")
    spec = importlib.util.spec_from_file_location("usf_v1_abtest", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_signed_model(base_model):
    """构造有符号 C_N 变体模型类（继承 base_model 的类型，复制
    _wing_forces_canonical 仅改 |C_N| 一行）。返回新实例。"""
    from modules.aero.sane_dickinson_3d import SaneDickinson3DModel, ROOT_OFFSET_3D

    class _Signed(SaneDickinson3DModel):
        def _wing_forces_canonical(self, r_centers, wing_state,
                                   v_body_at_hinge, omega_body):
            n = len(r_centers)
            dr = self._dr
            phi = wing_state["phi"]; alpha = wing_state["alpha"]
            phi_dot = wing_state["phi_dot"]; alpha_dot = wing_state["alpha_dot"]
            phi_ddot = wing_state["phi_ddot"]
            sp, cp = np.sin(phi), np.cos(phi)
            n_hat = np.array([sp, -cp, 0.0])
            r_body = np.stack([r_centers * sp, -r_centers * cp,
                               np.zeros_like(r_centers)], axis=1) \
                + ROOT_OFFSET_3D
            v_flap = np.stack([phi_dot * r_centers * cp,
                               phi_dot * r_centers * sp,
                               np.zeros_like(r_centers)], axis=1)
            c0 = np.array([0.0, 0.0, 1.0])
            chord0 = np.cross(c0, n_hat)
            chord0 /= max(np.linalg.norm(chord0), 1e-9)
            m_hat = np.cross(n_hat, chord0)
            chord = np.cos(alpha) * chord0 + np.sin(alpha) * m_hat
            v_om = np.cross(np.asarray(omega_body, dtype=float), r_body)
            v_rel = -(v_body_at_hinge + v_flap + v_om)
            v_rel_mag = np.linalg.norm(v_rel, axis=1)
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
            # ======== A/B 唯一差异行：np.abs(C_N) → C_N ========
            dF_normal_mag = q_dyn * C_N * self._chords * dr
            # ===================================================
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
            return F_wing_body, T_wing_body

    return _Signed()


BASE = {"alpha_set": 32.3169, "mean": 19.8105, "split": 0.176,
        "lag": -0.0674, "amp": 17.73, "freq": 1.80, "speed": 3.95}
BETAS = (1.0, 2.0, 5.0, 10.0)


def wave_grad_and_curve(v1, wave, n=288):
    """全波形 Tx(β) 曲线（±各档）与中心差分梯度；同跑 α_eff 折叠区统计。"""
    v1.set_waveform(wave)
    v1.model.set_wing_command(frequency=wave["freq"])
    T = 1.0 / wave["freq"]
    quat = v1.euler_to_quat(0.0, math.radians(-8.916), 0.0)
    out = {}
    fold_neg = 0; fold_tot = 0
    for tag, sgn in (("p", +1.0), ("m", -1.0)):
        for bd in BETAS:
            psi = sgn * math.radians(bd)
            vel = np.array([wave["speed"] * math.cos(psi),
                            wave["speed"] * math.sin(psi), 0.0])
            Tx = 0.0
            v1.set_waveform(wave)  # β 循环内重设，防差动残留
            for i in range(n):
                t = v1.T0[0] + i * T / n
                _, Tw = v1.wing_forces_body(quat, vel, np.zeros(3), t)
                Tx += float(Tw[0])
            out["%s%g" % (tag, bd)] = Tx / n
    v1.T0[0] += T
    g1 = (out["p1"] - out["m1"]) / 2.0
    g10 = (out["p10"] - out["m10"]) / 20.0
    return {"curve": out, "grad_1deg": g1, "grad_10deg": g10}


def fold_fraction_probe(v1, wave, n=144):
    """逐相位逐叶素 α_eff 负值（C_N<0 折叠区）占比独立探针。"""
    from modules.aero.sane_dickinson_3d import ROOT_OFFSET_3D
    m = v1.model
    v1.set_waveform(wave)
    v1.model.set_wing_command(frequency=wave["freq"])
    T = 1.0 / wave["freq"]
    neg = 0; tot = 0
    zero_cn = math.radians(-4.55)
    for i in range(n):
        t = v1.T0[0] + i * T / n
        ws = m.wing_left.evaluate(t, phase_offset_rad=0.0)
        # 重算 alpha_eff（与 canonical 同式，仅统计用途）
        phi = ws["phi"]; alpha = ws["alpha"]
        sp, cp = np.sin(phi), np.cos(phi)
        n_hat = np.array([sp, -cp, 0.0])
        r_centers = m._r_centers
        v_flap = np.stack([ws["phi_dot"] * r_centers * cp,
                           ws["phi_dot"] * r_centers * sp,
                           np.zeros_like(r_centers)], axis=1)
        r_body = np.stack([r_centers * sp, -r_centers * cp,
                           np.zeros_like(r_centers)], axis=1) + \
            ROOT_OFFSET_3D
        v_rel = -(np.array([wave["speed"], 0.0, 0.0]) + v_flap)
        v_span = (v_rel @ n_hat)[:, None] * n_hat[None, :]
        v_perp = v_rel - v_span
        c0 = np.array([0.0, 0.0, 1.0])
        chord0 = np.cross(c0, n_hat)
        chord0 /= max(np.linalg.norm(chord0), 1e-9)
        m_hat = np.cross(n_hat, chord0)
        chord = np.cos(alpha) * chord0 + np.sin(alpha) * m_hat
        ncross = np.cross(n_hat, chord)
        dot_c = (-v_perp) @ chord
        dot_m = (-v_perp) @ ncross
        alpha_eff = np.arctan2(dot_m, dot_c)
        neg += int(np.sum(alpha_eff < zero_cn))
        tot += len(alpha_eff)
    v1.T0[0] += T
    return neg / max(tot, 1)


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    print("== 力律 |C_N| 折叠通道 A/B 鉴别 ==", flush=True)
    v1 = _load_v1()
    orig_model = v1.model
    signed = make_signed_model(orig_model)

    # S1 自检：无折叠区逐位一致（静态小攻角板翼，α_eff 恒正）
    ws = {"phi": math.radians(0.0), "alpha": math.radians(-45.0),
          "phi_dot": 0.0, "alpha_dot": 0.0, "phi_ddot": 0.0}
    vb = np.array([3.95, 0.0, 0.0])
    FA, TA = orig_model._wing_forces_canonical(orig_model._r_centers.copy(),
                                               ws, vb, np.zeros(3))
    FB, TB = signed._wing_forces_canonical(signed._r_centers.copy(),
                                           ws, vb, np.zeros(3))
    dS1 = float(np.max(np.abs(np.array(FA) - np.array(FB))))
    ok1 = dS1 < 1e-12
    print("S1 无折叠区逐位一致: max|ΔF|=%.3e  判定=%s" % (dS1, "PASS" if ok1 else "FAIL"), flush=True)

    results = {}
    for mtag, mean in (("mean0", 0.0), ("mean_pos", 19.8105)):
        w = dict(BASE); w["mean"] = mean
        # A 组
        v1.model = orig_model
        rA = wave_grad_and_curve(v1, w)
        ffA = fold_fraction_probe(v1, w)
        # B 组
        v1.model = signed
        rB = wave_grad_and_curve(v1, w)
        v1.model = orig_model
        share1 = abs(rA["grad_1deg"] - rB["grad_1deg"]) / max(abs(rA["grad_1deg"]), 1e-12)
        share10 = abs(rA["grad_10deg"] - rB["grad_10deg"]) / max(abs(rA["grad_10deg"]), 1e-12)
        results[mtag] = {"A": rA, "B": rB, "fold_fraction": ffA,
                         "fold_share_grad1": share1, "fold_share_grad10": share10}
        print("%s: A g1=%+.6f  B g1=%+.6f  折叠份额(1°)=%.1f%%  折叠区占比=%.1f%%"
              % (mtag, rA["grad_1deg"], rB["grad_1deg"], 100 * share1, 100 * ffA), flush=True)
        print("%s: A g10=%+.6f  B g10=%+.6f  折叠份额(10°)=%.1f%%"
              % (mtag, rA["grad_10deg"], rB["grad_10deg"], 100 * share10), flush=True)

    gA = results["mean0"]["A"]["grad_1deg"]
    gB = results["mean0"]["B"]["grad_1deg"]
    share = results["mean0"]["fold_share_grad1"]
    if share > 0.5:
        verdict = "MAJORITY_FOLD_ARTIFACT（梯度主体为|C_N|折叠假象）"
    elif share < 0.2:
        verdict = "INDEPENDENT_OF_FOLD（梯度主体独立于折叠通道）"
    else:
        verdict = "MIXED（折叠与真实整流混合贡献）"
    print("== 判定：%s ==" % verdict, flush=True)
    out = {"ts": ts, "tool": "tools/force_law_ab_test.py",
           "S1_parity_maxdF": dS1, "results": results, "verdict": verdict}
    out_path = os.path.join(PROJECT_ROOT, "logs", "force_law_ab_test_%s.json" % ts)
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1, default=float)
    print("产物：%s" % out_path, flush=True)


if __name__ == "__main__":
    main()
