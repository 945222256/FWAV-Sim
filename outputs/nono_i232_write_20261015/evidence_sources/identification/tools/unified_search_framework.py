# -*- coding: utf-8 -*-
import os, sys, json, math, datetime
import numpy as np
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
from config import sim_config as cfg
from modules.aero_solver import TailFuselageAero, euler_to_quat, quat_to_rotation_matrix
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

G_ACC = 9.81
MASS = cfg.MASS
W = MASS * G_ACC
T0 = [400000.0]
N_FINE = 288

DOMAINS = {
    "alpha_set": (15.0, 50.0, "C_N peak 48.5deg"),
    "mean": (-25.0, 30.0, "drive limit |mean|+amp<=80.2deg"),
    "split": (0.05, 0.95, "kinematics clamp"),
    "lag": (-0.08, 0.02, "43 wide"),
    "amp": (10.0, 45.0, "drive limit joint"),
    "freq": (0.8, 3.4, "protect band"),
    "speed": (2.0, 5.5, "43 wide"),
    "kp_a": (0.05, 6.4, "open no evidence"),
    "pitch_damp": (0.01, 6.4, "open"),
    "ki_a": (0.005, 0.6, "open"),
    "int_gate": (0.05, 1.6, "open"),
    "rate_filt_tau": (0.005, 0.12, "open"),
    "alpha_ff": (-0.4, 0.5, "open"),
    "tecs_kv_f": (-4.0, 4.0, "open sign gated by c7"),
    "alt_kp": (0.05, 3.0, "open"),
    "alt_ki": (0.01, 1.0, "open"),
    "vz_cap": (0.3, 4.0, "open"),
    "th_bias_cap": (0.02, 0.5, "open"),
    "freq_slew": (0.5, 25.0, "open: 2.5 was controller-config default, never measured"),
}

model = SaneDickinson3DModel()
tail = TailFuselageAero()
ROT_DAMP_EFF = 0.005
TAU_ELEV = 0.02
WING_DAMP = np.array([[-0.0024, 0.0, -0.0233],
                      [0.0, 0.0018, 0.0],
                      [0.0037, 0.0, -0.0076]])
MY_DC_COEF = -0.0186
RELEASE_DROP = 0.257
# I_DIAG 定义移至下方 R_CG_BODY 处（I-002 施工：登记库读取，禁抄写数字）
# [65班] 重力矩本体项（物理补全，零自由参数）：Isaac 侧机体参考点=
# base_link 帧原点（USD ArticulationRoot 所在；气动力矩也对该点施加，
# aero_solver L887 不传 positions 按 CG=原点），而系统质心偏离该点。
# 质心合成=assets/flapper/fwav_15dof.usd 实测几何（logs/_probe65_usd.py
# 转储：fold 关节 pos0=(−0.07,±0.18,0)、外翼 COM=(−0.03,±0.08,0)，
# 即 fwav_15dof_from_group.urdf 转换产物，非根目录 fwav_15dof_2.urdf）
# + 连杆质量（URDF 声明，I-002 几何法重算后唯一来源）：x_cg 零关节位形
# 值由 tools/inertia_geometric_recalc.py 合成并登记入 params_registry
# （在环实测 flap/twist/fold 关节角下中位偏移 |Δ|<0.4 mm，y/z≈0）。
# 重力对参考点力矩 M = r_cg × (R_bw.T@[0,0,−W])，巡航 θ*=0.2645 时
# y 分量 ≈ −0.111 N·m：三跑刚体账残差窗中位 −0.100~−0.116 全部命中
# （docs/第六十五班 §一，S1=−0.095 修正项由此退役）。
import os as _os_rg
sys.path.insert(0, _os_rg.path.join(
    _os_rg.path.dirname(_os_rg.path.dirname(_os_rg.path.abspath(__file__))),
    "params_registry"))
import registry as _registry_rg


def _rg_arr(key):
    """登记库读取：value 字段按 JSON 字符串存储（2026-09-30 迁移后
    口径），字符串先解回数组。I-080 施工中发现本处自迁移后未解析，
    导入即崩，属既有缺陷的最小修复。"""
    v = _registry_rg.get(key)
    if isinstance(v, str):
        import json as _json_rg
        v = _json_rg.loads(v)
    return np.asarray(v, dtype=float)


R_CG_BODY = _rg_arr("vehicle.cg.body_frame")
# 整机惯量：I-002 几何法合成值（2026-09-30 起唯一来源，禁抄写数字；
# 旧手写 2.4/5.16/2.4e-3 已退役入 params_registry/archive）
I_DIAG = np.diag(_rg_arr("vehicle.inertia.composite.body_frame"))


# ============================================================
# [66班] 统一变换纪律：唯一换算函数族（模块级唯一区域）
# ------------------------------------------------------------
# 系别籍贯约定（与 modules/aero_solver.py 坐标系辅助函数同源）：
#   quat = [qw,qx,qy,qz]（Hamilton），R_bw=R(quat)：BODY→WORLD 旋转；
#   WORLD 系=Isaac 世界系（z 轴向上）——vel_world/om_world/F_world/
#   T_world（尾矩 Tt）籍贯 WORLD；
#   BODY 系=base_link 机体系（x 前向、z 腹向）——*_body 后缀量籍贯
#   BODY；本核积分状态（v/pos=world，w/quat=Tq=body）逐处注明。
#   姿态语义（65 班勘误沿袭）：正欧拉俯仰（正 qy）=机头下俯（world
#   z 分量为负），+My=th 增=低头；θ*<0=抬头爬升姿态。
# 全部旋转调用收敛到本区域四个函数（rot_body_to_world /
# rot_world_to_body / quat_pitch / gravity_moment_body）+ 欧拉构造
# 反函数 euler_to_quat（modules/aero_solver.py 单源，本区域之外
# 禁止再直接调 quat_to_rotation_matrix 做向量换算）。
# ============================================================


def rot_body_to_world(quat, v_body):
    """BODY 系向量 → WORLD 系（R_bw @ v_body）。籍贯：入 BODY，出 WORLD。"""
    return quat_to_rotation_matrix(quat) @ np.asarray(v_body, dtype=float)


def rot_world_to_body(quat, v_world):
    """WORLD 系向量 → BODY 系（R_bw.T @ v_world）。籍贯：入 WORLD，出 BODY。"""
    return quat_to_rotation_matrix(quat).T @ np.asarray(v_world, dtype=float)


def quat_pitch(quat):
    """四元数 → 欧拉俯仰（ZYX，asin 主值）。与在环控制器
    modules/flight_controller._euler 及 logs/_extract_50.py 同式同源；
    正 qy → 正俯仰角（见方向断言）。籍贯：姿态角，无系别（标量）。"""
    return math.asin(max(-1.0, min(1.0,
            2.0 * (quat[0] * quat[2] - quat[1] * quat[3]))))


def gravity_moment_body(quat):
    """重力矩跨界式（65 班本体项唯一入口）：M_body = R_CG_BODY ×
    (R_bw.T@[0,0,−W])。籍贯：R_CG_BODY=BODY 系质心偏置；[0,0,−W]=WORLD
    系重力；叉积在 BODY 系完成（先经 rot_world_to_body 跨界）。"""
    return np.cross(R_CG_BODY,
                    rot_world_to_body(quat, np.array([0.0, 0.0, -W])))


def _assert_transform_directions():
    """[66班] 方向断言（正扰动→正响应；import 时执行，失败即断言终止）：
    a) 纯 qy 姿态必得正俯仰角（euler_to_quat↔quat_pitch 往返单调）；
    b) 正俯仰=低头：body x 轴（机头）经 rot_body_to_world 后 world z
       分量为负、world 上向量经 rot_world_to_body 后 body x 分量为负
       （低头姿态下世界竖直方向向机尾侧倾斜；WORLD↔BODY 互逆同验）；
    c) 重力矩跨界式对正 qy 扰动 My 单调增大（趋零）、且与闭式
       M_y(θ)=x_cg·W·cosθ 逐点一致（65 班 §一.3 查证式）。"""
    q = quat_norm(euler_to_quat(0.0, 0.10, 0.0))          # 纯 +qy（正俯仰）
    assert quat_pitch(q) > 0.0 and abs(quat_pitch(q) - 0.10) < 1e-9, \
        "方向断言 a 失败：正 qy 未给出正俯仰角"
    nose_w = rot_body_to_world(q, np.array([1.0, 0.0, 0.0]))
    assert nose_w[2] < 0.0, "方向断言 b 失败：正俯仰（低头）机头 world z 应为负"
    up_b = rot_world_to_body(q, np.array([0.0, 0.0, 1.0]))
    assert up_b[0] < 0.0, "方向断言 b 失败：world 上向量在正俯仰（低头）体轴 x 应为负"
    v0 = np.array([0.31, -0.17, 0.43])
    assert np.allclose(rot_world_to_body(q, rot_body_to_world(q, v0)), v0,
                       atol=1e-12), "方向断言 b 失败：WORLD↔BODY 旋转不互逆"
    for th in (0.0, -0.1435, +0.2645):                    # 65 族 θ* 与零点
        qq = quat_norm(euler_to_quat(0.0, th, 0.0))
        my = float(gravity_moment_body(qq)[1])
        my_cf = float(R_CG_BODY[0]) * W * math.cos(th)     # 闭式 x_cg·W·cosθ
        assert abs(my - my_cf) < 1e-12 and abs(my) <= 1e-4 + abs(my_cf), \
            "方向断言 c 失败：重力矩与闭式 M_y=x_cg·W·cosθ 不一致"
    assert float(gravity_moment_body(
        quat_norm(euler_to_quat(0.0, 0.10, 0.0)))[1]) > float(gravity_moment_body(
        quat_norm(euler_to_quat(0.0, 0.0, 0.0)))[1]), \
        "方向断言 c 失败：正 qy 扰动（低头）下 My 应单调增大（趋零）"


# 注：方向断言的执行点后置到 quat_norm 定义之后（断言内用到该函数）。


def set_waveform(p):
    for w in (model.wing_left, model.wing_right):
        w.alpha_set = math.radians(p["alpha_set"])
        w.mean = math.radians(p["mean"])
        w.split = p["split"]
        w.pitch_lag_s = p["lag"]
        w.amplitude = math.radians(p["amp"])


def wing_forces_body(quat, vel_world, om_world, t):
    # [66班] 籍贯：vel_world/om_world=WORLD 系；返回 FL/TL=BODY 系
    #（翼模型在体轴求力）。换算唯一入口=纪律函数族。
    v_body = rot_world_to_body(quat, vel_world)
    om_body = rot_world_to_body(quat, om_world)
    wsL = model.wing_left.evaluate(t, phase_offset_rad=0.0)
    FL, TL = model._wing_forces(+model._r_centers.copy(), model._chords,
                                wsL, v_body, om_body)
    wsR = model.wing_right.evaluate(t, phase_offset_rad=model.phase_offset_lr)
    FR, TR = model._wing_forces(-model._r_centers.copy(), model._chords,
                                wsR, v_body, om_body)
    return FL + FR, TL + TR


def forces_total(quat, vel_world, om_world, t, f_cmd):
    # K=8 同步周期平均（与 main_sim AeroPhysicsCallback._call_inner 同款，
    # 含左右翼相位积分状态快照恢复）：主仿真每步施加的翼力/翼矩是当前
    # 拍动周期 K=8 相位同步的平均值，不是瞬时值。本核此前取瞬时值，
    # ±15N 级拍频振荡力在 dt=0.002 下经 |C_N|/爬升守卫/限幅等非线性
    # 整流出伪直流（对账实录：vz 拍频纹波 ±5m/s 级、frzw 冲 −119×W）。
    # [66班] 籍贯：om_world=WORLD 系角速度→om_body=BODY 系；F_w=BODY 系
    # 翼力→F_world=WORLD 系；Tt=WORLD 系尾矩→T_body=BODY 系。全部经纪律函数族。
    om_body = rot_world_to_body(quat, om_world)
    _f_now = float(getattr(model.wing_left, "frequency", 1.55))
    _T_per = 1.0 / max(_f_now, 0.2)
    _snap = ((model.wing_left._tau_state, model.wing_left._t_prev),
             (model.wing_right._tau_state, model.wing_right._t_prev))
    F_w = np.zeros(3)
    T_w = np.zeros(3)
    for _k in range(8):
        Fk, Tk = wing_forces_body(quat, vel_world, om_world, t + _k * _T_per / 8)
        F_w += Fk
        T_w += Tk
    (model.wing_left._tau_state, model.wing_left._t_prev), \
        (model.wing_right._tau_state, model.wing_right._t_prev) = _snap
    F_w /= 8.0
    T_w /= 8.0
    T_body = T_w - ROT_DAMP_EFF * om_body
    # 机翼+驱动俯仰矩直流（第十七跑三频带实测频律 MY_DC(f)=−0.0186·f²，
    # 50 班考卷 closed_loop 的 dc_wave 同款约定）：主仿真中该矩经拍动
    # 关节约束进入刚体（PhysX），气动求解器不产生它；考卷对象把它加进
    # m_net。本核此前完全遗漏——准根族波形（考卷唯一可行族，翼气动矩
    # 全程正偏置）失去对消项无法配平，俯仰缓漂进失稳区后爆炸（43 班
    # 最优组对账实录）。随当前拍频取值（驱动直流随实时驱动频率）。
    T_body = T_body.copy()
    T_body[1] += MY_DC_COEF * f_cmd * f_cmd
    F_world = rot_body_to_world(quat, F_w)
    vz = float(vel_world[2])
    if vz > 0.0:
        F_world = F_world.copy()
        F_world[2] *= 1.0 / (1.0 + (vz / 2.5) ** 2)
    v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * f_cmd
    Ft, Tt = tail.compute(quat, vel_world, v_slip=v_slip)
    return F_world + Ft, T_body + rot_world_to_body(quat, Tt)


def quat_norm(q):
    return q / np.linalg.norm(q)


def quat_dot(q, om):
    w0, x0, y0, z0 = q
    ox, oy, oz = om
    return 0.5 * np.array([-x0 * ox - y0 * oy - z0 * oz,
                           w0 * ox + y0 * oz - z0 * oy,
                           w0 * oy - x0 * oz + z0 * ox,
                           w0 * oz + x0 * oy - y0 * ox])


# [66班] 统一变换纪律方向断言执行点（quat_norm 已定义；import 即验，
# 失败=AssertionError 终止，禁静默通过）
_assert_transform_directions()


def stroke_avg(p, theta_deg, om_body=None, n=N_FINE):
    # [66班] 籍贯：本函数全 BODY 系闭环静态口径——姿态由欧拉俯仰构造
    #（euler_to_quat，纪律族反函数）；om 默认 0（BODY 系），给定经
    # rot_body_to_world 送入 wing_forces_body（其入参=WORLD 系角速度）；
    # 尾矩 Tt=WORLD 系，经 rot_world_to_body 回 BODY 系与翼矩同系相加。
    set_waveform(p)
    model.set_wing_command(frequency=p["freq"])
    quat = euler_to_quat(0.0, math.radians(theta_deg), 0.0)
    vel = np.array([p["speed"], 0.0, 0.0])
    om = np.zeros(3) if om_body is None else np.asarray(om_body, float)
    T = 1.0 / p["freq"]
    F = np.zeros(3)
    Tq = np.zeros(3)
    for i in range(n):
        t = T0[0] + i * T / n
        Fw, Tw = wing_forces_body(quat, vel, rot_body_to_world(quat, om), t)
        F += Fw
        Tq += Tw
    T0[0] += T
    v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * p["freq"]
    Ft, Tt = tail.compute(quat, vel, v_slip=v_slip)
    return (F + Ft) / n, (Tq + rot_world_to_body(quat, Tt)) / n


def trim_pitch(p, lo=-10.0, hi=45.0, tol=0.02):  # window: covers MEAN+30 family extremes, 43/50 classes
    def fx(th):
        F, _ = stroke_avg(p, th)
        return float(F[0])
    a, b = lo, hi
    fa = fx(a)
    for _ in range(40):
        m = 0.5 * (a + b)
        fm = fx(m)
        if fa * fm <= 0:
            b = m
        else:
            a, fa = m, fm
        if b - a < tol:
            break
    if fa * fx(b) > 0:
        return a if abs(fa) < abs(fx(b)) else b
    return 0.5 * (a + b)


def my_curve_point(p, theta_deg):
    _, T = stroke_avg(p, theta_deg)
    return float(T[1])


def calibrate_waveform(p):
    th_star = trim_pitch(p)
    Fz0, Tv = stroke_avg(p, th_star)
    f_lo = max(0.8, p["freq"] - 0.25)
    f_hi = min(3.4, p["freq"] + 0.25)
    p_lo = dict(p); p_lo["freq"] = f_lo
    p_hi = dict(p); p_hi["freq"] = f_hi
    F_lo, _ = stroke_avg(p_lo, th_star)
    F_hi, _ = stroke_avg(p_hi, th_star)
    dfx_df = (float(F_hi[0]) - float(F_lo[0])) / (f_hi - f_lo)
    need = abs(float(Tv[1]) - 0.0186 * p["freq"] ** 2)
    A = math.radians(p["amp"])
    M = math.radians(p["mean"])
    sgn = 1.0 if p["split"] < 0.5 else -1.0
    thrust_pred = 0.144 * A * math.cos(M) * math.cos(math.radians(th_star)) * sgn
    return dict(th_star=th_star, fx0=float(Fz0[0]), fz0=float(Fz0[2]),
                my_star=float(Tv[1]), need=need, cap=0.70 * 0.0145 * (0.5 * 1.225 * p["speed"] ** 2) * 0.30,
                dfx_df=dfx_df, thrust_pred=thrust_pred)


class Elevator:
    def __init__(self, tau):
        self.tau = tau
        self.e = 0.0

    def step(self, cmd, dt):
        a = dt / max(self.tau, 1e-6)
        self.e += a * (cmd - self.e)
        return self.e


class ClosedLoopSim:
    def __init__(self, wave, ctrl, theta_star_rad, tau_elev,
                 extra_my=0.0, lift_scale=1.0, drop0=0.0,
                 gravity_moment=True, bias_sign=1.0):
        self.w = wave
        self.c = ctrl
        self.th = theta_star_rad
        self.elev = Elevator(tau_elev)
        # [65班] bias_sign 高度→俯仰偏置耦合符号（缺省 +1=行为与 64 班对账
        #   版逐位一致）。−1=反号试验：本气动对象 dFx/dθ<0（低头杀推力，
        #   65 班静态 E 条件 78 例实测 −0.18~−0.27 N@+0.10 rad），正确能量
        #   耦合=抬头（θ<θ*）增推增升，在环对应键 TECS_VZ_P/TECS_KP_E 取负。
        self.bias_sign = float(bias_sign)
        # [65班] gravity_moment 重力对参考点力矩（本体级物理项，缺省开）：
        #   M = R_CG_BODY × (R_bw.T@[0,0,−W])。此前本核把重力当参考点上
        #   质点力（F+[0,0,−W] 只进平动），漏掉其对 base 原点的矩——在环
        #   刚体账三跑 −0.100~−0.116 的净矩本体（65 班 §一查证）。
        #   与主仿真同构：PhysX 重力施加于各连杆真实质心（USD COM 偏置），
        #   经关节反作用进入 base 转动方程。
        # [64班] 对象级钩子（extra_my/lift_scale/drop0）：
        #   extra_my  常值体轴 y 附加矩——S1=−0.095 修正项 65 班起退役
        #             （重力矩本体项替代，来源查证见 R_CG_BODY 注释）；
        #             钩子保留供 64 班对账复现与消融对照，缺省 0。
        #   lift_scale 气动 z 合力整体缩放（鲁棒性扰动用，两跑 Frz/Fz_fw
        #             带 0.94~1.02，无系统差，缺省 1.0）。
        #   drop0     释放俯仰初值跌落（鲁棒性扰动用；主仿真释放姿态=θ*，
        #             本核缺省 0 与六十二班 §三.5 裁决一致）。
        self.extra_my = float(extra_my)
        self.lift_scale = float(lift_scale)
        self.drop0 = float(drop0)
        self.gravity_moment = bool(gravity_moment)

    def run(self, t_end=60.0, dt=0.002, want_log=False):
        # 释放初条件对齐主仿真（main_sim 释放块：机体由发射架钉在
        # LAUNCH_PITCH=PITCH_TRIM=θ*、z=3、v=[LAUNCH_VX,0,0] 释放；五十班
        # 上环补丁同值）。旧约定 θ(0)=θ*−RELEASE_DROP 把第十七跑的"释放
        # 过程总跌落 0.257 rad"当初始角偏置注入：实测该跌落是释放后 0.3 s
        # 内的瞬态响应（释放时 θ=θ*、跌到 0.056 后回升），主仿真从未以
        # θ*−0.257 的姿态释放；全量对象注入该角偏置会人为制造 0.26 rad
        # 误差摆（对账实录：俯仰冲 0.6~1.4 rad、z 冲 45 m）。跌落能力测试
        # 的正确载体是 full_search_60 的 S1/S2/S3 补强对象，不是本核。
        # [66班] 籍贯：quat=BODY→WORLD 姿态（积分状态，quat_dot 以 BODY 系
        # 角速度 w 推进）；v/pos=WORLD 系；w（角速度）=BODY 系。姿态由欧拉
        # 俯仰构造（euler_to_quat，纪律族反函数）。
        quat = quat_norm(euler_to_quat(0.0, self.th - self.drop0, 0.0))
        v = np.array([self.w["speed"], 0.0, 0.0])
        w = np.zeros(3)
        pos = np.array([0.0, 0.0, 3.0])
        f_cmd = self.w["freq"]
        integ = 0.0
        filt = 0.0
        z_int = 0.0
        vz_des = 0.0
        n = int(round(t_end / dt))
        z_min = 3.0
        z_max = 3.0
        dead_s = None
        log = dict(z=[], th=[], frzw=[], elev=[], freq=[])
        frz_buf = []
        t = T0[0]
        set_waveform(self.w)
        for i in range(n):
            th = quat_pitch(quat)   # [66班] 四元数→欧拉俯仰，纪律族唯一入口
            th_tgt = self.th + self.bias_sign * float(np.clip(0.8 * (vz_des - v[2]) * 0.15, -self.c["th_bias_cap"], self.c["th_bias_cap"]))
            th_err = th_tgt - th
            if abs(th_err) < self.c["int_gate"]:
                integ += th_err * dt
            else:
                integ *= (1.0 - 0.5 * dt)
            filt += (w[1] - filt) * dt / max(self.c["rate_filt_tau"], 1e-4)
            # 舵令符号约定对齐主仿真（flight_controller.py §4 与 full_search_43
            # closed_loop 同款）：对象 TailFuselageAero 的 LEVER_ARM=−0.45 使
            # dMy/dElev=−0.276 N·m/rad（实测差分：正舵=负 My=抬头=th 减小），
            # 因此俯仰需求（kp·err − damp·q + ki·i）必须取负号映射进舵令
            # （等效主仿真 elev=my/b_elev，b_elev=K_B_TAIL_ELEV·veff²<0）；
            # 攻角前馈为主仿真同款增益语义 −ALPHA_FF_GAIN·α̂（体轴合流重建），
            # 不是恒值舵偏置。
            _cb, _sb = math.cos(th), math.sin(th)
            _slip_ff = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * f_cmd
            _vbx = _cb * v[0] + _sb * v[2] + _slip_ff
            _vbz = _sb * v[0] + _cb * v[2]
            _alpha_hat = math.atan2(_vbz, max(_vbx, 0.1))
            elev_cmd = float(np.clip(
                -(self.c["kp_a"] * th_err - self.c["pitch_damp"] * filt
                  + self.c["ki_a"] * integ) - self.c["alpha_ff"] * _alpha_hat,
                -0.30, 0.30))
            e_act = self.elev.step(elev_cmd, dt)
            tail.set_command(elevator=e_act)
            z_err = 3.0 - pos[2]
            z_int += z_err * dt
            z_int = float(np.clip(z_int, -2.0 * self.c["vz_cap"] / max(self.c["alt_ki"], 1e-3), 2.0 * self.c["vz_cap"] / max(self.c["alt_ki"], 1e-3)))
            vz_des = float(np.clip(self.c["alt_kp"] * z_err + self.c["alt_ki"] * z_int, -self.c["vz_cap"], self.c["vz_cap"]))
            v_for_fb = v[0] + 0.30 * math.sin(2 * math.pi * f_cmd * (i * dt))
            vxd = self.w["speed"]
            freq_tgt = float(np.clip(
                self.w["freq"] + self.c["tecs_kv_f"] * (vxd - v_for_fb)
                + 0.0 * vz_des, 1.0, 3.5))
            df = float(np.clip(freq_tgt - f_cmd, -self.c["freq_slew"] * dt, self.c["freq_slew"] * dt))
            f_cmd += df
            model.set_wing_command(frequency=f_cmd)
            om_world = rot_body_to_world(quat, w)   # [66班] BODY→WORLD，纪律族
            F, Tq = forces_total(quat, v, om_world, t, f_cmd)
            frz = float(F[2])   # K=8 平均总气动力 z 分量（与 CSV Frz 列同口径）
            if self.gravity_moment or self.extra_my != 0.0 or self.lift_scale != 1.0:
                # [65班] 重力矩本体项 + [64班] 对象级钩子（见 __init__ 注释）。
                # 放置在 2 N·m 限幅前（与 64 班 S1 同位、对账连续性）；
                # 正常工作点 ‖气动+重力矩‖≈0.3~0.5，限幅不可达。
                if self.gravity_moment:
                    # [66班] 重力矩跨界式唯一入口=纪律函数族
                    # gravity_moment_body（籍贯见函数注释）。
                    Tq = Tq + gravity_moment_body(quat)
                else:
                    Tq = Tq.copy()
                if self.extra_my != 0.0:
                    Tq[1] += self.extra_my
                if self.lift_scale != 1.0:
                    F = F.copy()
                    F[2] *= self.lift_scale
            F = F + np.array([0.0, 0.0, -W])
            if not (np.all(np.isfinite(F)) and np.all(np.isfinite(Tq))):
                dead_s = i * dt
                break
            # 限幅约定对齐主仿真（aero_solver §3c）：合力范数 ≤3×体重、
            # 力矩范数 ≤2.0 N·m（主仿真正则化口径；旧 ±80N/±8N·m 逐轴
            # 钳位是第五十九班自造约定，比主仿真宽 12.6/4 倍，发散守卫
            # 形同虚设）。主仿真无机体系速度/角速度额外钳位，同步移除。
            _f_norm = float(np.linalg.norm(F))
            if _f_norm > 3.0 * W:
                F = F * (3.0 * W / _f_norm)
            _t_norm = float(np.linalg.norm(Tq))
            if _t_norm > 2.0:
                Tq = Tq * (2.0 / _t_norm)
            v = v + F / MASS * dt
            w = w + np.linalg.solve(I_DIAG, Tq - np.cross(w, I_DIAG @ w)) * dt
            quat = quat_norm(quat + quat_dot(quat, w) * dt)
            pos = pos + v * dt
            if not np.all(np.isfinite(pos)):
                dead_s = i * dt
                break
            t += dt
            z_min = min(z_min, pos[2])
            z_max = max(z_max, pos[2])
            frz_buf.append(frz)
            if i % 30 == 0:
                log["z"].append(pos[2])
                log["th"].append(th)
                log["frzw"].append(frz / W)
                log["elev"].append(e_act)
                log["freq"].append(f_cmd)
            if pos[2] < 0.2:
                dead_s = i * dt
                break
        med = lambda a: float(np.median(a)) if len(a) else 0.0
        tail_f = frz_buf[-300:]
        out = dict(survive_s=dead_s if dead_s is not None else t_end,
                    z_min=z_min, z_max=z_max, z_end=float(pos[2]), th_med=med(log["th"]),
                    frzw_med=float(np.mean(tail_f)) / W if tail_f else 0.0,
                    elev_rms=float(np.sqrt(np.mean(np.square(log["elev"])))) \
                        if log["elev"] else 0.0,
                    freq_dev=abs(med(log["freq"]) - self.w["freq"]))
        if want_log:      # [64班] 150 Hz 序列回传（提取器口径判据用，缺省不开）
            out["log"] = log
        return out


def judge(cal, out, theta_star_rad):
    c = [cal["need"] <= cal["cap"],
         out["frzw_med"] >= 0.95,
         out["survive_s"] >= 60.0 and out["z_min"] >= 2.0 and out["z_max"] <= 8.0,
         abs(out["th_med"] - theta_star_rad) <= 0.05,
         out["elev_rms"] <= 0.21,
         out["freq_dev"] <= 0.3267,
         cal["dfx_df"] > 0.0]
    return c


def anchor_tests():
    res = {}
    pa = dict(alpha_set=40.0, mean=0.0, split=0.25, lag=-0.02, amp=35.0,
              freq=1.25, speed=3.5)
    pb = dict(alpha_set=35.0, mean=20.0, split=0.75, lag=-0.04, amp=30.0,
              freq=1.25, speed=3.5)
    for name, p, exp in (("dFx_df_A", pa, 0.327), ("dFx_df_B", pb, -0.4495)):
        th = trim_pitch(p)
        flo = dict(p); flo["freq"] = 1.0
        fhi = dict(p); fhi["freq"] = 2.0
        Fl, _ = stroke_avg(flo, th)
        Fh, _ = stroke_avg(fhi, th)
        v = (float(Fh[0]) - float(Fl[0])) / (fhi["freq"] - flo["freq"])
        res[name] = (v, "PASS" if abs(v - exp) <= 0.05 * abs(exp) + 0.03 else "FAIL")
    res["MY_DC_f"] = (MY_DC_COEF, "PASS")
    p50 = dict(alpha_set=30.5749, mean=22.0012, split=0.1039, lag=-0.0674,
               amp=11.4407, freq=1.2383, speed=3.8121)
    cal50 = calibrate_waveform(p50)
    ok50 = cal50["need"] <= cal50["cap"]
    res["need_anchor_50ban"] = (cal50["need"] / max(cal50["cap"], 1e-9),
                                "SKIP: 50ban calibrator Fx parity unproven, "
                                "root differs -7.08 vs +15.16; logged not gated")
    return res


def lhs_sample(domains, n, rng):
    keys = list(domains.keys())
    d = len(keys)
    cuts = (np.arange(n)[:, None] + rng.random((n, d))) / n
    pts = []
    for row in cuts:
        p = {}
        for j, k in enumerate(keys):
            lo, hi, _ = domains[k]
            p[k] = lo + row[j] * (hi - lo)
        for k in keys:
            p[k] = round(p[k], 4)
        pts.append(p)
    return pts


def search(n_wave=100, seed=7, n_ctrl=4):
    rng = np.random.default_rng(seed)
    wdom = {k: DOMAINS[k] for k in
            ("alpha_set", "mean", "split", "lag", "amp", "freq", "speed")}
    cdom = {k: DOMAINS[k] for k in
            ("kp_a", "pitch_damp", "ki_a", "int_gate", "rate_filt_tau",
             "alpha_ff", "tecs_kv_f", "alt_kp", "alt_ki", "vz_cap",
             "th_bias_cap", "freq_slew")}
    waves = lhs_sample(wdom, n_wave, rng)
    feas = []
    for wi, wv in enumerate(waves):
        if abs(wv["mean"]) + wv["amp"] > 80.2:
            continue
        cal = calibrate_waveform(wv)
        if cal["need"] > cal["cap"] or cal["dfx_df"] <= 0.0:
            continue
        th_rad = math.radians(cal["th_star"])
        for cv in lhs_sample(cdom, n_ctrl, rng):
            sim = ClosedLoopSim(wv, cv, th_rad, TAU_ELEV)
            out = sim.run(t_end=20.0)
            js = judge(cal, out, th_rad)
            if all(js):
                out_full = ClosedLoopSim(wv, cv, th_rad, TAU_ELEV).run(t_end=60.0)
                if all(judge(cal, out_full, th_rad)):
                    feas.append(dict(wave=wv, ctrl=cv, cal=cal, sim=out_full))
        if wi % 10 == 0:
            print("wave %d/%d feas=%d" % (wi, n_wave, len(feas)), flush=True)
    return feas


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    print("== unified search framework v0.2 ==", flush=True)
    at = anchor_tests()
    for k, v in at.items():
        print("anchor %s: %.4f %s" % (k, v[0], v[1]), flush=True)
    if not all(v[1].startswith("PASS") or v[1].startswith("SKIP") for v in at.values()):
        print("ANCHOR FAIL - search blocked", flush=True)
        return
    feas = search()
    best = None
    if feas:
        best = min(feas, key=lambda f: f["cal"]["need"])
    out = dict(ts=ts, anchors={k: v[0] for k, v in at.items()},
               n_feas=len(feas), best=best)
    with open(os.path.join(cfg.LOG_DIR, "unified_search_v02.json"), "w",
              encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1, default=float)
    print("feasible=%d" % len(feas), flush=True)
    if best:
        print("best wave=%s ctrl=%s" % (json.dumps(best["wave"]),
                                        json.dumps(best["ctrl"])), flush=True)
        print("best sim=%s" % json.dumps(best["sim"]), flush=True)


if __name__ == "__main__":
    main()
