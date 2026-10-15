# -*- coding: utf-8 -*-
"""
hover_env_v3_scaffold.py — 准周期框架训练环境脚手架（I-073）
==============================================================
状态：脚手架。只实现编码接口与状态机，不实现物理施加层；
物理层等 I-055 的机翼态灰箱辨识数据。占位路径一律显式抛错，
不编造任何数值。方案出处：rl/预登记_训练环境重锚与准周期输入输出_20261005.md
（预登记冻结前本文件随草案可改）。

三类构造级保证：
1. 构型从参数登记库读现行值（rl/env_config_source.py 唯一入口），
   缺键即抛错，无默认值；
2. 动作屏蔽在解码层消灭非法组合：滑翔模态下包络连续通道被忽略
   并置中性值，扑动模态下发复拍事件被忽略；
3. 冲量表接口读到空数据即报"等 I-055"，不造数。

自检：python rl/hover_env_v3_scaffold.py 跑五条构造断言。
"""
import math
import os
import sys

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rl.env_config_source import current_config, snapshot_line

DT_CTRL = 0.02          # 控制周期 50Hz，与 V2 一致
EPISODE_S = 10.0
HOLD_ATTITUDE_NEUTRAL = 0.0   # 停拍钉定的中性保持角（弧度）

# 观测两段的维度（与预登记第三节一致，改动须走预登记流程）
ENV_OBS_DIM = 13      # 拍动周期包络段：z,vz,roll,wx,f,sinφ,cosφ,n,ρ,dfdt,Ŵ(3)
EVT_OBS_DIM = 4       # 模态事件段：FLAP,GLIDE,距上次事件,保持姿态角

# 事件词汇（I-050 冻结出处 ltv/hybrid_flight_spec.json）
EVENT_NONE = 0
EVENT_STOP_FLAP = 1
EVENT_RESUME_FLAP = 2


class QuasiPeriodicActionSpace:
    """复合动作空间：包络连续四通道 + 事件离散三选一。

    学习框架侧用 gymnasium.spaces.Dict 等价定义；本类只做脚手架内
    解码，避免引入对具体学习库版本的依赖。
    """

    ENV_CHANNELS = ("freq", "dA", "duty", "amp_frac")
    N_ENV = 4

    def decode(self, envelope, event):
        """解复合动作为命令对象，动作屏蔽在此层实现。

        envelope: shape (4,) 连续包络通道；event: 0/1/2 离散事件。
        返回 dict(mode_cmd, envelope_effective, event, event_params)。
        """
        env_eff = np.clip(np.asarray(envelope, dtype=float), -1.0, 1.0)
        return {"envelope": env_eff, "event": int(event)}


class ImpulseTableInterface:
    """过渡冲量表接口（I-050 十五列格式）。

    表数值是测量资产，等 I-055；读到空表即抛错，不造数。
    """

    def __init__(self, table=None):
        self._table = table or []

    def lookup(self, event_id):
        if not self._table:
            raise RuntimeError(
                "过渡冲量表为空：冲量数值等 I-055 的测量数据，"
                "脚手架阶段不许编造冲量。")
        for row in self._table:
            if row.get("event_id") == event_id:
                return row
        raise KeyError(f"冲量表缺事件 {event_id}")


class FlapperQuasiPeriodicEnv:
    """准周期框架环境架子。

    与 V2 的关系：观测动作接口全新（两段观测 + 复合动作），动力学
    施加层留空。构造时从登记库读构型并打印快照。
    """

    def __init__(self, config=None, verbose=True):
        self.cfg = config if config is not None else current_config()
        if verbose:
            print("[env_v3] " + snapshot_line(self.cfg))
            print("[env_v3] 脚手架模式：接口测试专用，"
                  "I-055 数据落地前不得用于训练结论")
        self.action_space = QuasiPeriodicActionSpace()
        self.impulse_table = ImpulseTableInterface()
        # 模态状态机（I-050 模态层：FLAP/GLIDE，事件驱动转移）
        self.mode = "FLAP"
        self.hold_attitude = HOLD_ATTITUDE_NEUTRAL
        self.t_since_event = 0.0
        self.event_count = 0
        # 拍动包络状态
        self.t = 0.0
        self.freq_actual = 1.9
        self.stroke_count = 0
        self.duty = 1.0
        self.df_dt = 0.0
        self.last_wrench_est = np.zeros(3)
        # 机体状态占位：z,vz,roll,wx（数值初始化仅接口用，无物理含义）
        self.body_state = np.zeros(4)

    # ---------------- 观测编码 ----------------
    def _phase(self):
        return (self.freq_actual * self.t) % 1.0

    def _obs_envelope(self):
        """拍动周期包络段（11 维）。"""
        ph = 2.0 * math.pi * self._phase()
        w = self.last_wrench_est
        return np.concatenate([
            self.body_state,                        # z,vz,roll,wx
            [self.freq_actual],
            [math.sin(ph), math.cos(ph)],
            [(self.stroke_count % 32) / 32.0],      # 拍计数归一化
            [self.duty],
            [self.df_dt],
            [w[0] / 20.0, w[1] / 2.0, w[2] / 2.0],  # 力旋量估计压缩
        ]).astype(np.float32)

    def _obs_event(self):
        """模态事件段（4 维）。"""
        return np.array([
            1.0 if self.mode == "FLAP" else 0.0,
            1.0 if self.mode == "GLIDE" else 0.0,
            min(self.t_since_event, 10.0) / 10.0,
            self.hold_attitude,
        ], dtype=np.float32)

    def observe(self):
        return np.concatenate([self._obs_envelope(), self._obs_event()])

    # ---------------- 动作解码与屏蔽 ----------------
    def apply_action(self, envelope, event):
        """复合动作解码。屏蔽规则在此实现（构造级，不靠奖励）。"""
        dec = self.action_space.decode(envelope, event)
        if self.mode == "FLAP":
            if dec["event"] == EVENT_RESUME_FLAP:
                dec["event"] = EVENT_NONE    # 扑动下发复拍=非法，解码层忽略
            self._flap_envelope_step(dec["envelope"])
        else:
            # 滑翔下包络通道整体屏蔽，置中性值
            dec["envelope"] = np.zeros(QuasiPeriodicActionSpace.N_ENV)
        if self.mode == "FLAP" and dec["event"] == EVENT_STOP_FLAP:
            self._do_stop_flap()
        elif self.mode == "GLIDE" and dec["event"] == EVENT_RESUME_FLAP:
            self._do_resume_flap()
        return dec

    def _flap_envelope_step(self, env_eff):
        """包络连续通道的接口层推进（物理施加等 I-055，这里只走账面）。"""
        self.duty = (env_eff[2] + 1.0) / 2.0     # [-1,1]→[0,1]
        self.df_dt = float(env_eff[0])
        # 占空比语义（I-050 切割第一、二条）：ρ=0 仍是 FLAP，不进滑翔
        self.mode = "FLAP"

    def _do_stop_flap(self):
        """停拍事件：真实执行需拍边界落钉+冲量表，脚手架只做状态机侧。"""
        self.mode = "GLIDE"
        self.t_since_event = 0.0
        self.event_count += 1
        # 冲量施加是测量依赖项，调用即抛错：
        # self.impulse_table.lookup("stop_flap_default")

    def _do_resume_flap(self):
        """复拍事件：剖面参数取 I-050 词汇，幅值恢复物理等 I-055。"""
        self.mode = "FLAP"
        self.t_since_event = 0.0
        self.event_count += 1

    def step(self, envelope, event):
        """完整步接口。物理积分未实现——走到需要物理的一步就抛错。"""
        dec = self.apply_action(envelope, event)
        self.t += DT_CTRL
        self.t_since_event += DT_CTRL
        self.stroke_count = int(self.freq_actual * self.t)
        raise NotImplementedError(
            "物理施加层等 I-055 机翼态灰箱辨识数据；"
            "脚手架只保证到动作解码与模态状态机。")


def _self_check():
    """五条构造断言（预登记第四节交付物 3）。"""
    env = FlapperQuasiPeriodicEnv(verbose=False)
    # 断言一：登记库读取一致性
    assert abs(env.cfg["mass_kg"] - 0.215) < 1e-9, env.cfg["mass_kg"]
    assert abs(env.cfg["span_m"] - 0.32) < 1e-9
    assert abs(env.cfg["amplitude_deg"] - 17.73) < 1e-9
    # 断言二：观测分段形状
    o = env.observe()
    assert o.shape == (ENV_OBS_DIM + EVT_OBS_DIM,), o.shape
    assert env._obs_envelope().shape == (ENV_OBS_DIM,)
    assert env._obs_event().shape == (EVT_OBS_DIM,)
    # 断言三：事件解码语义（扑动下发停拍→模态切换）
    env.apply_action(np.zeros(4), EVENT_STOP_FLAP)
    assert env.mode == "GLIDE" and env.event_count == 1
    # 断言四：动作屏蔽（滑翔下包络通道被置中性，复拍在扑动下被忽略）
    dec = env.apply_action(np.ones(4) * 0.7, EVENT_STOP_FLAP)
    assert np.all(dec["envelope"] == 0.0)
    env.mode = "FLAP"
    dec = env.apply_action(np.zeros(4), EVENT_RESUME_FLAP)
    assert dec["event"] == EVENT_NONE
    # 断言五：冲量表空表即报错（不造数）
    try:
        env.impulse_table.lookup("stop_flap_default")
        raise AssertionError("空冲量表应当抛错")
    except RuntimeError:
        pass
    # 占空比语义：ρ 压到零仍是 FLAP
    env._flap_envelope_step(np.array([0.0, 0.0, -1.0, 0.0]))
    assert env.mode == "FLAP" and env.duty == 0.0
    print("[self-check] 五条构造断言全部通过")


if __name__ == "__main__":
    _self_check()
