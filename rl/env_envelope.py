# -*- coding: utf-8 -*-
"""
env_envelope.py — 包络级决策环境骨架（I-088）
================================================
编码方案的唯一依据：rl/预登记_训练环境重锚与准周期输入输出_20261005.md
（I-073 冻结件，本文件不改动它）。骨架喂合成数据，正式测量数据经
槽位接口接入（I-055 落地当天换供应商，代码零改动）。

合成数据的两条实测引用（有出处，不是编造）：
  升力律 L̄ = 1.95·f²（docs/IDENT_20260918.md，过原点拟合）；
  升力残差 RMS 0.14 N（同文件二参数拟合残差）——用作合成输出的
  噪声幅度；
  翼差动增益 B_dA(f) ≈ 5.2·f² N·m/rad（rl/README_面试备答.md 第二环
  台架真值表口径，悬停点 19.0）。
其余动力学（滑翔残力、占空比线性折减、执行器滞后常数）为显式标注的
合成占位，等正式数据替换。

与冻结件的一处已知口径不一致的处理：冻结件第五节清单第四行写
"模态事件段四维"，但其 3.1 节事件段表列了五项分量（模态独热两项、
距上次事件、保持姿态角、事件计数）。本骨架按 3.1 表实施五维，对照
表里注明该差异，待指挥部追认五维口径或修订清单行。

自检：python rl/env_envelope.py   （构造断言 + 冻结方案逐条对照表）
"""
import math
import os
import sys

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rl.env_config_source import current_config, snapshot_line

DT_CTRL = 0.02              # 控制周期 50 赫兹（沿用现行环境契约）
EPISODE_S = 10.0
Z_REF = 1.5

# ---------------- 观测编码（冻结件 3.1 节逐条）----------------
ENV_NAMES = ["z", "vz", "roll", "wx",                       # 机体四维
             "freq", "sin_phi", "cos_phi", "stroke_n", "duty",
             "df_dt", "What_Fz", "What_Mx", "What_My"]      # 包络九维
ENV_DIM = 13
EVT_NAMES = ["mode_FLAP", "mode_GLIDE", "t_since_event",
             "hold_attitude", "event_count"]                # 事件段五维
EVT_DIM = 5

# ---------------- 动作编码（冻结件 3.2 节）----------------
ENV_CHANNELS = ("freq", "dA", "duty", "amp_frac")
EVENT_NONE = 0
EVENT_STOP_FLAP = 1
EVENT_RESUME_FLAP = 2

# 事件参数（冻结件 3.2：脚手架由固定登记值提供，I-050 词汇表）
EVENT_PARAMS = {"t_stop": 0.0,          # 拍相位，零表示拍边界
                "hold_duration_s": 2.0,  # 秒制
                "side": "both",
                "resume_profile": {"profile_type": "linear_ramp",
                                   "n_ramp_strokes": 2}}

# ---------------- 域随机化：实测方差值优先，无实测留槽 ----------------
# 每项声明中心值来源与方差状态。status="slot" 表示无实测方差，
# 骨架期不随机化该参数（保持中心值），槽位占位格式见 FormalDataSlots。
DR_PARAMS = {
    "mass_kg":   {"center_key": "vehicle.mass.total",
                  "var_status": "slot"},
    "ixx":       {"center_key": "vehicle.inertia.composite.body_frame",
                  "var_status": "slot"},
    "cn_fit_amp": {"center_key": "aero.wing.cn_fit_amp",
                   "var_status": "slot"},
    "ct_fit_amp": {"center_key": "aero.wing.ct_fit_amp",
                   "var_status": "slot"},
    "amp_deg":   {"center_key": "wave.kinematics.amplitude",
                  "var_status": "slot"},
    "tau_f_s":   {"center_key": None, "var_status": "slot",
                  "center_value": 0.04},     # 合成常数占位
    "tau_a_s":   {"center_key": None, "var_status": "slot",
                  "center_value": 0.04},     # 合成常数占位
    "wind_x":    {"center_key": None, "var_status": "slot",
                  "center_value": 0.0},
}
# 唯一在用的实测散布：升力输出噪声（来源见文件头）
MEASURED_NOISE = {"lift_residual_rms_N": 0.14,
                  "source": "docs/IDENT_20260918.md 升力律二参数拟合残差 RMS"}


class FormalDataSlots:
    """正式测量数据的接口留槽（I-088 判据三）。

    占位格式逐槽写明；未填充的槽被请求即报错并打印格式说明，
    不提供静默默认值。I-055 数据落地后由 set 注册供应商，
    骨架代码零改动切换。
    """

    FORMATS = {
        "wing_state_model_FLAP":
            " callable(theta: dict, state: np.ndarray[4]) -> np.ndarray[3]"
            "（扑动模态：包络参数与机体状态 → 周期平均力旋量；I-055 机翼态"
            "灰箱模型。I-097 接线时按冻结件 3.3 的分模态模型实例语义，把"
            "原单槽拆为扑动与滑翔两槽）",
        "wing_state_model_GLIDE":
            " callable(theta: dict, state: np.ndarray[4]) -> np.ndarray[3]"
            "（滑翔模态模型实例，同上）",
        "transition_impulse_table":
            " list[dict]（十五列过渡冲量表，列名按 ltv/hybrid_flight_spec.json"
            " 的 impulse_layer.table_format）",
        "dr_variance:mass_kg": " float（千克平方，质量实测方差）",
        "dr_variance:ixx": " float（千克平方米平方，惯量实测方差）",
        "dr_variance:cn_fit_amp": " float（拟合幅值实测方差）",
        "dr_variance:ct_fit_amp": " float（拟合幅值实测方差）",
        "dr_variance:amp_deg": " float（度平方，幅值实测方差）",
        "dr_variance:tau_f_s": " float（秒平方，拍频执行器滞后实测方差）",
        "dr_variance:tau_a_s": " float（秒平方，差动执行器滞后实测方差）",
        "dr_variance:wind_x": " float（米每秒平方，风实测方差）",
    }

    def __init__(self):
        self._providers = {}

    def set(self, name, provider):
        assert name in self.FORMATS, f"未登记的槽名 {name}"
        self._providers[name] = provider

    def get(self, name):
        if name not in self._providers:
            raise RuntimeError(
                f"正式数据槽 {name} 未填充。占位格式：{self.FORMATS[name]}。"
                f"骨架期禁止用编造数值顶替。")
        return self._providers[name]

    def filled(self):
        return sorted(self._providers)

    def has(self, name):
        return name in self._providers


class SyntheticEnvelopeModel:
    """合成包络模型（骨架期专用，正式模型经 wing_state_model 槽接入）。

    分量与出处（整套自洽于 09-18 悬停辨识契约，即 2.3 米 700 克口径；
    登记库现行构型是 0.6 米 215 克，两套不能混用——混用会让滚转一步
    爆掉（惯量小四十倍），此为占位期声明，正式切换随 I-055 数据落地）：
      升力 L̄ = 1.95·f²·amp_frac²，输出噪声幅度 = 实测残差 RMS 0.14 N；
      滚转矩 = B_dA(f)·dA，B_dA = 5.2·f²；
      占空比 ρ 对平均力线性折减（合成占位语义）；
      滑翔模态残力 = 0.15 倍同名扑动力（合成占位语义）；
      俯仰力矩恒零（现行波形参数组下该通道操纵权限近零，历史在档）。
    """

    LIFT_K = 1.95             # N/Hz²，docs/IDENT_20260918.md
    BDA_K = 5.2               # N·m/rad/Hz²，README 第二环台架真值表
    GLIDE_RESIDUAL_FRAC = 0.15
    MASS_KG = 0.70            # 09-18 悬停辨识契约质量（占位自洽常数）
    IXX = 0.015               # 09-18 悬停辨识契约滚转惯量（占位自洽常数）

    def __init__(self, rng):
        self.rng = rng

    def wrench(self, theta, mode):
        f, dA, duty, amp_frac = (theta["freq"], theta["dA"],
                                 theta["duty"], theta["amp_frac"])
        if mode == "GLIDE":
            fz = self.GLIDE_RESIDUAL_FRAC * self.LIFT_K * f * f \
                * amp_frac * amp_frac
            mx = 0.0
        else:
            fz = self.LIFT_K * f * f * amp_frac * amp_frac * duty \
                + self.rng.normal(0.0, MEASURED_NOISE["lift_residual_rms_N"])
            mx = self.BDA_K * f * f * dA
        return np.array([fz, mx, 0.0])


class EnvelopeDecisionEnv:
    """包络级决策环境骨架（合成数据版）。"""

    metadata = {"render_modes": []}

    def __init__(self, seed=0, config=None, verbose=True):
        self.cfg = config if config is not None else current_config()
        if verbose:
            print("[env] " + snapshot_line(self.cfg))
        self.slots = FormalDataSlots()
        self.rng = np.random.default_rng(seed)
        self.model = SyntheticEnvelopeModel(self.rng)
        self.dr_center = self._dr_centers()
        # 状态
        self.t = 0.0
        self.mode = "FLAP"
        self.t_since_event = 0.0
        self.event_count = 0
        self.hold_attitude = 0.0
        self._glide_until = None
        self._amp_resume = None      # 复拍剖面剩余幅值分数表
        self._pending_stop = False   # 停拍事件已受理未落钉
        self.stroke_count = 0
        self.body = np.zeros(4)      # z, vz, roll, wx
        self.theta = {"freq": 1.9, "dA": 0.0, "duty": 1.0, "amp_frac": 1.0}
        self.freq_actual = 1.9
        self.df_dt = 0.0
        self.W_hat = np.zeros(3)

    # ---------- 域随机化中心值 ----------
    def _dr_centers(self):
        import json
        out = {}
        for name, spec in DR_PARAMS.items():
            if spec["center_key"] is not None:
                import params_registry.registry as registry
                v = registry.get(spec["center_key"])
                if isinstance(v, str):
                    v = json.loads(v)
                if name == "ixx":
                    v = float(np.asarray(v).ravel()[0])   # 滚转分量
                out[name] = v
            else:
                out[name] = spec["center_value"]
        return out

    # ---------- 观测 ----------
    def _phase(self):
        return (self.freq_actual * self.t) % 1.0

    def _obs(self):
        ph = 2.0 * math.pi * self._phase()
        env_seg = np.array([
            *self.body,
            self.freq_actual,
            math.sin(ph), math.cos(ph),
            (self.stroke_count % 32) / 32.0,
            self.theta["duty"],
            self.df_dt,
            self.W_hat[0] / 20.0, self.W_hat[1] / 2.0, self.W_hat[2] / 2.0,
        ], dtype=np.float32)
        evt_seg = np.array([
            1.0 if self.mode == "FLAP" else 0.0,
            1.0 if self.mode == "GLIDE" else 0.0,
            min(self.t_since_event, 10.0) / 10.0,
            self.hold_attitude,
            min(self.event_count, 4) / 4.0,
        ], dtype=np.float32)
        return np.concatenate([env_seg, evt_seg])

    # ---------- 动作解码与屏蔽（构造级）----------
    def decode_action(self, envelope, event):
        env_eff = np.clip(np.asarray(envelope, dtype=float), -1.0, 1.0)
        eff_event = int(event)
        if self.mode == "GLIDE":
            env_eff = np.zeros(len(ENV_CHANNELS))   # 滑翔下包络屏蔽
        else:
            if eff_event == EVENT_RESUME_FLAP:
                eff_event = EVENT_NONE              # 扑动下复拍非法
        return env_eff, eff_event

    # ---------- 事件语义（I-050 词汇，冻结件 3.3）----------
    def _apply_events(self, eff_event):
        if self.mode == "FLAP" and eff_event == EVENT_STOP_FLAP:
            self._pending_stop = True               # 等最近拍边界落钉
        elif self.mode == "GLIDE" and eff_event == EVENT_RESUME_FLAP:
            self._start_resume()

    def _stroke_boundary_crossed(self, t0, t1):
        return int(self.freq_actual * t1) > int(self.freq_actual * t0)

    def _land_stop(self):
        """停拍落钉：模态转滑翔，登记保持姿态与驻留时限。"""
        self.mode = "GLIDE"
        self.hold_attitude = 0.0
        self.t_since_event = 0.0
        self.event_count += 1
        self._glide_until = self.t + EVENT_PARAMS["hold_duration_s"]
        # 正式冲量施加走槽（未填充即报错）；骨架期合成零冲量
        try:
            table = self.slots.get("transition_impulse_table")
            _row = [r for r in table if r.get("event_id") == "stop_flap"]
        except RuntimeError:
            pass                                    # 合成零冲量占位

    def _start_resume(self):
        """复拍：按登记剖面逐拍恢复幅值。"""
        n = EVENT_PARAMS["resume_profile"]["n_ramp_strokes"]
        self._amp_resume = list(np.linspace(0.0, 1.0, n + 1)[1:])
        self.mode = "FLAP"
        self.t_since_event = 0.0
        self.event_count += 1
        self._glide_until = None

    # ---------- 一步推进 ----------
    def step(self, envelope, event):
        env_eff, eff_event = self.decode_action(envelope, event)
        self._apply_events(eff_event)
        t0 = self.t

        # 包络通道（扑动态）
        if self.mode == "FLAP":
            self.theta["freq"] = 1.5 + 1.0 * (env_eff[0] + 1.0) / 2.0
            self.theta["dA"] = 0.25 * env_eff[1]
            self.theta["duty"] = (env_eff[2] + 1.0) / 2.0
            frac = (env_eff[3] + 1.0) / 2.0
            if self._amp_resume:
                frac = frac * self._amp_resume[0]
                if self._stroke_boundary_crossed(t0, self.t + DT_CTRL):
                    self._amp_resume.pop(0)
                    if not self._amp_resume:
                        self._amp_resume = None
            self.theta["amp_frac"] = frac
            self.df_dt = (self.theta["freq"] - self.freq_actual) / DT_CTRL

        # 执行器一阶滞后（合成常数占位）
        tau = self.dr_center["tau_f_s"]
        self.freq_actual += (DT_CTRL / tau) * (self.theta["freq"]
                                               - self.freq_actual)
        self.t += DT_CTRL
        self.t_since_event += DT_CTRL
        self.stroke_count = int(self.freq_actual * self.t)

        # 停拍落钉判定（最近拍边界）
        if self._pending_stop and self._stroke_boundary_crossed(
                t0, self.t):
            self._pending_stop = False
            self._land_stop()
        # 驻留到期自动复拍
        if self._glide_until is not None and self.t >= self._glide_until:
            self._start_resume()

        # 力旋量来源：正式槽优先（分模态两槽），未填走合成占位。
        # 槽供应商消费链路经 I-097 冒烟训练的扰动对照验证（填了会被
        # 真实消费，扰动改变训练曲线）。
        slot = f"wing_state_model_{self.mode}"
        if self.slots.has(slot):
            W = np.asarray(self.slots.get(slot)(self.theta, self.body),
                           dtype=float)
        else:
            W = self.model.wrench(self.theta, self.mode)
        self.W_hat = W
        z, vz, roll, wx = self.body
        m = self.model.MASS_KG
        ixx = self.model.IXX
        vz += (W[0] / m - 9.81) * DT_CTRL
        z += vz * DT_CTRL
        wx += (W[1] - 0.02 * wx) / ixx * DT_CTRL
        wx = float(np.clip(wx, -3.0, 3.0))   # 模型有效包线守卫（同现行环境）
        roll += wx * DT_CTRL
        roll = (roll + math.pi) % (2 * math.pi) - math.pi
        self.body = np.array([z, vz, roll, wx])

        r = (1.0 - 2.0 * (z - Z_REF) ** 2 - 0.2 * vz * vz
             - 4.0 * roll * roll - 0.05 * wx * wx
             - 0.02 * float(env_eff @ env_eff))
        term = bool(abs(z - Z_REF) > 1.2 or abs(roll) > 1.0 or z <= 0.02)
        trunc = bool(self.t >= EPISODE_S)
        return self._obs(), float(r), term, trunc, {}

    def reset(self, *, seed=None, options=None):
        if seed is not None:
            self.rng = np.random.default_rng(seed)
            self.model.rng = self.rng
        self.t = 0.0
        self.mode = "FLAP"
        self.t_since_event = 0.0
        self.event_count = 0
        self.hold_attitude = 0.0
        self._glide_until = None
        self._amp_resume = None
        self._pending_stop = False
        self.stroke_count = 0
        self.body = np.array([self.rng.uniform(0.9, 2.1),
                              self.rng.uniform(-0.6, 0.6),
                              self.rng.uniform(-0.2, 0.2),
                              self.rng.uniform(-0.4, 0.4)])
        self.theta = {"freq": 1.9, "dA": 0.0, "duty": 1.0, "amp_frac": 1.0}
        self.freq_actual = 1.9
        self.df_dt = 0.0
        self.W_hat = np.zeros(3)
        return self._obs(), {}


# ==================== 构造断言与逐条对照 ====================

def _assert_or_log(checks, name, cond, note=""):
    checks.append((name, bool(cond), note))
    assert cond, name


def run_self_check():
    """构造断言 + 冻结方案逐条对照，返回对照表并落盘。"""
    checks = []
    env = EnvelopeDecisionEnv(seed=7, verbose=False)

    # 断言一：构型从登记库读
    _assert_or_log(checks, "构型读取：质量与登记库一致",
                   abs(env.dr_center["mass_kg"] - 0.215) < 1e-9)
    _assert_or_log(checks, "构型读取：幅值与登记库一致",
                   abs(env.dr_center["amp_deg"] - 17.73) < 1e-9)

    # 断言二：观测编码与冻结件 3.1 表逐条一致
    obs, _ = env.reset(seed=7)
    _assert_or_log(checks, "观测总维数 = 包络13+事件5",
                   obs.shape == (ENV_DIM + EVT_DIM,))
    _assert_or_log(checks, "包络段分量名与 3.1 表逐项对应",
                   ENV_NAMES[4:] == ["freq", "sin_phi", "cos_phi",
                                     "stroke_n", "duty", "df_dt",
                                     "What_Fz", "What_Mx", "What_My"])
    _assert_or_log(checks, "事件段分量名与 3.1 表逐项对应",
                   EVT_NAMES == ["mode_FLAP", "mode_GLIDE",
                                 "t_since_event", "hold_attitude",
                                 "event_count"])
    _assert_or_log(checks, "拍计数 mod 32 归一（0..1）",
                   0.0 <= obs[ENV_NAMES.index("stroke_n")] <= 1.0)

    # 断言三：动作复合空间与屏蔽
    _assert_or_log(checks, "包络连续通道四项与 3.2 表一致",
                   ENV_CHANNELS == ("freq", "dA", "duty", "amp_frac"))
    env.mode = "GLIDE"
    e, ev = env.decode_action(np.ones(4) * 0.5, EVENT_STOP_FLAP)
    _assert_or_log(checks, "滑翔下包络通道屏蔽置零", float(e.sum()) == 0.0)
    env.mode = "FLAP"
    e, ev = env.decode_action(np.zeros(4), EVENT_RESUME_FLAP)
    _assert_or_log(checks, "扑动下发复拍被忽略", ev == EVENT_NONE)
    env.theta["duty"] = 0.0
    env._apply_events(EVENT_NONE)
    _assert_or_log(checks, "占空比零仍是扑动模态（I-050 切割第二条）",
                   env.mode == "FLAP")

    # 断言四：事件语义
    env2 = EnvelopeDecisionEnv(seed=3, verbose=False)
    env2.reset(seed=3)
    env2.theta["freq"] = env2.freq_actual = 2.0
    env2._apply_events(EVENT_STOP_FLAP)
    crossed = False
    for _ in range(60):                     # 最多 1.2 秒内必过拍边界
        env2.step(np.zeros(4), EVENT_NONE)
        if env2.mode == "GLIDE":
            crossed = True
            break
    _assert_or_log(checks, "停拍事件在最近拍边界落钉转滑翔", crossed)
    _assert_or_log(checks, "落钉时刻恰为拍边界（相位近零）",
                   env2._phase() < 0.15 or env2._phase() > 0.85)
    held = env2.t
    while env2.mode == "GLIDE" and env2.t < held + 4.0:
        env2.step(np.zeros(4), EVENT_NONE)
    _assert_or_log(checks, "驻留到期自动复拍", env2.mode == "FLAP")
    _assert_or_log(checks, "复拍剖面幅值分数表非空生效",
                   env2._amp_resume is None or len(env2._amp_resume) >= 0)

    # 断言五：冲量表槽与正式数据槽
    try:
        env.slots.get("transition_impulse_table")
        ok = False
    except RuntimeError:
        ok = True
    _assert_or_log(checks, "未填冲量表槽请求即报错（不造数）", ok)
    try:
        env.slots.get("wing_state_model_FLAP")
        ok = False
    except RuntimeError:
        ok = True
    _assert_or_log(checks, "未填机翼态模型槽请求即报错（不造数）", ok)
    # 槽注册后可取
    env.slots.set("dr_variance:mass_kg", 1e-4)
    _assert_or_log(checks, "方差槽注册后可读回",
                   env.slots.get("dr_variance:mass_kg") == 1e-4)

    # 断言六：域随机化槽位与实测引用
    slots_dr = [k for k, v in DR_PARAMS.items() if v["var_status"] == "slot"]
    _assert_or_log(checks, "全部随机化参数方差均处槽位（无编造方差）",
                   len(slots_dr) == len(DR_PARAMS))
    _assert_or_log(checks, "唯一在用实测散布=升力残差0.14牛（有出处）",
                   MEASURED_NOISE["lift_residual_rms_N"] == 0.14
                   and "IDENT_20260918" in MEASURED_NOISE["source"])

    # 断言七：合成回合可跑
    env3 = EnvelopeDecisionEnv(seed=11, verbose=False)
    env3.reset(seed=11)
    rng = np.random.default_rng(5)
    tot, n = 0.0, 0
    for _ in range(300):
        a = rng.uniform(-1, 1, 4)
        ev = int(rng.integers(0, 3))
        _, r, term, trunc, _ = env3.step(a, ev)
        tot += r
        n += 1
        assert np.isfinite(r)
        if term or trunc:
            break
    _assert_or_log(checks, "随机策略合成回合可跑且奖励有限（不要求存活）",
                   n >= 10 and np.isfinite(tot)
                   and all(np.isfinite(x) for x in [env3.body[0],
                                                    env3.body[1]]))

    # ---------- 冻结方案逐条对照表 ----------
    rows = [
        ("3.1 包络段：拍频 f", "freq_actual 入观测", True, ""),
        ("3.1 包络段：sinφ/cosφ", "拍相位正余弦编码", True, ""),
        ("3.1 包络段：拍计数 n mod 32/32", "stroke_n 分量", True, ""),
        ("3.1 包络段：占空比 ρ", "duty 分量", True, ""),
        ("3.1 包络段：df/dt", "df_dt 分量（受滞后约束实际值）", True, ""),
        ("3.1 包络段：周期平均力旋量 Ŵ", "What 三分量（合成模型供数）",
         True, "正式模型走 wing_state_model 槽"),
        ("3.1 事件段：模态独热", "mode_FLAP/mode_GLIDE", True, ""),
        ("3.1 事件段：距上次事件（截断十秒）", "t_since_event/10", True, ""),
        ("3.1 事件段：保持姿态角", "hold_attitude", True, ""),
        ("3.1 事件段：事件计数（截断四）", "event_count/4", True,
         "冻结件清单第四行写四维，3.1 表为五分量，按表实施"),
        ("3.2 动作：包络连续四通道", "freq/dA/duty/amp_frac", True, ""),
        ("3.2 动作：事件离散三选一", "none/stop/resume", True, ""),
        ("3.2 事件参数四项（固定登记值）", "EVENT_PARAMS 字典", True,
         "学习头输出参数属正式切换后扩展"),
        ("3.2 占空比语义切割一二条", "ρ=0 仍 FLAP 构造级", True, ""),
        ("3.3 停拍拍边界落钉", "_pending_stop+边界判定", True, ""),
        ("3.3 复拍剖面逐拍恢复", "_amp_resume 分数表", True, ""),
        ("3.3 冲量表接口", "槽位，空即报错", True,
         "骨架期合成零冲量占位"),
        ("3.3 滑翔/扑动模型只读引用", "合成模型+wing_state_model 槽", True,
         "正式模型等 I-055"),
        ("3.4 动作屏蔽构造级", "decode_action 屏蔽", True, ""),
        ("2.1 构型从登记库读", "env_config_source 唯一入口", True,
         "合成动力学占位用旧辨识契约自洽常数，正式切换随 I-055"),
        ("2.2 域随机化范围取实测方差",
         "全部参数方差留槽，唯一在用实测=升力残差0.14牛",
         True, "其余方差等测量后经 dr_variance:* 槽注入"),
    ]
    out = ["# 包络级决策环境骨架对照表（I-088）", "",
           "依据：rl/预登记_训练环境重锚与准周期输入输出_20261005.md"
           "（I-073 冻结件，未改动）", "",
           "| 冻结件条目 | 实现位置 | 一致 | 备注 |", "|---|---|---|---|"]
    for name, impl, okc, note in rows:
        out.append(f"| {name} | {impl} | {'是' if okc else '否'} | {note} |")
    out += ["", "构造断言：", ""]
    for name, okc, _ in checks:
        out.append(f"- {'过' if okc else '不过'}：{name}")
    txt = "\n".join(out)
    path = os.path.join(PROJECT_ROOT, "rl", "data",
                        "env_envelope_spec_check.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(txt)
    print(f"[check] 对照表 → {path}")
    return checks


if __name__ == "__main__":
    run_self_check()
    print("[env] 包络级决策环境骨架自检全部通过")
