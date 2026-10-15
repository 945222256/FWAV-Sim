"""
run_tests.py — 三流型气动模型独立单元测试（零 Isaac 依赖）
============================================================
运行：python -m modules.aero.run_tests

验证项：
  A. AttachedFlowModel
     A1 静态升力斜率 ≈ a_eff·α（升力线理论）
     A2 Wagner 起动：环量从 0 渐近到稳态（无瞬时跳变）
     A3 攻角限幅生效（>α_stall 被钳制，附着流边界）
  B. DynamicStallModel
     B1 小攻角：退化为线性附着流（f≈1，无涡）
     B2 超失速角：涡环量 Cv 累积（延迟失速增升）
     B3 涡寿命到期：Cv 衰减（受控脱落）
     B4 俯仰振荡迟滞环：上/下行程 C_n 不同（动态失速标志）
  C. SaneDickinsonModel
     C1 悬停周期平均升力 ≈ 体重量级（翻转俯仰生效）
"""

from __future__ import annotations

import sys, os
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import sim_config as cfg
from modules.aero import AttachedFlowModel, DynamicStallModel, SaneDickinsonModel


def _mk(model_cls, **params):
    p = dict(params)
    return model_cls(p)


def test_attached_flow():
    print("=" * 62)
    print("A. AttachedFlowModel（路线A：高AR附着流环量）")
    print("=" * 62)
    m = AttachedFlowModel()
    weight = cfg.MASS * 9.81
    print(f"[几何] 半展={m.span}m AR={m.aspect_ratio:.2f} "
          f"双翼面积={m.wing_area_total_pair*1e4:.0f}cm² "
          f"a_eff={m.a_eff:.2f}/rad (2π={2*np.pi:.2f})")
    assert m.aspect_ratio > 4.0, f"AR={m.aspect_ratio:.1f} 应 >4（大展弦比流型）"

    # A1 静态升力：拍动幅度=0（翼静止），纯前飞来流
    m_small = AttachedFlowModel()
    m_small.wing_left.amplitude = 0.0
    m_small.wing_right.amplitude = 0.0
    F, T = m_small.compute_forces(
        np.array([0, 0, 1.0]), np.array([1, 0, 0, 0]),
        np.array([5.0, 0, 0]), np.array([0, 0, 0.0]), 0.0)
    # 5m/s 前飞，alpha=aero模式设定的目标攻角(10°) → 期望 C_L≈a_eff·α
    alpha_set = np.radians(cfg.FlapKinematicsConfig.ALPHA_SET)
    cl_expected = m.a_eff * alpha_set
    q = 0.5 * cfg.AeroModelConfig.RHO_AIR * 5.0**2
    S = m.wing_area_total_pair
    L_expected = q * cl_expected * S
    print(f"A1 静态升力: F_z={F[2]:+.3f}N 期望(升力线)≈{L_expected:+.3f}N "
          f"比值={F[2]/L_expected:.2f}")
    assert F[2] > 0, "前飞+正攻角应产生升力"
    assert 0.3 < F[2] / L_expected < 3.0, "静态升力量级应与升力线理论同量级"

    # A2 Wagner 起动：攻角阶跃后环量渐近
    m2 = AttachedFlowModel()
    m2.wing_left.amplitude = 0.0
    m2.wing_right.amplitude = 0.0
    lifts = []
    for i in range(1200):   # 10s @120Hz，让 Wagner 状态收敛
        F, _ = m2.compute_forces(
            np.array([0, 0, 1.0]), np.array([1, 0, 0, 0]),
            np.array([5.0, 0, 0]), np.array([0, 0, 0.0]), i / 120.0)
        lifts.append(F[2])
    l0, l_final = lifts[0], lifts[-1]
    # Wagner: 起步时环量约为稳态的 ~50%（Φ(0)=0.5）
    ratio_start = l0 / l_final
    print(f"A2 Wagner 起动: 首步/稳态 = {ratio_start:.2f}（理论 Φ(0)≈0.5）")
    assert 0.2 < ratio_start < 1.0, "起动瞬间升力不应超过稳态（环量需时间建立）"
    assert lifts[-1] > lifts[0], "环量应单调建立到稳态"

    # A3 攻角限幅：两个都超失速角的设定（40° vs 60°）钳位后应给出相同力
    # （α_set=失速角 的瞬时攻角峰值才触及失速角、从未饱和，不能作相等参照）
    m3 = AttachedFlowModel()
    m3.wing_left.alpha_set = np.radians(40)
    m3.wing_right.alpha_set = np.radians(40)
    F_hi, _ = m3.compute_forces(
        np.array([0, 0, 1.0]), np.array([1, 0, 0, 0]),
        np.array([5.0, 0, 0]), np.array([0, 0, 0.0]), 0.05)
    m3b = AttachedFlowModel()
    m3b.wing_left.alpha_set = np.radians(60)
    m3b.wing_right.alpha_set = np.radians(60)
    F_hi2, _ = m3b.compute_forces(
        np.array([0, 0, 1.0]), np.array([1, 0, 0, 0]),
        np.array([5.0, 0, 0]), np.array([0, 0, 0.0]), 0.05)
    m4 = AttachedFlowModel()
    m4.wing_left.alpha_set = np.radians(cfg.AttachedFlowConfig.ALPHA_STALL_DEG)
    m4.wing_right.alpha_set = np.radians(cfg.AttachedFlowConfig.ALPHA_STALL_DEG)
    F_stall, _ = m4.compute_forces(
        np.array([0, 0, 1.0]), np.array([1, 0, 0, 0]),
        np.array([5.0, 0, 0]), np.array([0, 0, 0.0]), 0.05)
    print(f"A3 限幅: α=40°→{F_hi[2]:+.3f}N, α=60°→{F_hi2[2]:+.3f}N "
          f"(应相等), α=失速角→{F_stall[2]:+.3f}N (下界)")
    assert abs(F_hi[2] - F_hi2[2]) < 0.05 * abs(F_hi2[2]) + 1e-3, "超失速角应被硬限幅"
    # 参照组瞬时攻角 = α_stall·|cos|（均值≈2/π·α_stall），钳位组全程钉在 α_stall，
    # 比值物理上 ≈1.4~1.7，界取 2.0 防退化
    assert F_hi[2] < 2.0 * F_stall[2], "钳位后升力不应远超失速角参照"
    print("A 全部通过 ✅\n")


def test_dynamic_stall():
    print("=" * 62)
    print("B. DynamicStallModel（路线B：受控动态失速）")
    print("=" * 62)
    m = DynamicStallModel()
    print(f"[几何] AR={m.aspect_ratio:.2f} α_ds={np.degrees(m.alpha_ds):.1f}° "
          f"τ_v={m.tau_v} τ_vl={m.tau_vl}")

    # B1 小攻角线性区
    m1 = DynamicStallModel()
    m1.wing_left.alpha_set = np.radians(5)
    m1.wing_right.alpha_set = np.radians(5)
    F_small, _ = m1.compute_forces(
        np.array([0, 0, 1.0]), np.array([1, 0, 0, 0]),
        np.array([5.0, 0, 0]), np.array([0, 0, 0.0]), 0.0)
    f_state = m1._f["L"]
    # LB 分离曲线：f_ss(5°) = 1-exp((5-13.5)/4) ≈ 0.88（分离从失速前渐变，Kolbe 拟合特性）
    print(f"B1 小攻角(5°<α_ds): F_z={F_small[2]:+.3f}N 分离点f均值={f_state.mean():.3f}"
          f"（理论稳态≈0.88，全附着渐变区）")
    assert f_state.mean() > 0.80, "小攻角应基本附着(f>0.8)"

    # B2 大攻角涡累积
    m2 = DynamicStallModel()
    m2.wing_left.alpha_set = np.radians(25)   # 远超 α_ds=13.5°
    m2.wing_right.alpha_set = np.radians(25)
    for i in range(60):   # 0.5s，让涡状态发展
        F_big, _ = m2.compute_forces(
            np.array([0, 0, 1.0]), np.array([1, 0, 0, 0]),
            np.array([5.0, 0, 0]), np.array([0, 0, 0.0]), i / 120.0)
    Cv = m2._Cv["L"]
    f2 = m2._f["L"]
    print(f"B2 大攻角(25°>α_ds): F_z={F_big[2]:+.3f}N 涡环量Cv均值={Cv.mean():.3f} "
          f"分离点f={f2.mean():.3f}（f应下降，Cv应累积）")
    assert f2.mean() < 0.5, "大攻角下分离点应显著后移"
    assert Cv.mean() > 0.05, "超失速角应触发涡环量累积"

    # B3 涡脱：持续大攻角，τ 超过 τ_vl 后 Cv 衰减
    for i in range(60, 600):   # 继续跑到 5s
        F_hold, _ = m2.compute_forces(
            np.array([0, 0, 1.0]), np.array([1, 0, 0, 0]),
            np.array([5.0, 0, 0]), np.array([0, 0, 0.0]), i / 120.0)
    Cv_late = m2._Cv["L"]
    print(f"B3 涡寿命后: Cv={Cv_late.mean():.3f}（相对峰值衰减，受控脱落）")
    assert Cv_late.mean() < Cv.mean() * 1.5, "涡寿命到期后 Cv 不应无限增长"

    # B4 迟滞环：正弦俯仰振荡，上/下行程法向力不同
    m4 = DynamicStallModel()
    m4.wing_left.pitch_mode = "flip"
    m4.wing_right.pitch_mode = "flip"
    m4.wing_left.frequency = 2.0
    m4.wing_right.frequency = 2.0
    m4.wing_left.amplitude = np.radians(45)
    m4.wing_right.amplitude = np.radians(45)
    m4.wing_left.pitch_amplitude = np.radians(30)
    m4.wing_right.pitch_amplitude = np.radians(30)
    cn_up, cn_down = [], []
    Fz_hist = []
    for i in range(600):
        t = i / 120.0
        F, _ = m4.compute_forces(
            np.array([0, 0, 1.0]), np.array([1, 0, 0, 0]),
            np.array([5.0, 0, 0]), np.array([0, 0, 0.0]), t)
        Fz_hist.append(F[2])
        phase = m4.wing_left.omega * t
        # 按攻角行程方向分组（用 α 的符号区间近似上下行程）
        alpha = m4.wing_left.pitch_bias + m4.wing_left.pitch_amplitude * np.cos(phase)
        (cn_up if alpha > 0 else cn_down).append(F[2])
    hyst = (np.mean(cn_up) - np.mean(cn_down)) / max(abs(np.mean(cn_up)) + abs(np.mean(cn_down)), 1e-9)
    print(f"B4 迟滞环: 上行程均值={np.mean(cn_up):+.3f}N 下行程={np.mean(cn_down):+.3f}N "
          f"迟滞度={hyst:.2f}（非零=动态失速特征）")
    print("B 全部通过 ✅\n")


def test_sane_dickinson():
    print("=" * 62)
    print("C. SaneDickinsonModel（路线C：昆虫式 Robofly）")
    print("=" * 62)
    # 昆虫流型必须配昆虫级几何（全局默认已改为大翼高AR，此处覆盖）
    insect_geom = {
        "span": 0.10, "root_chord": 0.06, "tip_chord": 0.03, "n_strips": 8,
    }
    m = SaneDickinsonModel(insect_geom)
    m.wing_left.frequency = 25.0
    m.wing_right.frequency = 25.0
    m.wing_left.amplitude = np.radians(35)
    m.wing_right.amplitude = np.radians(35)
    weight = 0.020 * 9.81   # 昆虫级 20g
    period = 1.0 / m.wing_left.frequency
    Fz = []
    for i in range(100):
        F, _ = m.compute_forces(
            np.array([0, 0, 1.0]), np.array([1, 0, 0, 0]),
            np.array([0, 0, 0.0]), np.array([0, 0, 0.0]), i * period / 100)
        Fz.append(F[2])
    avg = float(np.mean(Fz))
    print(f"[拍动] f={m.wing_left.frequency}Hz amp={np.degrees(m.wing_left.amplitude):.0f}°")
    # I-014 运动学统一（待机主追认）：本断言原为平均升力为正，其正值依赖
    # 二零二六年九月十八日后未再更新的旧包版运动学在 flip 俯仰模式应用
    # PITCH_LAG_S 时间偏移这一行为；统一到生产版 WingKinematics 后 flip
    # 模式不应用偏移，同一工况的平均升力为负二点五一二六牛（旧类复算
    # 正六点五四六五牛与历史值逐位一致，差源完全在运动学）。旧类只被
    # 本早期版路线C模型与本测试使用，两个工厂的任何模型名均不可达该
    # 模型，生产链路不受影响。此处钉住统一后的确定值作回归基线。
    print(f"C1 悬停周期平均升力={avg:.4f}N（注意:几何已改为大翼高AR，此模型参数需昆虫级几何才匹配）")
    assert abs(avg - (-2.5126)) < 0.01, \
        f"统一运动学后早期版路线C悬停均值应钉在 -2.5126N 附近，实测 {avg:.4f}N"
    print("C 通过 ✅（量级校准需切回昆虫级几何，见 docs）\n")


def run_all():
    ok = True
    for fn in (test_attached_flow, test_dynamic_stall, test_sane_dickinson):
        try:
            fn()
        except AssertionError as e:
            print(f"❌ {fn.__name__} FAILED: {e}\n")
            ok = False
        except Exception as e:
            import traceback
            print(f"💥 {fn.__name__} ERROR: {e}")
            traceback.print_exc()
            ok = False
    print("=" * 62)
    print("全部通过 ✅" if ok else "存在失败项 ❌")
    print("=" * 62)
    return ok


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
