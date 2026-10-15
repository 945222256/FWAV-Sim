# -*- coding: utf-8 -*-
"""
verify_i221_motion_primitives.py — I-221 先行施工本地可验检查（阶段证据）。

先行范围（2026-10-15 机主批准的 partial_start，原 I-220 依赖保留）：
仅核对周期与单次动态运动基元的方程、内部状态、形状权重、初值及导数；
解析／采样／示教参考导入与归一化版本；接收外部相位与规范进度的共同
轨迹采样接口与链式导数装配。本检查不勾选 I-221 整项判据；真实
RhythmService 接入、停复拍／事件／预览副本接续与完整合作配方验收等
待 I-220 修复验收后办理。

时钟来源纪律：本检查的时钟样本全部由明确标记的检查夹具
（source 以 check_fixture: 开头）构造，不含任何真实时钟接通声明；
生产代码（modules/motion_action/primitives.py 与 trajectories.py）对来
源前缀做守卫，非检查夹具来源在先行版本被拒绝。

检查只调用生产入口；数值性质核对用通用数学工具（有限差分、网格比
较），不另写一套基元公式充当验证器。

返修记录（run-beth-i221-partial-rework-20261015-a1）：按 Fil 先行接口
复核三项缺陷追加 R01—R03 检查组；原 F01—F10 四十二项保留不动。返修
运行以第一个命令行参数为输出目录（缺省原目录 logs/motion_action_
i-221），原交付证据文件不被覆盖。

用法：
  python tools/verify_i221_motion_primitives.py [输出目录]
仅使用 Python 标准库、numpy 与本仓纯 Python 模块，不启动 Isaac。
"""
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

RESULTS = []
LOG_DIR = os.path.join(ROOT, "logs", "motion_action_i-221")

FIXTURE_SOURCE = "check_fixture:verify_i221"


# ── 检查夹具（明确标记；不伪称真实时钟） ──
class FixtureClockFactory:
    """检查夹具：按声明的频率／进度序列构造 CanonicalClockSample。

    本夹具只生成时钟样本供基元与提供者消费；它不是节律服务，不接真实
    RhythmService，所有样本 source 均为 check_fixture: 前缀。
    """

    def __init__(self, dt=1.0e-4, source=FIXTURE_SOURCE):
        self.dt = float(dt)
        self.source = str(source)
        if not self.source.startswith("check_fixture:"):
            raise ValueError("检查夹具来源必须以 check_fixture: 开头")

    def constant_freq(self, n_steps, freq_hz, chi0=0.0):
        """恒频 χ 序列（chi_dot 恒等于 freq_hz，chi_ddot=0）。"""
        out = []
        for i in range(1, n_steps + 1):
            out.append(_clock(step=i, t_s=i * self.dt,
                              chi=chi0 + freq_hz * i * self.dt,
                              chi_dot=freq_hz, chi_ddot=0.0,
                              source=self.source))
        return out

    def swept_freq(self, n_steps, f0, f1, chi0=0.0):
        """线性变频 χ 序列（chi_dot 线性变化，chi_ddot 恒定）。"""
        dfdt = (f1 - f0) / (n_steps * self.dt)
        out = []
        chi = chi0
        for i in range(1, n_steps + 1):
            f_i = f0 + dfdt * i * self.dt
            chi = chi0 + (f0 * i * self.dt
                          + 0.5 * dfdt * (i * self.dt) ** 2)
            out.append(_clock(step=i, t_s=i * self.dt, chi=chi, chi_dot=f_i,
                              chi_ddot=dfdt, source=self.source))
        return out

    def linear_progress(self, n_steps, rate=1.0):
        """线性规范进度序列（p 从 0 匀速到 1，progress_dot=rate）。"""
        out = []
        for i in range(1, n_steps + 1):
            p = min(rate * i * self.dt, 1.0)
            out.append(_clock(step=i, t_s=i * self.dt, chi=p, chi_dot=0.0,
                              source=self.source, canonical_progress=p,
                              progress_dot=rate, progress_ddot=0.0))
        return out


def _clock(step, t_s, chi, chi_dot, source, chi_ddot=None,
           canonical_progress=None, progress_dot=None, progress_ddot=None,
           frozen=False):
    from modules.motion_action.primitives import CanonicalClockSample
    return CanonicalClockSample(
        step=step, t_s=t_s, chi=chi, chi_dot=chi_dot, source=source,
        chi_ddot=chi_ddot, canonical_progress=canonical_progress,
        progress_dot=progress_dot, progress_ddot=progress_ddot,
        frozen=frozen)


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), str(detail)))
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                         ("——" + str(detail)) if detail else ""))
    return bool(ok)


def _close(a, b, tol):
    return a is not None and b is not None and abs(a - b) <= tol


def _sin_periodic_primitive(name="check_periodic_sin"):
    """生产入口构造：正弦参考经 fit_reference_periodic 导入的周期基元。"""
    from modules.motion_action.primitives import (ReferenceSampleSet,
                                                  fit_reference_periodic)
    ref = ReferenceSampleSet.from_analytic(
        lambda x: math.sin(2.0 * math.pi * x), "phase", n_points=241)
    prim, norm = fit_reference_periodic(
        ref, center=0.0, scale=1.0, phase_zero=0.0, version="check_v1",
        name=name)
    return prim, norm, ref


def _cubic_discrete_primitive():
    """生产入口构造：三次平滑参考经 fit_reference_discrete 导入的单次基元。"""
    from modules.motion_action.primitives import (ReferenceSampleSet,
                                                  fit_reference_discrete)
    x0, g = 0.1, 0.6
    ref = ReferenceSampleSet.from_analytic(
        lambda p: x0 + (g - x0) * (3.0 * p ** 2 - 2.0 * p ** 3),
        "progress", n_points=121)
    prim, norm = fit_reference_discrete(ref, x0=x0, g=g, version="check_v1")
    return prim, norm, ref


# ══════════════════════════════════════════════════════════════
# F01 周期基元方程与链式导数
# ══════════════════════════════════════════════════════════════
def cmd_f01():
    prim, _norm, _ref = _sin_periodic_primitive("f01_periodic")
    fx = FixtureClockFactory(dt=1.0e-4)

    # F01a 恒频：申报导数（提交时刻点导数）与中心差分一致
    # （前向差分对照点导数自带 O(h) 偏差，采用中心差分为公平对照）
    seq = fx.constant_freq(8, freq_hz=2.0)
    zs, dots, ddots = [], [], []
    for clk in seq:
        s = prim.advance(clk)
        zs.append(s.z)
        dots.append(s.z_dot)
        ddots.append(s.z_ddot)
    ok_dot = True
    ok_ddot = True
    worst_d = worst_a = 0.0
    for i in range(1, len(zs) - 1):
        cd_dot = (zs[i + 1] - zs[i - 1]) / (2 * fx.dt)
        cd_ddot = (dots[i + 1] - dots[i - 1]) / (2 * fx.dt)
        ok_dot = ok_dot and _close(dots[i], cd_dot, 1e-3)
        ok_ddot = ok_ddot and _close(ddots[i], cd_ddot, 1e-2)
        worst_d = max(worst_d, abs(dots[i] - cd_dot))
        worst_a = max(worst_a, abs(ddots[i] - cd_ddot))
    check("F01/恒频一阶导数与中心差分一致", ok_dot,
          "最大偏差 %.3e（容差 1e-3）" % worst_d)
    check("F01/恒频二阶导数与中心差分一致", ok_ddot,
          "z̈=ν̈v+ν̇²G（ν̈=0）；最大偏差 %.3e（容差 1e-2）" % worst_a)

    # F01b 变频：chi_ddot≠0 时链式二阶导数仍与中心差分一致
    prim2, _n2, _r2 = _sin_periodic_primitive("f01b_periodic")
    seq2 = fx.swept_freq(8, f0=2.0, f1=3.0)
    zs2, dots2, ddots2 = [], [], []
    for clk in seq2:
        s = prim2.advance(clk)
        zs2.append(s.z)
        dots2.append(s.z_dot)
        ddots2.append(s.z_ddot)
    ok = True
    for i in range(1, len(zs2) - 1):
        cd_dot = (zs2[i + 1] - zs2[i - 1]) / (2 * fx.dt)
        cd_ddot = (dots2[i + 1] - dots2[i - 1]) / (2 * fx.dt)
        ok = ok and _close(dots2[i], cd_dot, 1e-3) \
            and _close(ddots2[i], cd_ddot, 1e-1)
    check("F01/变频一阶与二阶链式导数一致", ok,
          "χ̇ 由 2→3 Hz 线性扫频，χ̈ 恒定（中心差分对照）")

    # F01c 周期回绕不重置：跨整数 χ 前后状态连续（无跳变）
    prim3, _n3, _r3 = _sin_periodic_primitive("f01c_periodic")
    fine = FixtureClockFactory(dt=1.0e-5)
    seq3 = fine.constant_freq(3, freq_hz=1.0, chi0=0.999999)
    jumps = []
    prev = None
    for clk in seq3:
        s = prim3.advance(clk)
        if prev is not None:
            jumps.append(abs(s.z - prev))
        prev = s.z
    check("F01/周期回绕不重置内部状态", max(jumps) < 1e-2,
          "跨 χ=1 回绕相邻步 z 跳变最大 %.3e" % max(jumps))

    # F01d 周期核周期性：F(θ)=F(θ+2π)
    ok = all(_close(prim.forcing(t), prim.forcing(t + 2.0 * math.pi), 1e-12)
             for t in (0.3, 1.2, 2.7, 5.9))
    check("F01/形状驱动项以 2π 为周期", ok)


# ══════════════════════════════════════════════════════════════
# F02 单一提交与只读纪律
# ══════════════════════════════════════════════════════════════
def cmd_f02():
    from modules.motion_action.primitives import PrimitiveError
    prim, _norm, _ref = _sin_periodic_primitive("f02_periodic")
    fx = FixtureClockFactory(dt=1.0e-4)
    seq = fx.constant_freq(5, freq_hz=2.0)
    prim.advance(seq[0])
    prim.advance(seq[1])

    # F02a 同 step 重复提交拒绝
    try:
        prim.advance(seq[1])
        ok_a = False
    except PrimitiveError:
        ok_a = True
    check("F02/同一物理步重复 advance 拒绝", ok_a)

    # F02b 步号回退拒绝
    try:
        prim.advance(seq[0])
        ok_b = False
    except PrimitiveError:
        ok_b = True
    check("F02/物理步号回退拒绝", ok_b)

    # F02c sample 只读（状态逐字段不变）
    before = prim.state()
    prim.sample()
    check("F02/sample 只读不推进", prim.state() == before)

    # F02d 相位回退拒绝且保留最后有效状态
    st = prim.state()
    back = _clock(step=99, t_s=9.9e-4, chi=st["chi"] - 0.01, chi_dot=2.0,
                  source=FIXTURE_SOURCE)
    try:
        prim.advance(back)
        ok_d = False
    except PrimitiveError:
        ok_d = True
    check("F02/规范相位回退拒绝并保留最后有效状态", ok_d and prim.state() == st)

    # F02e 冻结与零增量状态保持
    st = prim.state()
    frozen_clk = _clock(step=st["step"] + 1, t_s=st["step"] * 1e-4 + 1e-4,
                        chi=st["chi"], chi_dot=2.0, source=FIXTURE_SOURCE,
                        frozen=True)
    s = prim.advance(frozen_clk)
    check("F02/时钟声明冻结时状态保持",
          s.z == st["z"] and s.internal_rate == st["v"] and s.z_dot == 0.0)


# ══════════════════════════════════════════════════════════════
# F03 周期参考导入（解析／采样／示教）与归一化登记
# ══════════════════════════════════════════════════════════════
def _rollout_rms(prim, fx, freq, ref_fn, n_steps=1200):
    import numpy as np
    seq = fx.constant_freq(n_steps, freq_hz=freq)
    errs = []
    for clk in seq:
        s = prim.advance(clk)
        chi_ref = clk.chi % 1.0
        errs.append(s.z - ref_fn(chi_ref))
    return float(np.sqrt(np.mean(np.asarray(errs) ** 2)))


def cmd_f03():
    from modules.motion_action.primitives import ReferenceSampleSet, \
        fit_reference_periodic
    fx = FixtureClockFactory(dt=1.0e-4)
    ref_fn = lambda x: math.sin(2.0 * math.pi * x)  # noqa: E731

    # F03a 解析来源
    prim_a, norm_a, _ = _sin_periodic_primitive("f03a")
    rms_a = _rollout_rms(prim_a, fx, 2.0, ref_fn)
    check("F03/解析参考导入回放误差达标", rms_a < 0.02,
          "回放 RMS=%.3e（阈值 0.02；拟合残差 RMS=%.3e）"
          % (rms_a, norm_a.rollout_rms_shape))

    # F03b 采样来源（从同一正弦取 120 个采样点）
    xs = [i / 120.0 for i in range(120)]
    prim_b, norm_b = fit_reference_periodic(
        ReferenceSampleSet.from_samples(
            [(x, ref_fn(x)) for x in xs], "phase"),
        center=0.0, scale=1.0, phase_zero=0.0, version="check_v1",
        name="f03b")
    rms_b = _rollout_rms(prim_b, fx, 2.0, ref_fn)
    check("F03/采样参考导入回放误差达标", rms_b < 0.02,
          "回放 RMS=%.3e" % rms_b)

    # F03c 示教来源（时间列仅登记，相位由示教标注给出）
    demo = [(0.001 * i, 0.001 * i * 2.0, ref_fn((0.001 * i * 2.0) % 1.0))
            for i in range(0, 500)]
    prim_c, norm_c = fit_reference_periodic(
        ReferenceSampleSet.from_demonstration(demo, "phase"),
        center=0.0, scale=1.0, phase_zero=0.0, version="check_v1",
        name="f03c")
    rms_c = _rollout_rms(prim_c, fx, 2.0, ref_fn)
    check("F03/示教参考导入回放误差达标", rms_c < 0.02,
          "回放 RMS=%.3e" % rms_c)

    # F03d 导入元数据完整（归一化、坐标、版本、方法、误差、初值注记）
    fields = (norm_a.center, norm_a.scale, norm_a.phase_coordinate,
              norm_a.phase_zero, norm_a.version, norm_a.source_kind,
              norm_a.fit_method, norm_a.fit_rms_shape,
              norm_a.rollout_rms_shape, norm_a.n_basis, norm_a.n_samples,
              norm_a.initial_state_note)
    ok = (norm_a.center == 0.0 and norm_a.scale == 1.0
          and norm_a.phase_coordinate == "cumulative_beats"
          and norm_a.version == "check_v1"
          and norm_a.source_kind == "analytic"
          and norm_a.fit_method and norm_a.fit_rms_shape is not None
          and norm_a.rollout_rms_shape is not None
          and norm_a.n_basis >= 3 and norm_a.n_samples >= 8
          and "初值由参考导入" in norm_a.initial_state_note)
    check("F03/参考归一化与拟合元数据完整", ok, "字段=%r" % (fields,))

    # F03e 初值不统一设零：正弦参考在 χ0=0 处 v0≈2π；显式 rest 仍可用
    # （初值须在 fit 后立即读取；rollout 会推进内部状态）
    prim_e, norm_e, _re = _sin_periodic_primitive("f03e_periodic")
    v0 = prim_e.state()["v"]
    check("F03/初值速度由参考导数导入（非统一零）",
          abs(v0 - 2.0 * math.pi) < 0.05, "v0=%.6f（期望≈2π；导入注记=%s）"
          % (v0, norm_e.initial_state_note))
    prim_e.reset(0.0, 0.0, 0.0, note="显式静止初值选择")
    check("F03/显式静止初值仍可用", prim_e.state()["v"] == 0.0)


# ══════════════════════════════════════════════════════════════
# F04 单次基元（外部规范进度驱动）
# ══════════════════════════════════════════════════════════════
def cmd_f04():
    prim, norm, _ = _cubic_discrete_primitive()
    fx = FixtureClockFactory(dt=1.0e-4)
    seq = fx.linear_progress(10001, rate=1.0)
    last = None
    for clk in seq:
        last = prim.advance(clk)
    # F04a 终点收敛（归一化坐标终点为 1）
    check("F04/单次基元收敛到参考终点", abs(last.z - 1.0) < 0.02,
          "终点 z=%.6f（阈值 0.02）completed=%s" % (last.z, prim.completed))

    # F04b f(1)=0 且有权重时 f(0) 一般非零
    f1 = prim.forcing(1.0)
    f0 = prim.forcing(0.0)
    any_w = any(abs(w) > 1e-9 for w in prim.weights)
    check("F04/形状驱动项终点归零", f1 == 0.0 and (not any_w or f0 != 0.0),
          "f(1)=%.3e f(0)=%.3e" % (f1, f0))

    # F04c 一阶导数 ẋ=uṡ 与中心差分一致；s̈ 缺省时二阶 None
    prim2, _n2, _r2 = _cubic_discrete_primitive()
    seq2 = fx.linear_progress(8, rate=1.0)
    zs = []
    for clk in seq2:
        s = prim2.advance(clk)
        zs.append((s.z, s.z_dot))
    ok = True
    for i in range(1, len(zs) - 1):
        cd = (zs[i + 1][0] - zs[i - 1][0]) / (2 * fx.dt)
        ok = ok and _close(zs[i][1], cd, 1e-3)
    check("F04/一阶导数与中心差分一致", ok,
          "ẋ=uṡ（提交时刻点导数；中心差分为公平对照）")
    # s̈ 未提供（None）时 z_ddot 为 None
    prim3, _n3, _r3 = _cubic_discrete_primitive()
    clk_nodd = _clock(step=1, t_s=1e-4, chi=1e-4, chi_dot=0.0,
                      source=FIXTURE_SOURCE, canonical_progress=1e-4,
                      progress_dot=1.0, progress_ddot=None)
    s3 = prim3.advance(clk_nodd)
    check("F04/s̈ 缺省时二阶导数 None 不假零", s3.z_ddot is None,
          "note=%s" % s3.derivative_note)

    # F04d 起终点保存
    check("F04/起终点保存于基元与归一化登记",
          prim.endpoints == (0.1, 0.6) and norm.endpoints == (0.1, 0.6)
          and norm.center == 0.1 and abs(norm.scale - 0.5) < 1e-12)

    # F04e 参考导入回放误差（绝对轨迹对照）
    import numpy as np
    prim4, _n4, ref4 = _cubic_discrete_primitive()
    seq4 = fx.linear_progress(10001, rate=1.0)
    errs = []
    for clk in seq4:
        s = prim4.advance(clk)
        p = clk.canonical_progress
        ref_val = 0.1 + 0.5 * (3 * p ** 2 - 2 * p ** 3)
        errs.append(0.1 + 0.5 * s.z - ref_val)
    rms = float(np.sqrt(np.mean(np.asarray(errs) ** 2)))
    check("F04/单次参考导入回放误差达标", rms < 0.02,
          "绝对轨迹回放 RMS=%.3e（阈值 0.02）" % rms)


# ══════════════════════════════════════════════════════════════
# F05 共同采样接口、装配链式导数与 I-219 契约咬合
# ══════════════════════════════════════════════════════════════
def cmd_f05():
    from modules.motion_action.trajectories import (
        AnalyticProvider, HoldProvider, PrimitiveProvider,
        TransitionProvider, assemble_channel_trajectory,
        build_channel_action, crosscheck_first_order_with_i219,
        make_spring_transition)
    fx = FixtureClockFactory(dt=1e-4)
    prim, _norm, _ref = _sin_periodic_primitive("f05_periodic")
    providers = [
        AnalyticProvider("f05_analytic", "sin_phase"),
        PrimitiveProvider(prim),
        HoldProvider("f05_hold", 0.05),
        make_spring_transition("f05_trans", 0.0, 0.3),
    ]
    # F05a 四类提供者同一采样接口
    ok = True
    for prov in providers:
        clk = _clock(step=1, t_s=1e-4, chi=2e-4, chi_dot=2.0,
                     source=FIXTURE_SOURCE, canonical_progress=2e-4,
                     progress_dot=2.0)
        prov.advance(clk)
        s = prov.sample()
        ok = ok and hasattr(s, "z") and hasattr(s, "z_dot") \
            and hasattr(s, "z_ddot") and hasattr(s, "presence") \
            and hasattr(prov, "state") and hasattr(prov, "clone_for_preview")
    check("F05/解析、基元、保持、过渡提供者同一采样接口", ok,
          "kinds=%s" % [p.kind for p in providers])

    # F05b 时变中心与尺度的链式导数与有限差分一致
    prov = PrimitiveProvider(_sin_periodic_primitive("f05b_periodic")[0])
    center0, scale0, ctrl = 0.2, 0.4, 0.01
    d_center, d_scale = 0.05, -0.1
    dd_center, dd_scale = 0.0, 0.02
    freq = 2.0

    def q_of_t(t):
        clk = _clock(step=int(round(t / fx.dt)), t_s=t, chi=freq * t,
                     chi_dot=freq, chi_ddot=0.0, source=FIXTURE_SOURCE)
        prov.advance(clk)
        s = prov.sample()
        c = center0 + d_center * t
        a = scale0 + d_scale * t
        ac = dd_center * t
        aacc = dd_scale * t
        out = assemble_channel_trajectory(
            s, center=c, scale=a, control=ctrl + 0.0 * t,
            d_center=d_center, d_scale=d_scale,
            dd_center=dd_center, dd_scale=dd_scale)
        return out

    t0 = 0.0
    h = 1.0e-4
    out_prev = q_of_t(t0)
    out_mid = q_of_t(t0 + h)
    out_next = q_of_t(t0 + 2 * h)
    fd_v = (out_mid.value - out_prev.value) / h
    fd_a = (out_next.value - 2 * out_mid.value + out_prev.value) / h ** 2
    check("F05/时变中心与尺度的一阶链式导数与差分一致",
          _close(out_mid.velocity, fd_v, 1e-2),
          "装配 %.6e 差分 %.6e" % (out_mid.velocity, fd_v))
    check("F05/二阶链式导数与差分一致",
          _close(out_mid.acceleration, fd_a, 1e-1),
          "装配 %.6e 差分 %.6e（容差 1e-1）" % (out_mid.acceleration, fd_a))

    # F05c 一阶分支与 I-219 output_with_derivative 同式一致
    from modules.motion_action.trajectories import ShapeSample
    cases_ok = True
    for center, scale, zval, zdot, limits in (
            (0.2, 0.4, 0.3, 0.5, None),
            (0.0, 1.0, 2.5, 0.1, (-1.4, 1.4)),      # 饱和
            (0.0, 1.0, 1.4, 0.1, (-1.4, 1.4))):     # 边界
        sh = ShapeSample(step=1, z=zval, z_dot=zdot, z_ddot=0.0,
                         presence="value", source="check",
                         derivative_note="")
        mine = assemble_channel_trajectory(
            sh, center=center, scale=scale, limits=limits)
        ref = crosscheck_first_order_with_i219(
            sh, center=center, scale=scale, limits=limits)
        cases_ok = cases_ok and _close(mine.value, ref[0], 1e-12) \
            and ((mine.velocity is None and ref[1] is None)
                 or _close(mine.velocity, ref[1], 1e-12))
    check("F05/一阶装配与 I-219 同一输出规则一致", cases_ok,
          "未限幅、饱和、边界三情形核对（饱和区导数为真实零；边界不可用）")

    # F05d 缺项级联（不填假零）
    sh_no1 = ShapeSample(step=1, z=0.3, z_dot=None, z_ddot=0.0,
                         presence="value", source="check",
                         derivative_note="一阶不可用")
    out_no1 = assemble_channel_trajectory(sh_no1, 0.0, 1.0)
    sh_no2 = ShapeSample(step=1, z=0.3, z_dot=0.5, z_ddot=None,
                         presence="value", source="check",
                         derivative_note="二阶不可用")
    out_no2 = assemble_channel_trajectory(sh_no2, 0.0, 1.0)
    sh_miss = ShapeSample(step=1, z=None, z_dot=None, z_ddot=None,
                          presence="missing", source="check",
                          derivative_note="缺失")
    out_miss = assemble_channel_trajectory(sh_miss, 0.0, 1.0)
    check("F05/缺项级联不填假零",
          out_no1.velocity is None and out_no1.acceleration is None
          and out_no2.velocity is not None and out_no2.acceleration is None
          and out_miss.value is None and out_miss.velocity is None)

    # F05e 限幅边界导数不可用；饱和区导数为真实零
    sh_sat = ShapeSample(step=1, z=2.5, z_dot=1.0, z_ddot=0.0,
                         presence="value", source="check", derivative_note="")
    out_sat = assemble_channel_trajectory(sh_sat, 0.0, 1.0, limits=(-1.4, 1.4))
    sh_edge = ShapeSample(step=1, z=1.4, z_dot=1.0, z_ddot=0.0,
                          presence="value", source="check", derivative_note="")
    out_edge = assemble_channel_trajectory(sh_edge, 0.0, 1.0,
                                           limits=(-1.4, 1.4))
    check("F05/饱和区真实零导数与边界不可用",
          out_sat.value == 1.4 and out_sat.velocity == 0.0
          and out_sat.acceleration == 0.0 and out_edge.value == 1.4
          and out_edge.velocity is None)

    # F05f ChannelAction 量名身份（I-219 契约咬合）
    ca_f = build_channel_action(
        "flap_L", out_no2, "primitive:f05")
    ca_t = build_channel_action(
        "twist_L", out_no1, "analytic:f05")
    ca_h = build_channel_action(
        "tail_yaw", out_sat, "hold:f05")
    check("F05/通道动作导数身份按 I-219 需求表分列",
          ca_f.derivative_statuses.get("flap_angular_velocity") == "available"
          and ca_f.derivative_statuses.get("flap_angular_acceleration")
          == "missing"
          and ca_t.derivative_statuses.get("twist_angular_velocity")
          == "missing"
          and not ca_t.derivative_available
          and ca_h.derivative_statuses.get("derivatives") == "not_required")


# ══════════════════════════════════════════════════════════════
# F06 解析提供者与旧解析资产同式
# ══════════════════════════════════════════════════════════════
def cmd_f06():
    from modules.aero.kinematics import split_phase_map
    from modules.motion_action.trajectories import AnalyticProvider
    fx = FixtureClockFactory(dt=1e-4)
    chi, cd = 0.37, 2.5

    def run_one(prov):
        clk = _clock(step=1, t_s=chi / cd, chi=chi, chi_dot=cd, chi_ddot=0.0,
                     source=FIXTURE_SOURCE)
        prov.advance(clk)
        return prov.sample()

    # F06a sin/cos 与手工公式一致
    ap = AnalyticProvider("f06_sin", "sin_phase")
    s = run_one(ap)
    tp = 2.0 * math.pi
    ok = _close(s.z, math.sin(tp * chi), 1e-12) \
        and _close(s.z_dot, tp * cd * math.cos(tp * chi), 1e-9) \
        and _close(s.z_ddot, -tp ** 2 * cd ** 2 * math.sin(tp * chi), 1e-6)
    ac = AnalyticProvider("f06_cos", "cos_phase")
    sc = run_one(ac)
    ok = ok and _close(sc.z, math.cos(tp * chi), 1e-12) \
        and _close(sc.z_dot, -tp * cd * math.sin(tp * chi), 1e-9)
    check("F06/sin 与 cos 解析形状及其导数同式", ok)

    # F06b split_sin_phase 与旧分裂周期核同式
    asp = AnalyticProvider("f06_split", "split_sin_phase", split=0.176)
    ss = run_one(asp)
    ph, dph = split_phase_map(chi % 1.0, 0.176)
    ok = _close(ss.z, math.sin(ph), 1e-12) \
        and _close(ss.z_dot, math.cos(ph) * dph * cd, 1e-9)
    check("F06/split 形状与旧分裂周期核公共出口同式", ok,
          "复用 modules/aero/kinematics.py 的 split_phase_map（只读）")

    # F06c aero_pitch 与旧俯仰程序公式一致；尖点与二阶如实登记
    alpha_set = 0.5641
    aap = AnalyticProvider("f06_aero", "aero_pitch", split=0.176,
                           alpha_set_rad=alpha_set, pitch_bias_rad=0.0,
                           lag_beats=0.0)
    sa = run_one(aap)
    ph2, dph2 = split_phase_map(chi % 1.0, 0.176)
    expect_z = (1.0 if math.cos(ph2) >= 0 else -1.0) * alpha_set \
        * abs(math.cos(ph2))
    ok = _close(sa.z, expect_z, 1e-12) \
        and _close(sa.z_dot, alpha_set * (-math.sin(ph2)) * dph2 * cd, 1e-9) \
        and sa.z_ddot is None
    check("F06/aero 俯仰形状与旧公式同式（二阶不提供）", ok)
    # 尖点：split=0.176 时 τ=s/2=0.088 对应 ph=π/2（cos(ph)=0 分段边界）
    chi_cusp = 0.176 / 2.0
    clk_cusp = _clock(step=1, t_s=0.0, chi=chi_cusp, chi_dot=cd,
                      chi_ddot=0.0, source=FIXTURE_SOURCE)
    aap2 = AnalyticProvider("f06_aero_cusp", "aero_pitch", split=0.176,
                            alpha_set_rad=alpha_set)
    aap2.advance(clk_cusp)
    scusp = aap2.sample()
    check("F06/aero 俯仰分段边界导数不可用", scusp.z_dot is None,
          "note=%s" % scusp.derivative_note)

    # F06d fold 偏移 2.2 rad 固定，改写拒绝
    try:
        AnalyticProvider("f06_fold_bad", "fold_sin_phase",
                         phase_offset_rad=1.1)
        ok_d = False
    except Exception:
        ok_d = True
    afp = AnalyticProvider("f06_fold", "fold_sin_phase",
                           phase_offset_rad=2.2,
                           phase_offset_source="已批准首配方保留原 2.2 rad 偏移")
    sf = run_one(afp)
    ok_d = ok_d and _close(sf.z, math.sin(tp * chi + 2.2), 1e-12)
    check("F06/折翼 2.2 rad 偏移唯一持有且固定", ok_d)


# ══════════════════════════════════════════════════════════════
# F07 克隆隔离与复位
# ══════════════════════════════════════════════════════════════
def cmd_f07():
    from modules.motion_action.trajectories import (HoldProvider,
                                                    PrimitiveProvider,
                                                    TransitionProvider,
                                                    make_spring_transition)
    fx = FixtureClockFactory(dt=1e-4)

    # F07a 周期基元克隆：副本接续推进不影响原件
    prim, _n, _r = _sin_periodic_primitive("f07_periodic")
    prov = PrimitiveProvider(prim)
    for clk in fx.constant_freq(5, 2.0):
        prov.advance(clk)
    st_before = prov.state()
    z_before = prov.sample().z
    twin = prov.clone_for_preview()
    chi_off = 2.0 * 5 * fx.dt          # 原件已提交相位
    for clk in fx.constant_freq(5, 2.0):
        clk2 = _clock(step=clk.step + 5, t_s=clk.t_s + 5e-4,
                      chi=chi_off + clk.chi, chi_dot=clk.chi_dot,
                      chi_ddot=0.0, source=FIXTURE_SOURCE)
        twin.advance(clk2)
    check("F07/周期基元预览克隆推进不影响原件",
          prov.state() == st_before
          and _close(prov.sample().z, z_before, 0.0)
          and twin.state()["step"] > st_before["step"])

    # F07b 过渡与保持提供者克隆：副本推进后原件读数不变
    tp = make_spring_transition("f07_trans", 0.0, 0.3)
    hp = HoldProvider("f07_hold", 0.05)
    clk = _clock(step=1, t_s=1e-4, chi=1e-4, chi_dot=0.0,
                 source=FIXTURE_SOURCE, canonical_progress=1e-4,
                 progress_dot=1.0)
    tp.advance(clk)
    hp.advance(clk)
    z_tp_before = tp.sample().z
    tp2 = tp.clone_for_preview()
    hp2 = hp.clone_for_preview()
    clk2 = _clock(step=2, t_s=2e-4, chi=2e-4, chi_dot=0.0,
                  source=FIXTURE_SOURCE, canonical_progress=2e-4,
                  progress_dot=1.0)
    tp2.advance(clk2)
    hp2.advance(clk2)
    check("F07/过渡与保持提供者克隆隔离",
          tp.state()["step"] == 1 and hp.state()["step"] == 1
          and tp2.state()["step"] == 2 and hp2.state()["step"] == 2
          and _close(tp.sample().z, z_tp_before, 0.0)
          and hp.sample().z == 0.05)

    # F07c reset 重新开始（重置语义）
    prim_r, _nr, _rr = _sin_periodic_primitive("f07c_periodic")
    for clk in fx.constant_freq(3, 2.0):
        prim_r.advance(clk)
    st1 = prim_r.state()
    prim_r.reset(st1["z"], st1["v"], st1["chi"], note="复位重放")
    after = prim_r.state()
    check("F07/reset 后回到未提交状态",
          after["step"] == -1 and after["advance_count"] == 0
          and _close(after["z"], st1["z"], 0.0))


# ══════════════════════════════════════════════════════════════
# F08 时钟来源守卫（检查夹具标记；不伪称真实时钟）
# ══════════════════════════════════════════════════════════════
def cmd_f08():
    from modules.motion_action.primitives import PrimitiveError
    prim, _n, _r = _sin_periodic_primitive("f08_periodic")
    for src in ("rhythm_service", "production", "", "check_fixture"):
        try:
            prim.advance(_clock(step=1, t_s=1e-4, chi=2e-4, chi_dot=2.0,
                                source=src))
            ok = False
            break
        except PrimitiveError:
            ok = True
    check("F08/未登记时钟标签拒绝", ok,
          "缺冒号或未登记标签拒绝；真实服务适配另由合作集成检查覆盖")
    prim.advance(_clock(step=1, t_s=1e-4, chi=2e-4, chi_dot=2.0,
                        source=FIXTURE_SOURCE))
    check("F08/检查夹具来源正常放行", prim.state()["step"] == 1)


# ══════════════════════════════════════════════════════════════
# F09 耦合入口（单位与控制权声明）
# ══════════════════════════════════════════════════════════════
def cmd_f09():
    from modules.motion_action.primitives import (COUPLING_UNIT_PERIODIC,
                                                  CouplingInput,
                                                  PrimitiveError)
    fx = FixtureClockFactory(dt=1e-3)
    base = [_clock(step=1, t_s=1e-3, chi=2e-3, chi_dot=2.0,
                   source=FIXTURE_SOURCE)]
    prim_a, _na, _ra = _sin_periodic_primitive("f09_a")
    prim_b, _nb, _rb = _sin_periodic_primitive("f09_b")
    for prim in (prim_a, prim_b):
        prim.advance(base[0])
    seq = fx.constant_freq(30, 2.0)
    seq = [_clock(step=s.step + 1, t_s=s.t_s + 1e-3, chi=s.chi,
                  chi_dot=s.chi_dot, chi_ddot=0.0, source=FIXTURE_SOURCE)
           for s in seq]
    coup = CouplingInput(value=0.5, unit=COUPLING_UNIT_PERIODIC,
                         owner="phase_lock_check", outlet="d_v_coupling",
                         limit_note="检查用耦合")
    for clk in seq:
        prim_a.advance(clk)
        prim_b.advance(clk, coupling=coup)
    check("F09/耦合输入改变演化（单位与控制权声明后生效）",
          abs(prim_a.state()["z"] - prim_b.state()["z"]) > 1e-9)
    try:
        prim_a.advance(_clock(step=99, t_s=0.1, chi=0.1, chi_dot=2.0,
                              source=FIXTURE_SOURCE),
                       coupling=CouplingInput(
                           value=0.1, unit="wrong_unit", owner="x",
                           outlet="y"))
        ok_u = False
    except PrimitiveError:
        ok_u = True
    try:
        prim_a.advance(_clock(step=100, t_s=0.11, chi=0.11, chi_dot=2.0,
                              source=FIXTURE_SOURCE),
                       coupling=CouplingInput(
                           value=float("nan"), unit=COUPLING_UNIT_PERIODIC,
                           owner="x", outlet="y"))
        ok_n = False
    except PrimitiveError:
        ok_n = True
    check("F09/耦合单位不符与非有限值拒绝", ok_u and ok_n)


# ══════════════════════════════════════════════════════════════
# F10 两跑一致性（确定性）
# ══════════════════════════════════════════════════════════════
def cmd_f10():
    def run_once():
        prim, _norm, _ref = _sin_periodic_primitive("f10_periodic")
        dprim, _dn, _dr = _cubic_discrete_primitive()
        fx = FixtureClockFactory(dt=1e-4)
        vals = []
        for clk in fx.constant_freq(200, 2.0):
            vals.append(prim.advance(clk).z)
        for clk in fx.linear_progress(300, 1.0):
            vals.append(dprim.advance(clk).z)
        return vals

    r1 = run_once()
    r2 = run_once()
    same = len(r1) == len(r2) and all(a == b for a, b in zip(r1, r2))
    check("F10/完整序列两跑逐位一致", same, "共 %d 个提交点" % len(r1))


# ══════════════════════════════════════════════════════════════
# R01 返修检查：非对称 split 接缝导数身份（Fil F01）
# ══════════════════════════════════════════════════════════════
def cmd_r01():
    from modules.motion_action.trajectories import (
        AnalyticProvider, assemble_channel_trajectory, build_channel_action)

    def split_probe(chi, split):
        prov = AnalyticProvider("r01_split", "split_sin_phase", split=split)
        return prov.advance(_clock(step=1, t_s=chi / 2.0, chi=chi, chi_dot=2.0,
                                   chi_ddot=0.0, source=FIXTURE_SOURCE))

    # R01a 非对称 split=0.75：分裂接缝与整数拍接缝处一阶二阶均判事件
    # 边界不可用（两侧单边差分确实不等，见 R01e）
    ok_a = True
    for chi in (0.75, 1.0):
        s = split_probe(chi, 0.75)
        ok_a = ok_a and s.z_dot is None and s.z_ddot is None \
            and s.first_order_status == "event_boundary_unavailable" \
            and s.second_order_status == "event_boundary_unavailable"
    check("R01/非对称 split 接缝一阶二阶判事件边界不可用", ok_a,
          "χ=0.75 与 χ=1.0 两处接缝（Fil 反例相位）")

    # R01b 分段内部导数解析可用
    s_in = split_probe(0.2, 0.75)
    check("R01/分段内部导数保持解析可用",
          s_in.z_dot is not None and s_in.z_ddot is not None
          and s_in.first_order_status == "available"
          and s_in.second_order_status == "available")

    # R01c 对称 split=0.5：两侧速率相等、导数按实际连续性判可用（不机
    # 械把所有拍边判无效）
    ok_c = True
    for chi in (0.5, 1.0):
        s = split_probe(chi, 0.5)
        ok_c = ok_c and s.z_dot is not None \
            and s.first_order_status == "available"
    check("R01/对称 split 接缝按实际连续性判可用", ok_c,
          "π/s 与 π/(1-s) 相等时导数连续")

    # R01d 身份与原因经装配保留到通道动作（Fil F01 通过条件）
    s_seam = split_probe(0.75, 0.75)
    out = assemble_channel_trajectory(s_seam, 0.0, 1.0)
    act = build_channel_action("flap_L", out, "r01:seam")
    check("R01/接缝不可用身份与原因经装配保留到通道动作",
          act.derivative_statuses.get("flap_angular_velocity")
          == "event_boundary_unavailable"
          and act.derivative_statuses.get("flap_angular_acceleration")
          == "event_boundary_unavailable"
          and not act.derivative_available
          and "接缝" in act.derivative_note
          and act.velocity is None and act.acceleration is None)

    # R01e 复现 Fil 单边差分：接缝两侧导数确实不等（证明判不可用正确）
    eps = 1.0e-7
    ok_e = True
    for chi in (0.75, 1.0):
        center = split_probe(chi, 0.75)
        left = split_probe(chi - eps, 0.75)
        right = split_probe(chi + eps, 0.75)
        d_left = (center.z - left.z) / (eps / 2.0)
        d_right = (right.z - center.z) / (eps / 2.0)
        ok_e = ok_e and abs(d_left - d_right) > 1.0
    check("R01/接缝两侧单边差分确实不等（Fil 反例复现）", ok_e,
          " Fil 独立结果中两侧分别约为 -8.38 与 -25.13 或 +25.13 与 +8.38")


# ══════════════════════════════════════════════════════════════
# R02 返修检查：单次终点后进度预算不重复（Fil F02）
# ══════════════════════════════════════════════════════════════
def cmd_r02():
    from modules.motion_action.primitives import DiscretePrimitive

    def make():
        prim = DiscretePrimitive("r02_post_endpoint", 4.0, 1.0, 1.0,
                                 [0.5], [1.0], weights=[0.0],
                                 endpoints=(0.0, 1.0))
        prim.reset(0.0, 0.0, 0.0, note="闭式对照静止起点")
        return prim

    def clk(step, p):
        return _clock(step=step, t_s=p, chi=p, chi_dot=0.0,
                      source=FIXTURE_SOURCE, canonical_progress=p,
                      progress_dot=1.0, progress_ddot=0.0)

    def exact(p):
        return 1.0 - (1.0 + 2.0 * p) * math.exp(-2.0 * p)

    # R02a 连续 1.0/1.1/1.2 对照闭式（Fil 反例序列；预算 1.0、0.1、0.1）
    prim = make()
    worst = 0.0
    for step, p in enumerate((1.0, 1.1, 1.2), 1):
        s = prim.advance(clk(step, p))
        worst = max(worst, abs(s.z - exact(p)))
    check("R02/终点后连续三步对照闭式不重复预算", worst < 0.006,
          "最大误差 %.3e（Fil 阈值 0.006；修复前 0.0409）" % worst)

    # R02b 分步与直达都落在闭式附近：修复前分步序列会演化到等效预算
    # 1.3 的状态（0.7324）；分步与直达的 RK2 子步划分不同，离散路径不
    # 要求逐位一致，要求两者都对照闭式且互相接近（预算不重复的判据）
    prim_step = make()
    for step, p in enumerate((1.0, 1.1, 1.2), 1):
        s_step = prim_step.advance(clk(step, p))
    prim_direct = make()
    s_direct = prim_direct.advance(clk(1, 1.2))
    check("R02/分步与直达都落在闭式附近且互相接近",
          abs(s_step.z - exact(1.2)) < 0.006
          and abs(s_direct.z - exact(1.2)) < 0.006
          and abs(s_step.z - s_direct.z) < 0.01,
          "分步 %.9f 直达 %.9f 闭式 %.9f" % (s_step.z, s_direct.z,
                                              exact(1.2)))

    # R02c 外部进度与驱动进度分离：外部 1.2、驱动截在 1
    st = prim.state()
    check("R02/外部规范进度与驱动截断进度分离",
          _close(st["p_ext"], 1.2, 1e-12) and st["p_drive"] == 1.0
          and prim.completed)

    # R02d 终点前、恰到终点、终点后：均匀细步长上物理时间导数与进度坐
    # 标中心差分一致（粗区间中心差分自带 O(h²) 截断，用 0.01 步长）
    prim_d = make()
    ps = [0.96 + 0.01 * i for i in range(9)]     # 0.96 … 1.04 跨终点
    zs, dots = [], []
    for step, p in enumerate(ps, 1):
        s = prim_d.advance(clk(step, p))
        zs.append(s.z)
        dots.append(s.z_dot)
    ok = True
    worst = 0.0
    for i in range(1, len(zs) - 1):
        cd = (zs[i + 1] - zs[i - 1]) / (2.0 * 0.01)
        worst = max(worst, abs(dots[i] - cd))
        ok = ok and _close(dots[i], cd, 5e-3)
    check("R02/终点前后物理时间导数与实际增量一致", ok,
          "ẋ=uṡ 以 0.01 进度步中心差分对照；最大偏差 %.3e" % worst)


# ══════════════════════════════════════════════════════════════
# R03 返修检查：明确零值与未支持状态身份传播（Fil F03）
# ══════════════════════════════════════════════════════════════
def cmd_r03():
    from modules.motion_action.trajectories import (
        HoldProvider, ShapeSample, assemble_channel_trajectory,
        build_channel_action)

    # R03a 四状态经形状、装配、通道动作完整调用链逐级保留
    ok_a = True
    for presence in ("zero", "unsupported", "missing", "value"):
        present = presence in ("zero", "value")
        shape = ShapeSample(
            step=1, z=0.0 if present else None,
            z_dot=0.0 if present else None,
            z_ddot=0.0 if present else None, presence=presence,
            source="check_fixture:presence",
            derivative_note="explicit presence test")
        output = assemble_channel_trajectory(shape, 0.0, 1.0)
        action = build_channel_action("tail_L", output, "r03:presence")
        ok_a = ok_a and action.presence == presence \
            and output.presence == presence \
            and output.shape_presence == presence \
            and ((action.value == 0.0) if present
                 else action.value is None)
    check("R03/四状态经形状装配通道动作逐级保留身份", ok_a,
          "zero、unsupported、missing、value 四链路（Fil 反例构造）")

    # R03b 明确零形状按同一装配公式合成（续修一更正预期：非零最终目标
    # 按普通数值身份输出，输入零身份保留在 shape_presence；此前要求非
    # 零目标继续带 zero 身份的预期与契约相反，已更正）
    shape_z = ShapeSample(step=1, z=0.0, z_dot=0.0, z_ddot=0.0,
                          presence="zero", source="check_fixture:zero",
                          derivative_note="明确零形状")
    out_z = assemble_channel_trajectory(shape_z, center=0.2, scale=0.5,
                                        control=0.1)
    act_z = build_channel_action("flap_L", out_z, "r03:zero_compose")
    check("R03/明确零形状与中心尺度控制修合成最终目标",
          _close(out_z.value, 0.3, 1e-12) and act_z.presence == "value"
          and out_z.shape_presence == "zero"
          and _close(act_z.value, 0.3, 1e-12),
          "center=0.2、scale=0.5、control=0.1 → 目标 0.3 为普通数值身份，"
          "输入零身份保留在 shape_presence")

    # R03c 普通数值零与既有保持提供者零不回归
    shape_v0 = ShapeSample(step=1, z=0.0, z_dot=0.0, z_ddot=0.0,
                           presence="value", source="check_fixture:value",
                           derivative_note="普通数值零")
    out_v0 = assemble_channel_trajectory(shape_v0, 0.0, 1.0)
    act_v0 = build_channel_action("tail_L", out_v0, "r03:value_zero")
    holder = HoldProvider("r03_hold", 0.0)
    hold_s = holder.advance(_clock(step=1, t_s=0.0, chi=0.0, chi_dot=2.0,
                                   source=FIXTURE_SOURCE))
    hold_out = assemble_channel_trajectory(hold_s, 0.0, 1.0)
    hold_act = build_channel_action("tail_L", hold_out, "r03:hold")
    check("R03/普通数值零与保持提供者零行为不回归",
          act_v0.presence == "value" and act_v0.value == 0.0
          and hold_act.presence == "value" and hold_act.value == 0.0
          and hold_s.z_dot == 0.0,
          "数值零仍为 value 身份；保持零导数为真实零（Fil 正向对照）")

    # R03d 未支持与缺失分别保留，不互相合并
    shape_u = ShapeSample(step=1, z=None, z_dot=None, z_ddot=None,
                          presence="unsupported", source="check_fixture:u",
                          derivative_note="未支持")
    out_u = assemble_channel_trajectory(shape_u, 0.0, 1.0)
    act_u = build_channel_action("flap_L", out_u, "r03:unsupported")
    check("R03/未支持与缺失身份不合并",
          out_u.presence == "unsupported" and act_u.presence == "unsupported"
          and act_u.value is None and act_u.velocity is None
          and "未支持" in act_u.derivative_note)


# ══════════════════════════════════════════════════════════════
# S01 续修检查：合成后最终目标的身份（Fil 返修复核缺口一）
# ══════════════════════════════════════════════════════════════
def cmd_s01():
    from modules.motion_action.trajectories import (
        ShapeSample, assemble_channel_trajectory, build_channel_action)

    zero_shape = ShapeSample(step=1, z=0.0, z_dot=0.0, z_ddot=0.0,
                             presence="zero", source="check_fixture:s01",
                             derivative_note="明确零形状")

    def chain(shape, center, control, limits=None):
        out = assemble_channel_trajectory(shape, center=center, scale=0.5,
                                          control=control, limits=limits)
        act = build_channel_action("flap_L", out, "s01:composed")
        return out, act

    # S01a 零中心零控制：全零合成，最终目标保留明确零身份
    out_a, act_a = chain(zero_shape, 0.0, 0.0)
    check("S01/全零合成目标保留明确零身份",
          out_a.value == 0.0 and out_a.presence == "zero"
          and act_a.presence == "zero"
          and out_a.shape_presence == "zero")

    # S01b 非零中心加控制修正：目标 0.3，最终身份为普通数值，输入零身
    # 份保留在 shape_presence，数值不清零
    out_b, act_b = chain(zero_shape, 0.2, 0.1)
    check("S01/非零合成目标按普通数值身份输出",
          _close(out_b.value, 0.3, 1e-12) and out_b.presence == "value"
          and act_b.presence == "value" and _close(act_b.value, 0.3, 1e-12)
          and out_b.shape_presence == "zero",
          "center=0.2、scale=0.5、control=0.1 → 0.3（Fil 反例数值）")

    # S01c 饱和后非零：限幅 [-0.1,0.1] 后目标 0.1，身份仍为普通数值
    out_c, act_c = chain(zero_shape, 0.2, 0.1, limits=(-0.1, 0.1))
    check("S01/饱和后非零目标按普通数值身份输出",
          _close(out_c.value, 0.1, 1e-12) and out_c.presence == "value"
          and act_c.presence == "value"
          and _close(act_c.value, 0.1, 1e-12)
          and out_c.shape_presence == "zero")

    # S01d 普通数值零形状的全零合成不作明确零身份（既有契约保持）
    value_zero = ShapeSample(step=1, z=0.0, z_dot=0.0, z_ddot=0.0,
                             presence="value", source="check_fixture:s01",
                             derivative_note="普通数值零")
    out_d, act_d = chain(value_zero, 0.0, 0.0)
    check("S01/普通数值零全零合成不作明确零身份",
          out_d.value == 0.0 and out_d.presence == "value"
          and act_d.presence == "value")


# ══════════════════════════════════════════════════════════════
# S02 续修检查：有数值的声明近似导数身份传播（Fil 返修复核缺口二）
# ══════════════════════════════════════════════════════════════
def cmd_s02():
    from modules.motion_action.trajectories import (
        ShapeSample, assemble_channel_trajectory, build_channel_action)
    from modules.motion_action.types import DERIVATIVE_STATUS_DECLARED_APPROX

    approx = ShapeSample(
        step=1, z=0.2, z_dot=1.0, z_ddot=2.0, presence="value",
        source="check_fixture:s02", derivative_note="声明近似且带数值导数",
        first_order_status=DERIVATIVE_STATUS_DECLARED_APPROX,
        second_order_status=DERIVATIVE_STATUS_DECLARED_APPROX)
    out = assemble_channel_trajectory(approx, center=0.0, scale=0.5)
    act = build_channel_action("flap_L", out, "s02:approx")

    # S02a 数值正确（0.5 与 1.0），身份保留声明近似，不升级为普通可用
    check("S02/有数值的声明近似导数保留身份不升级",
          _close(out.velocity, 0.5, 1e-12)
          and _close(out.acceleration, 1.0, 1e-12)
          and out.velocity_status == DERIVATIVE_STATUS_DECLARED_APPROX
          and out.acceleration_status == DERIVATIVE_STATUS_DECLARED_APPROX
          and act.derivative_statuses.get("flap_angular_velocity")
          == DERIVATIVE_STATUS_DECLARED_APPROX
          and act.derivative_statuses.get("flap_angular_acceleration")
          == DERIVATIVE_STATUS_DECLARED_APPROX,
          "尺度 0.5：一阶 1.0→0.5、二阶 2.0→1.0（Fil 反例数值）")

    # S02b 普通精确导数正向对照保持 available
    exact = ShapeSample(
        step=1, z=0.2, z_dot=1.0, z_ddot=2.0, presence="value",
        source="check_fixture:s02", derivative_note="普通精确导数",
        first_order_status="available", second_order_status="available")
    out_e = assemble_channel_trajectory(exact, center=0.0, scale=0.5)
    act_e = build_channel_action("flap_L", out_e, "s02:exact")
    check("S02/普通精确导数正向对照保持",
          out_e.velocity_status == "available"
          and out_e.acceleration_status == "available"
          and act_e.derivative_statuses.get("flap_angular_velocity")
          == "available"
          and act_e.derivative_statuses.get("flap_angular_acceleration")
          == "available" and act_e.derivative_available)

    # S02c 声明近似且导数可用时求力必需量满足（derivative_available 真）
    check("S02/声明近似导数满足求力必需量判定",
          act.derivative_available is True)

    # S02d 事件边界与真实零行为保持（接缝判不可用、保持常值零为真实导
    # 数身份 available——R01 与 R03c 已覆盖，此处经装配出口再核对一遍）
    from modules.motion_action.trajectories import AnalyticProvider
    prov = AnalyticProvider("s02_split", "split_sin_phase", split=0.75)
    seam = prov.advance(_clock(step=1, t_s=0.375, chi=0.75, chi_dot=2.0,
                               chi_ddot=0.0, source=FIXTURE_SOURCE))
    out_s = assemble_channel_trajectory(seam, 0.0, 1.0)
    act_s = build_channel_action("flap_L", out_s, "s02:seam")
    check("S02/事件边界与真实零行为保持",
          act_s.velocity is None
          and act_s.derivative_statuses.get("flap_angular_velocity")
          == "event_boundary_unavailable"
          and not act_s.derivative_available)


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else LOG_DIR
    os.makedirs(out_dir, exist_ok=True)
    print("I-221 先行施工检查（阶段证据；整项判据验收等 I-220 后办理）")
    print("=" * 72)
    cmd_f01()
    cmd_f02()
    cmd_f03()
    cmd_f04()
    cmd_f05()
    cmd_f06()
    cmd_f07()
    cmd_f08()
    cmd_f09()
    cmd_f10()
    cmd_r01()
    cmd_r02()
    cmd_r03()
    cmd_s01()
    cmd_s02()
    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[1]]
    print("=" * 72)
    print("合计 %d 项，通过 %d 项，失败 %d 项" % (total, total - len(failed),
                                                 len(failed)))
    result_doc = {
        "item_id": "I-221",
        "scope": "partial_start（先行施工阶段证据，不勾选整项判据）",
        "run_id": "run-kt-kula-i221-status-propagation-rework-20261015-a1",
        "rework": "先行续修 Fil 返修复核两处状态传播缺口：S01 合成后最终"
                  "目标身份（非零目标按普通数值输出，输入身份保留在 "
                  "shape_presence）、S02 有数值的声明近似导数身份不升级；"
                  "原 F01—F10 与 R01—R03 保留（R03b 预期按契约更正）",
        "fixture_note": "时钟样本全部由 check_fixture: 前缀的检查夹具构造，"
                        "不含真实时钟接通声明；生产代码对来源前缀做守卫",
        "checks": [{"name": n, "pass": ok, "detail": d}
                   for n, ok, d in RESULTS],
        "summary": {"total": total, "passed": total - len(failed),
                    "failed": len(failed)},
    }
    out_path = os.path.join(out_dir, "verify_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result_doc, f, ensure_ascii=False, indent=1)
    print("结果已写入 %s" % out_path)
    if failed:
        for name, _ok, detail in failed:
            print("FAIL %s %s" % (name, detail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
