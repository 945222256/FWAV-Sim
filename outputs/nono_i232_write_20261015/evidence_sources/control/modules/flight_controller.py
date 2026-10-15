# -*- coding: utf-8 -*-
"""
flight_controller.py — 姿态串级 PID + TECS(高度/速度) + 位置保持
================================================================
结构（SparrowHawk V.B 的仿真版）：
  位置外环: (xr,yr) 误差 → 期望速度 → 期望 pitch/roll（小角度）
  TECS: 高度误差 → 拍频（升力∝f²）；速度误差 → 加速度需求 → 前向力需求
        → 俯仰角（∂Fx/∂θ 实测 8.9 N/rad + 速度阻尼前馈 3.17 N/(m/s)，
        悬停配平前倾 11.8° 内置）
  姿态外环: 角度误差 → 期望角速度
  角速度内环: 角速度误差 → 期望力矩 → /舵效增益 → 三路尾舵

舵效调度（2026-09-19 台架真值，docs/IDENT_20260919.md）：
  尾翼 B(v) = k·v_air²（动压平方律，k_elev/k_rud=0.0290，k_ail=0.00965）；
  v_air < 1 m/s 时尾舵无权限，通道关闭（静气悬停物理事实）。
  翼差动 B_dA(f) = 5.2·f²，滚转 PD 输出按 B_DA_REF/B_dA(f) 归一，
  保持环路增益不随拍频变化（K_DA 是在悬停点整定的）。
"""
import math
import os
import sys
import time

import numpy as np

import config.sim_config as cfg


def _wrap(a):
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def _euler(quat):
    w, x, y, z = quat
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return roll, pitch, yaw


def _os_env(key, default=""):
    """L1 领地内直接读环境变量的唯一入口（I-019 同款绕行：通用增益桥
    对非单一浮点语义的键会静默失败，辨识类原始串/幅值键走此函数）。"""
    import os
    return os.environ.get(key, default)


_MS_TREF = 1.0 / 1.95   # 名义拍周期，相位跳变量化基准

def multisine(f, ch, t):
    """多正弦激励信号（辨识产线第二步）：对数频率网格 0.1~20Hz，
    施罗德相位错开通道。归一化到单位幅值，由调用侧乘通道幅值。
    MULTISINE_FREQS 为逗号分隔频率表（Hz）。
    I-019 绕行（2026-09-30）：通用增益桥按单一浮点解析逗号串会静默
    失败，此处直接读原始环境变量恢复完整语义；桥修复后两条路径等价。"""
    import math as _m
    import os as _os
    _raw = _os.environ.get("FLAPPER_MULTISINE_FREQS")
    if _raw:
        freqs = [float(x) for x in _raw.split(",") if x.strip()]
    else:
        freqs = [float(x) for x in str(
            getattr(f, "MULTISINE_FREQS", "0.2,0.5,1.0,1.5,2.33,3.0,5.0,8.0")
        ).split(",") if x.strip()]
    # 逐拍相位跳变（奇偶混叠处置）：注入相位按名义拍周期整量化计数
    # 累进跳变，使估计半与验证半见到同族注入相位；跳变率经环境键
    # 给出，默认零=历史行为；控制器与估计器重构端用同一公式复制
    _hop = float(_os.environ.get("FLAPPER_MS_HOP", "0") or 0)
    _ph_hop = _hop * int(t / _MS_TREF) if _hop != 0.0 else 0.0
    n = len(freqs)
    ch_idx = {"ail": 0, "elev": 1, "rud": 2, "freq": 3, "da": 4}.get(ch, 0)
    s = 0.0
    for i, fr_q in enumerate(freqs):
        ph = (3.14159265 * (i * (i + 1) // 2) / n + ch_idx * 0.7
              + _ph_hop)
        s += _m.sin(2 * 3.14159265 * fr_q * t + ph)
    return s / (0.5 * n)


def _quat_mul(a, b):
    """四元数乘 (w,x,y,z)。"""
    aw, ax, ay, az = a; bw, bx, by, bz = b
    return np.array([
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw])


def _quat_conj(a):
    return np.array([a[0], -a[1], -a[2], -a[3]])


def _euler2quat(roll, pitch, yaw):
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return np.array([
        cr * cp * cy + sr * sp * sy,
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy])


def _freq_diff_clamp(df, freq, f):
    """频率差指令的每翼硬界裁剪（纯函数，供单元测直接覆盖）。

    约束区间 [lo, hi]：每翼频率 f±δf 均须在 [FREQ_DIFF_FLO,
    FREQ_DIFF_FHI] 内且 |δf|≤FD_MAX。集体拍频越界时区间倒置，
    np.clip 会静默取域外端点（T5 抓获的缺陷形态），此时无差动
    权限，返回 0。
    """
    lo = max(-f.FD_MAX, f.FREQ_DIFF_FLO - freq, freq - f.FREQ_DIFF_FHI)
    hi = min(+f.FD_MAX, f.FREQ_DIFF_FHI - freq, freq - f.FREQ_DIFF_FLO)
    if lo > hi:
        return 0.0
    return float(np.clip(df, lo, hi))


class FlightController:
    def __init__(self):
        f = cfg.FlightControllerConfig
        self.zr, self.xr, self.yr = f.Z_REF, f.X_REF, f.Y_REF
        self.yaw_ref = math.radians(f.YAW_REF_DEG)
        # 高度环基准拍频必须用辨识的悬停频率 1.88~1.90Hz（台架 L̄=1.95f² +
        # 配平前倾的升力余弦损失）：用 2.5Hz 名义值会让环路停在 z+0.46m
        # 的静差点（闭环实测证实）
        self.f0 = f.FREQ_HOVER
        # 起始即为配平拍频：软启动斜坡从悬停值开始上升，而不是从最低 1.5Hz 开始——
        # 1.5Hz 升力 4.4N < 体重 6.87N，从该值起动必然先下沉、触发 DP/高度环
        # 全力推油门的弹射序列（泵能回路的第一环节，2026-09-19 三组在环实录）
        self._freq_last = f.FREQ_HOVER
        # 逐拍差动状态（2026-09-27 冲量积分线）：拍边界计时器与两翼闸门
        self._sd_t_last = None
        self._sd_gate_L = 1.0
        self._sd_gate_R = 1.0
        # 占空比调制累加器与频率差指令（2026-09-27 阶段二，默认关）
        self._dm_acc_L = 0.0
        self._dm_acc_R = 0.0
        self._df_cmd = 0.0
        self._t_last = None
        self._pd_last = None                    # pitch_des 斜坡状态
        self._xi = 0.0                          # 位置误差积分（消静差）
        self._wavegen_state = None              # Hopf 发生器状态（WAVEGEN_ON
                                                # 时使用，I-172 接入；None=
                                                # 未初始化，关闭态不消费）
        self._w_filt = None                     # 内环角速率低通状态（防拍频泵能）
        self._wx_roll_lpf = None                # 滚转速率反馈专用低通状态
                                                # （ROLL_FILT_TAU>0 时启用）
        self._da_lpf = None                     # 滚转执行量低通状态（dA/ail，
                                                # R3 教训：wx 5.73Hz 纹波经
                                                # τ0.030 滤波直通反馈，把满幅
                                                # 修正打成 3.7Hz 换边极限环，
                                                # 平均力矩归零机体钉死）
        self._pv_filt = None                    # 外环速度 EWMA（2f 相干激励压制）
        self._zi = 0.0                          # 巡航高度误差积分（消稳态高度偏差/平衡锁）
        self._pitch_i = 0.0                     # 俯仰角误差积分（第14轮：消比例内环静差）
        self._roll_i = 0.0                      # 滚转误差慢积分（转弯线 R25，
                                                # ROLL_KI>0 时启用）
        self._wx_acc = 0.0                      # 拍周期角速率累计器（逐拍
                                                # 决策用拍平均 wx，2026-09-29
                                                # 转弯线 R36/R37 教训：瞬时
                                                # 滤波值要么被纹波淹没要么
                                                # 滞后毁阻尼）
        self._wx_acc_n = 0
        self._beta_i = 0.0                      # 侧滑静差积分（转弯线 R49，
                                                # COORD_KI>0 时启用）
        self._ei = 0.0                          # 总比能误差积分（真·TECS 拍频通道）
        self._vzi = 0.0                         # 垂直速度误差积分（第十一班结构更换）
        self.alpha_ff_last = 0.0                # 尾翼攻角前馈估计（第11轮对照列）
        # ── 两段状态机（任务三，2026-09-25）：ENABLED=False 时走下方原
        # 单段路径，本属性恒为 None，行为与旧版逐位一致 ──
        self._fsm = None
        _fsm_cfg = getattr(cfg, "FlightStateMachineConfig", None)
        if _fsm_cfg is not None and _fsm_cfg.ENABLED:
            from modules.flight_state_machine import FlightStateMachine
            self._fsm = FlightStateMachine()
            print("[FSM] 两段状态机启用：LAUNCH(锁定律/拍频锁 1.25) "
                  "→ 2s 斜坡 → CRUISE(清单v2+任务二增益)", flush=True)

    def update(self, pose, vel, t_sim=None, *, motion_context=None):
        """pose=[x,y,z,qw,qx,qy,qz], vel=[vx,vy,vz,wx,wy,wz] → 控制输出。

        t_sim：仿真时间（秒）。传入时 dt 用仿真时间差分——被控对象按
        仿真时间演化，墙钟 time.time() 在 RTF≠1 时让滤波/斜坡/积分的
        有效增益漂移、行为不可复现（另一会话独立分析指出，2026-09-20 修）。
        不传时退回墙钟（兼容旧调用）。
        """
        if motion_context is not None:
            from modules.motion_action.types import deep_copy_mapping
            self.motion_context = deep_copy_mapping(dict(motion_context))
            if motion_context.get("native_hopf") != "unsupported":
                raise NotImplementedError("native rhythm ownership is not implemented")
        f = cfg.FlightControllerConfig
        p = np.asarray(pose[0:3], dtype=float)
        roll, pitch, yaw = _euler(pose[3:7])
        v = np.asarray(vel[0:3], dtype=float)
        w = np.asarray(vel[3:6], dtype=float)

        # ── 0. 空速 + 滑流（动压调度的输入）──
        # 尾翼有效动压 v_eff² = v_air² + v_slip²：悬停时拍动滑流仍为尾翼
        # 提供动压（真机物理），前飞时以空速为主。freq 在本步第 1 节才更新，
        # 调度用上一步的拍频即可（环路内一阶滞后无害）。
        if getattr(cfg, "WIND_ENABLED", False):
            v_air = float(np.linalg.norm(
                v - np.asarray(cfg.WIND_VECTOR, dtype=float)))
        else:
            v_air = float(np.linalg.norm(v))
        if getattr(cfg, "ENABLE_TAIL_SLIPSTREAM", False):
            _v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * self._freq_last
            # 2026-09-19：与尾翼模型的合流口径对齐（正交合成）。原线性
            # 相加 v_air+v_slip 在空速大时虚高 1.6 倍以上——调度增益虚大
            # → 升降舵给小 → 速度摆动中俯仰环增益塌陷、姿态失守（在环
            # 231823 run vx 冲 46m/s 的机理）。悬停 v_air≈0 时两者等价
            v_eff = float(np.sqrt(v_air * v_air + _v_slip * _v_slip))
        else:
            v_eff = v_air
        # 2026-09-20 调度 v_eff 封顶：尾翼模型的 a_tail 在失速角被
        # clip，高速时真实增量舵效归零，而 v_eff² 外推的调度却指数
        # 爆炸（veff=45 时信成 59 N·m/rad）→ 归一化把舵令压到 0.001rad、
        # 环在漂移中瘫软（002239 run 全量记录实录）。封顶 8m/s 让
        # 舵令始终在已验证量程内
        v_eff = min(v_eff, 8.0)
        self._v_eff_last = float(v_eff)   # 协调转弯率前馈分母用
        # 尾翼攻角前馈用的滑流速度（第5轮；调度封顶不影响物理尾流）
        self._slip_ff = (cfg.SLIP_K * cfg.SLIP_V_PER_HZ * self._freq_last
                         if getattr(cfg, "ENABLE_TAIL_SLIPSTREAM", False)
                         else 0.0)

        # ── 0b. 外环反馈（2026-09-20 配套相位积分修复启用 EWMA）──
        # 相位改积分状态后力变成相干激励，KD_Z 若吃 2f 纹波 vz 会经快斜
        # 坡把拍频调制在 2f 上=参数泵（实测 vx ±94m/s）。τ=0.15 压 2f。
        if t_sim is not None:
            dt = 0.0083 if self._t_last is None else max(1e-3, min(0.1, float(t_sim) - self._t_last))
            self._t_last = float(t_sim)
        else:
            now = time.time()
            dt = 0.0083 if self._t_last is None else max(1e-3, min(0.1, now - self._t_last))
            self._t_last = now
        # 俯仰反馈互补滤波（2026-09-30 守门轮四：轮三定案欧拉运动学
        # 耦合污染俯仰读数、一阶低通首修因相位滞后更差（R60 负结果
        # 留档）。互补滤波：高频段置信体轴俯仰率积分（无欧拉耦合、
        # 无相位滞后），低频段置信欧拉角（拉回积分漂移）。
        # pitch_cf = α·pitch_euler + (1−α)·(pitch_cf + wy·dt)，
        # α=dt/(τ+dt)；PITCH_FB_FILT_TAU>0 启用（语义=互补时间常数），
        # 默认 0=直读欧拉=历史行为。
        _tau_pfb = getattr(f, "PITCH_FB_FILT_TAU", 0.0)
        if _tau_pfb > 0.0:
            if getattr(self, "_pfb_filt", None) is None:
                self._pfb_filt = pitch
            _a_pfb = dt / (_tau_pfb + dt)
            self._pfb_filt = (1.0 - _a_pfb) * (self._pfb_filt
                                                + w[1] * dt)                 + _a_pfb * pitch
            pitch = float(np.clip(self._pfb_filt, -1.4, 1.4))
        if self._pv_filt is None:
            self._pv_filt = np.asarray(v, dtype=float).copy()
        else:
            _b = min(1.0, dt / max(f.OUTER_FILT_TAU, 1e-3))
            self._pv_filt = self._pv_filt + _b * (np.asarray(v, dtype=float) - self._pv_filt)
        pf, vf = p, self._pv_filt
        # ── 0b. 速度体轴重建（2026-09-29 转弯线 R14~R25 总根因修复）──
        # 历史 vf=世界系滤波速度被全部下游当体轴使用：尾翼攻角前馈
        # _vbx=cos(pitch)·vx_world+…（只转 pitch、假设 yaw=roll=0）。
        # 直线巡航（yaw≈0）近似成立掩盖十年；盘旋中机头转过 ~90° 时
        # 世界 vx→0，_vbx 被 clamp(·,0.1) 兜底，攻角前馈发散，升降舵
        # 满偏换边，俯仰崩盘拖垮全机——十轮在环实录崩点全部绑定
        # 轨迹角 χ≈-80°~-90°（vx=V·cosχ 塌零）即此。按坐标系纪律
        # 重建：世界滤波速度经姿态四元数转体轴，导航量（pf/vyd）仍
        # 用世界系。FLAPPER_LEGACY_VF=1 时保留旧世界系语义（对照用）。
        if not getattr(cfg, "LEGACY_VF_WORLD", getattr(
                f, "LEGACY_VF_WORLD", False)):
            _qw, _qx, _qy, _qz = pose[3], pose[4], pose[5], pose[6]
            # v_body = R^T · v_world（R=四元数旋转矩阵）
            _r00 = 1 - 2 * (_qy * _qy + _qz * _qz)
            _r01 = 2 * (_qx * _qy - _qw * _qz)
            _r02 = 2 * (_qx * _qz + _qw * _qy)
            _r10 = 2 * (_qx * _qy + _qw * _qz)
            _r11 = 1 - 2 * (_qx * _qx + _qz * _qz)
            _r12 = 2 * (_qy * _qz - _qw * _qx)
            _r20 = 2 * (_qx * _qz - _qw * _qy)
            _r21 = 2 * (_qy * _qz + _qw * _qx)
            _r22 = 1 - 2 * (_qx * _qx + _qy * _qy)
            _vw = self._pv_filt
            vf = np.array([
                _r00 * _vw[0] + _r10 * _vw[1] + _r20 * _vw[2],
                _r01 * _vw[0] + _r11 * _vw[1] + _r21 * _vw[2],
                _r02 * _vw[0] + _r12 * _vw[1] + _r22 * _vw[2]])
            self._vf_body_last = vf.copy()

        # ── 0c. 接地联锁（2026-09-20 增益调整配合）：低空低速判定为已落地——
        # 全部执行器回怠速/中立，积分器冻结（0920_123505 坠地后拍频
        # 5.5Hz 空转 22s，尾段数据全部失真）。怠速经斜坡平滑过渡。
        if pf[2] < f.GROUND_Z and abs(vf[2]) < 0.5 and abs(vf[0]) < 2.0:
            self._pitch_tgt_filt = None   # 坠地清零（第十班）
            freq = float(np.clip(cfg.MANUAL_FREQ_MIN,
                                 self._freq_last - f.FREQ_SLEW * dt,
                                 self._freq_last + f.FREQ_SLEW * dt))
            self._freq_last = freq
            return {"freq": freq, "elevator": 0.0, "aileron": 0.0,
                    "rudder": 0.0, "dA": 0.0, "vxd": 0.0,
                    "v_eff": v_eff, "wyd": 0.0, "my_dem": 0.0,
                    "alpha_ff": getattr(self, "alpha_ff_last", 0.0),
                    "roll": roll, "pitch": pitch, "roll_des": 0.0,
                    "pitch_des": 0.0}

        # ── 0d. 两段状态机（任务三，2026-09-25）：每拍推进状态/计数/斜坡 ──
        # 反馈读数用控制器自身口径：vx 取 EWMA 滤波值（与各环路同源），
        # z/pitch 取位姿直读。接地联锁（0c）优先：坠地后状态机停拍。
        _fsm = self._fsm
        if _fsm is not None:
            _fsm.tick(dt, vf[0], pf[2], pitch)
            if _fsm.armed_now:
                # 锁定律激活（发射释放）当拍：pitch_des 斜坡状态同步到当前
                # 实际俯仰，消除释放瞬间的指令阶跃（发射姿态同步后锚点
                # ≈0.436，同步后误差从零起步）
                self._pd_last = float(pitch)
                self._pitch_tgt_filt = None

        # ── 1. TECS 高度通道（悬停口径；巡航口径在节 2C 重配对并跳过本节）──
        _cruise = float(getattr(f, "CRUISE_VX", 0.0))
        freq = self._freq_last
        if _fsm is not None and _fsm.state == "LAUNCH":
            # 起飞段（任务三规格）：高度/速度环冻结——拍频锁定 FSM.LAUNCH_FREQ
            # 不推频；悬停口径 PD、防弹射限幅、姿态保护与拍频斜坡对该常值
            # 均无意义，整节跳过（main_sim 释放块把 _freq_last 硬复位为旧
            # 配平值 1.55，本分支下一拍即覆盖回 1.25）
            freq = float(cfg.FlightStateMachineConfig.LAUNCH_FREQ)
            self._freq_last = freq
        else:
            if _cruise <= 0.0:
                freq = self.f0 + f.KP_Z * (self.zr - pf[2]) - f.KD_Z * vf[2]
            # 输出上限用自动驾驶仪自己的防弹射权限（≈1.5×体重）：此前沿用
            # MANUAL_FREQ_MAX=3.5Hz（3.5×W）等于把弹射器留在活通路——
            # 2026-09-19 在环实录：掉高→高度环冲顶 3.5Hz→弹到 6.4m→绳
            # 374N 拽回→穿地。DP 侧的 2.2Hz 限幅加在 ap_out 之后的分支，
            # 飞控在环时永远轮不到
            freq = float(np.clip(freq, cfg.MANUAL_FREQ_MIN, f.FREQ_MAX_AP))
            # 拍频斜坡限制：消除开局升力冲击（2.6×W 的一脚油门会直接翻机）
            # 姿态优先：大滚转/俯仰角时保住拍频权限（升力=控制力矩的来源）
            att_bad = abs(roll) > 0.8 or abs(pitch) > 0.8
            if att_bad:
                freq = max(freq, self.f0)
            # 拍频斜坡：对称限幅（原 min() 只限上升，下行无限速——相位积分
            # 修复前下行跳变是释放冲击的主要源；对称后任意方向的频率变化都
            # 平滑，相位积分状态保证连续）
            freq = float(np.clip(freq, self._freq_last - f.FREQ_SLEW * dt,
                                 self._freq_last + f.FREQ_SLEW * dt))
            self._freq_last = freq

        # 内环速率反馈低通（2026-09-19）：拍动驱动反作用与气动振荡让瞬时
        # 角速率含大幅值拍频成分（1.9Hz 及谐波），内环直接吃瞬时值会以全
        # 舵偏追逐每个拍——在真实全机惯量（URDF 占位质量×23.7 缩放合成，
        # Iyy≈3e-3 量级）下成为姿态泵能源（实测：滑流权限开大后发散反而
        # 加速）。τ 压制拍频以上成分，内环只控慢变姿态分量；与 DP 高度
        # 环输入低通同款处方。
        if self._w_filt is None:
            self._w_filt = np.asarray(w, dtype=float).copy()
        else:
            _a = min(1.0, dt / max(f.RATE_FILT_TAU, 1e-3))
            self._w_filt = self._w_filt + _a * (np.asarray(w, dtype=float) - self._w_filt)
        self._wx_acc += float(w[0])
        self._wx_acc_n += 1

        # ── 2. 位置外环 + TECS 速度通道（2026-09-19 实测导数版）──
        # 位置误差积分项：消除常值静差（Fx(θ) 拟合截距残差等慢变偏置）。
        # 不带积分项时闭环停在 x≈+1.6m/vx≈+0.09m/s 的爬行点（实测）。
        # 抗饱和：pitch 指令触限时冻结积分
        ex = self.xr - pf[0]
        if (_fsm is None or _fsm.state != "LAUNCH") and \
                abs(self._pd_last or 0.0) < f.TILT_MAX * 0.99:
            # 起飞段（任务三）：速度环（位置→速度链）冻结，积分同步冻结
            self._xi = float(np.clip(self._xi + ex * dt,
                                     -f.XI_MAX, f.XI_MAX))
        if _fsm is not None and _fsm.state == "LAUNCH":
            # ── 2L. 起飞段分支（任务三规格）：俯仰保持锁定律 ──
            # 目标俯仰经 2 s 斜坡（flight_state_machine 内部状态）；
            # 高度/速度环冻结：不产生 vxd/TECS 俯仰指令，freq 已在节 1
            # 锁定。滚转/偏航链与尾舵调度结构不变。
            vxd = 0.0
            pitch_tgt = float(np.clip(_fsm.launch_pitch_target(),
                                      -f.TILT_MAX, f.TILT_MAX))
        elif _cruise > 0.0:
            # ── 2C. 巡航口径：真·总能量控制（TECS，2026-09-20）──
            # 旧交换配对（速度→拍频/高度→俯仰）在"高度高+速度低"象限
            # 正反馈：速度环加拍频=加升力→爬升更快→速度被拖低→再加油门，
            # 叠加爬升态尾翼攻角对消舵矩的俯仰冻结，终局拍频触顶失速
            # （0920_104813 在环实录：z 6.4→34m 后 vx 转负坠地）。
            # TECS 语义：拍频随总比能（多出来的能量才收油）、俯仰随能量
            # 分配（高且慢=势能过剩→压低机头把高度换成速度，两路同向）。
            # 2026-09-20 夜间开发（高度优先速度基准）：新被控对象
            # ∂Fz/∂V≈+0.9 N/(m/s) 是升力主变量、∂Fz/∂θ 极弱（≈0.03~0.3
            # N/rad）、∂Fz/∂f<0，固定 vxd 把速度通道锁定后高度误差没有
            # 释放升力的通路（试验架 z 缓爬 6.7m 失败实录 logs/harness_215g_v4.log）。
            # 高度误差经速度基准释放升力：z 高→目标速度降→升力随速度下降。
            # 机动预算（I-138）：坡度段内预算内高度差被预算死区吸收不驱动
            # 速度基准，预算外差值仍全额驱动；默认关时补偿量为零行为不变。
            _dz_budget = getattr(self, "_alt_budget_dz", 0.0)
            vxd = _cruise - f.TECS_ZV_K * ((pf[2] - self.zr) - _dz_budget)
            # 目标速度下限抬到验收带下沿 V_CMD_MIN=3.0（第六班）：旧下限 2.0
            # 低于升力下限，高度优先基准曾把速度降到升力下限以下导致坠落（在环主要故障链条①）
            vxd = float(np.clip(vxd, max(f.V_CMD_MIN, 0.5 * _cruise),
                                1.5 * _cruise))
            _g = 9.81
            _e_pot = _g * (self.zr - pf[2])
            _e_kin = 0.5 * (vxd * vxd - vf[0] * vf[0])
            e_E = _e_pot + _e_kin      # 总比能误差 (J/kg)
            e_B = float(np.clip(_e_pot - _e_kin,
                                -f.TECS_EB_MAX, f.TECS_EB_MAX))  # 分配误差限幅：禁深俯冲指令
            # 比能积分消配平截距稳态差；拍频触限时冻结（抗饱和）
            if f.FREQ_AP_MIN * 1.01 < self._freq_last < f.FREQ_MAX_AP * 0.99:
                self._ei = float(np.clip(self._ei + e_E * dt,
                                         -f.TECS_EI_MAX, f.TECS_EI_MAX))
            # 第十一班·极点配置油门阻尼：速度误差→拍频（古典浮沉阻尼器，
            # 模型实测拍频状态对浮沉极点参与度为零，故直接注入速度阻尼）
            # [66班] 频率通道符号修正（正频响语义，本体级代码逻辑修正非
            # 补丁）：本被控对象油门频响为正（∂Fx/∂f>0，第四十九班频率
            # 特性实测、统一框架 dfx_df>0 硬预滤同源），速度缺口
            # (vxd−vx)>0 的正确响应=升频补推、速度超额=降频。原
            # +KV_F·(vxd−vx) 对历史部署的负值 KV_F（四三班组 −2.611、
            # 五十班组 −0.6，logs/_patch_run50.py 按 −akvf 落 config）
            # 构成速度正反馈：速度缺→降频→推力减→更缺，经 clip[1.0,3.5]
            # ×斜坡 2.5 Hz/s 整流为极限环把拍频钉死 1.1792 Hz（第六十三班
            # §四A 四重不变证据：f0/KV_F 量级/幅值/开关态四变钉值不动）。
            # 符号翻转后 KV_F 按搜索侧符号约定解释：负值配置经本式给出
            # 正频响有效增益 |KV_F|——速度缺→升频补推；KF_P/KF_I 能量项
            # 符号不变（e_E>0=能量缺、正系数升频，本就是正频响语义）。
            freq = self.f0 + f.TECS_KF_P * e_E + f.TECS_KF_I * self._ei                 - f.TECS_KV_F * (vxd - vf[0])
            # 巡航保护性下限（第18轮头号，四轮证据链 2.1/1.58/1.50/1.50）：
            # 拍频低于 ~3Hz 扑翼升力按 f² 骤降，低速下垂段无可控升力。
            # 3.0 低于零推力点 3.3——贴下限时仍在净卸能，不助爬升；与
            # 手动油门下限 MANUAL_FREQ_MIN=1.5 分立口径。
            # 低速防能量耗散保护（第六班升级候选E）：vx 跌破 V_CMD_MIN=3.0 时按
            # 缺口比例升频补推力——f>f* 在两被控对象上均为正 Fx（见 FREQ_AP_MIN
            # 注释）。升频封顶 2.0 Hz（第六班在环 kimi2 实录：VMIN 把 freq 顶到
            # 3.5，巡航波形负升力区，t=65 从 15.8m 坠落）
            if getattr(f, "TECS_VFLOOR_ON", False) and vf[0] < f.V_CMD_MIN:
                freq = max(freq, min(self.f0
                           + f.TECS_VMIN_K * (f.V_CMD_MIN - vf[0]),
                           f.TECS_VMIN_FMAX))
            # 定坡度试验拍频上限（2026-09-27 归因测量定案：拍频越过约
            # 2.4Hz 进入工作点失效区，试验内压回失效区之下；仅
            # BANK_TEST_ON 时读取，关闭态上限=FREQ_MAX_AP 行为不变）
            _f_hi = (f.BANK_FREQ_CAP if (getattr(f, "BANK_TEST_ON", False)
                                         and getattr(f, "BANK_FREQ_CAP", 0.0) > 0.0)
                     else f.FREQ_MAX_AP)
            freq = float(np.clip(freq, f.FREQ_AP_MIN, _f_hi))
            # 盘旋段拍频冻结（2026-09-30 转弯线 R58→R59 定案：盘旋中
            # 体轴速度波动经总能量环放大成拍频摆动（KF_P·e_E 对速度
            # 的一阶参与度约 −KF_P·v），拍频摆动经机翼俯仰矩对拍频的
            # 一阶直接驱动力矩摆动，形成 TECS-频率-俯仰矩三路自激；
            # 拍对齐拍均值实录机翼俯仰矩漂移与拍频摆动严格同步。
            # 冻结切入时刻的调度值，切断该环；高度缓降在判据带内）。
            if getattr(f, "BANK_FREQ_FREEZE", False) \
                    and getattr(f, "BANK_TEST_ON", False) \
                    and t_sim is not None and t_sim >= f.BANK_TEST_T:
                freq = self._freq_last
            freq = float(np.clip(freq, self._freq_last - f.FREQ_SLEW * dt,
                                 self._freq_last + f.FREQ_SLEW * dt))
            self._freq_last = freq
            if getattr(f, "TECS_EB_TO_VZ", False):
                # 结构更换（第十一班·极点证据批准）：分配误差 → 期望垂直速度
                # → 比例+积分 → 俯仰指令。慢速分配通道对 3.21 rad/s 浮沉
                # 极点不可控（带宽差 60 倍），垂直速度快通道带宽可达；
                # 积分项消垂直速度静差，限幅防历史积分发散复现
                vz_des = f.TECS_EB_V_GAIN * e_B
                self._vzi = float(np.clip(
                    self._vzi + (vz_des - vf[2]) * dt,
                    -f.TECS_VZI_MAX, f.TECS_VZI_MAX))
                pitch_tgt = float(np.clip(
                    f.PITCH_TRIM + f.TECS_VZ_P * (vz_des - vf[2])
                    + f.TECS_VZ_I * self._vzi,
                    -f.TILT_MAX, f.TILT_MAX))
            else:
                pitch_tgt = float(np.clip(
                    f.PITCH_TRIM + f.TECS_KP_E * e_B - f.TECS_KD_V * vf[2],
                    -f.TILT_MAX, f.TILT_MAX))
            # 俯仰下压权限随速度衰减（第六班，在环主要故障链条②）：低速下本被控对象
            # 无升力托底，"低头换速度"不成立——vx 低于 V_PITCH_FREE=3.5 时
            # 下压权限线性衰减；第七班收尾修订：衰减到浅俯冲余量
            # V_DIVE_ALLOW=0.08 为止而非归零——低速恢复速度的唯一物理通道是
            # 浅俯冲换速度，归零会把唯一通道完全封死（kpr15/kdv25 两版在环实录：
            # vx 跌破 3 后无通道恢复，减速下沉坠地）
            if pitch_tgt < f.PITCH_TRIM:
                _fade = float(np.clip(
                    (vf[0] - f.V_PITCH_LOCK)
                    / max(f.V_PITCH_FREE - f.V_PITCH_LOCK, 1e-3), 0.0, 1.0))
                _down_cap = _fade * f.TILT_MAX + (1.0 - _fade) * f.V_DIVE_ALLOW
                pitch_tgt = max(pitch_tgt, f.PITCH_TRIM - _down_cap)
        else:
            vxd = f.KP_XY * ex + f.KI_XY * self._xi
            # 速度误差 → 加速度需求 → 前向力需求（含速度阻尼前馈）→ 俯仰角
            # （悬停口径专用；巡航的 pitch_tgt 已在 2C 分支给出）
            ax_d = f.KP_V * (vxd - vf[0])
            fx_ff = float(np.clip(f.DFX_DV * max(vxd, 0.0),
                                  -f.DFX_FF_MAX, f.DFX_FF_MAX))
            fx_d = cfg.MASS * ax_d + fx_ff
            pitch_tgt = float(np.clip(f.PITCH_TRIM + fx_d / f.DFX_DTHETA,
                                      -f.TILT_MAX, f.TILT_MAX))
        if _fsm is not None and _fsm.state != "LAUNCH" and _fsm.blend() < 1.0:
            # ── 2T. 切换瞬态（任务三规格）：目标俯仰在起飞段锁定律输出与
            # 巡航段 TECS 输出之间 2 s 线性斜坡，禁止阶跃。PITCH_TRIM 含于
            # 巡航段目标内一并过渡（s=1 时本块退化为巡航段原值）。
            _s = _fsm.blend()
            _pl = float(np.clip(_fsm.launch_pitch_target(),
                                -f.TILT_MAX, f.TILT_MAX))
            pitch_tgt = float(np.clip((1.0 - _s) * _pl + _s * pitch_tgt,
                                      -f.TILT_MAX, f.TILT_MAX))
        # 第八班（横向级联补全·教科书结构）：位置→航向→滚转。旧结构
        # 位置→速度→滚转跳过航向级，航向自由游荡（020256 实录：|v| 恒
        # 4.4 而航向 ±50° 摆、y 猎振，±90° 后末端崩溃）。期望航向由 y
        # 误差低增益生成，滚转跟随航向差——协同转弯闭环。
        _chi_des = float(np.clip(f.KP_XY * (self.yr - pf[1]), -0.6, 0.6))
        vyd = f.KP_XY * (self.yr - pf[1])
        # 配平/速度指令不得瞬间下达：悬停俯仰=零自然阻尼双积分器+
        # 尾舵权限薄（爬升诱发的尾翼攻角扰动 ≈ 舵面权限上限，离线闭环
        # 实测阶跃下指令会俯冲失控），pitch_des 按斜坡过渡
        # 第十班（浮沉治理·候选甲）：期望俯仰角先过一阶低通再进斜坡。
        # 驱动源=TECS 俯仰通道输出在 ±TILT_MAX 满幅摆动（第九班实录）。
        # 历史反面证据：慢斜坡 0.5 曾致速度环相位滞后（PITCH_DES_SLEW
        # 注释/232852），故三档 τ 全败即止、转结构更换。滤波状态在释放
        # 与坠地后清零（main_sim 释放块 / 0c 接地联锁）。
        _tau_p = float(getattr(f, "PITCH_TGT_FILT_TAU", 0.0))
        if _tau_p > 0.0:
            if getattr(self, "_pitch_tgt_filt", None) is None:
                self._pitch_tgt_filt = float(pitch_tgt)
            _a = min(1.0, dt / _tau_p)
            self._pitch_tgt_filt += _a * (pitch_tgt - self._pitch_tgt_filt)
            pitch_tgt = float(self._pitch_tgt_filt)
        _pd0 = self._pd_last
        if _pd0 is None:
            # 2026-09-19：首拍直接从配平目标出发。原实现从当前姿态(0)起
            # 以 0.5rad/s 斜坡爬向配平，0.35s 空窗里 -1.3N 配平残差把机体
            # 加速到 -3m/s，漂移空速接管升力后失控（第一门在环实录）
            _pd0 = pitch_tgt
        pitch_des = _pd0 + float(np.clip(pitch_tgt - _pd0,
                                         -f.PITCH_DES_SLEW * dt,
                                         f.PITCH_DES_SLEW * dt))
        self._pd_last = pitch_des
        _wrap_pi = lambda a: (a + math.pi) % (2.0 * math.pi) - math.pi
        roll_des = f.KP_CHI * _wrap_pi(_chi_des - yaw)   # 航向差→滚转（坡度管转弯）
        if not getattr(f, "LAT_CHI_CASCADE", True):
            roll_des = -f.KP_V * (vyd - vf[1])            # 旧结构（开关回退用）
        roll_des = float(np.clip(roll_des, -f.TILT_MAX, f.TILT_MAX))
        # 定坡度盘旋试验（2026-09-27，默认关闭）：开关开启且时间越过
        # 阈值后，滚转指令覆盖为常值坡度，用于压坡度转弯闭环考核；
        # 判据先于数据见 docs/压坡度转弯试验_判据与方案.md。
        if getattr(f, "BANK_TEST_ON", False) and t_sim is not None \
                and t_sim >= f.BANK_TEST_T:
            # 坡度指令斜坡（2026-09-29 转弯线 R1/R2 教训）：阶跃覆盖使
            # 切入误差一次性打满滚转权限，权限重构后 bang-bang 大幅
            # 摆振（R1 在环实录 9s 滚转过冲至 91°）。斜坡态沿 _bank_last
            # 持有，切入时刻从当前 roll_des 出发爬向目标；BANK_SLEW=999
            # 时一步到位，行为与斜坡前逐位一致。
            _bt = math.radians(f.BANK_TEST_DEG)
            _b0 = getattr(self, "_bank_last", None)
            if _b0 is None:
                _b0 = roll_des
            _bt = float(np.clip(_bt, -f.TILT_MAX, f.TILT_MAX))
            roll_des = _b0 + float(np.clip(_bt - _b0,
                                           -f.BANK_SLEW * dt, f.BANK_SLEW * dt))
            self._bank_last = roll_des
            # 坡度段俯仰保持（2026-09-28 权限包线定案）：把俯仰指令压进
            # 密度通道正权限区，依据见 config 注释与
            # docs/权限包线与因子隔离定案.md
            if getattr(f, "BANK_PITCH_HOLD_ON", False):
                pitch_des = math.radians(f.BANK_PITCH_HOLD_DEG)
            # 高度环机动预算（事项 I-138，默认关=行为逐位不变）：坡度段
            # 内高度环目标从 zr 放宽到 zr±预算，预算内高度差不驱动速度
            # 基准（即允许转弯期间按预算换高度），超出预算仍硬限回归。
            if getattr(f, "ALT_BUDGET_ON", False):
                _dz = pf[2] - self.zr
                _bud = abs(f.ALT_BUDGET_M)
                if abs(_dz) <= _bud:
                    _dz_eff = 0.0
                else:
                    _dz_eff = _dz - math.copysign(_bud, _dz)
                self._alt_budget_dz = _dz_eff
            else:
                self._alt_budget_dz = 0.0

        # ── 3. 姿态外环：角度误差 → 期望角速度 ──
        # 分段增益（任务三）：状态机启用时取 LAUNCH/CRUISE 分段值（切换
        # 瞬态内线性插值；KP_A 为滚转/俯仰/偏航三路共用的结构常量，按
        # 规格"KP_A"单键语义整体分段）；未启用时沿用旧键（单段路径）。
        _kp_a, _ki_a, _pdamp = f.KP_A, f.KI_A, f.PITCH_DAMP
        if _fsm is not None:
            _kp_a, _ki_a, _pdamp = _fsm.gains()
        # 姿态误差四元数化（2026-09-30 守门轮十一立项：R58~R70 十三轮
        # 在环实录的崩点首发反复落在欧拉运动学耦合的假俯仰分量上；
        # 本改造把姿态误差计算整体搬到体轴系——q_err=q_des⁻¹⊗q_actual，
        # 体轴误差=2·vec(q_err)（小角近似），直接生成三轴期望角速度，
        # 绕过欧拉角的全部运动学耦合。ATT_QUAT_ERR=1 启用（默认 0=
        # 欧拉误差=历史行为）；偏航指令通道保持各自逻辑不变。
        _q_err_on = getattr(f, "ATT_QUAT_ERR", False)
        _e_att = None
        if _q_err_on:
            _q_act = np.asarray(pose[3:7], dtype=float)
            _n = float(np.linalg.norm(_q_act))
            if _n > 1e-6:
                _q_act = _q_act / _n
                # q_des 的偏航分量取实测 yaw（R72 教训：取 yaw_ref 时
                # 巡航段偏航游荡使误差向量被偏航差污染，滚转俯仰分量
                # 严重混叠即坠；取实测 yaw 则 q_err 只含滚转俯仰差，
                # 偏航通道仍走原逻辑）
                _q_des = _euler2quat(_wrap(roll_des), _wrap(pitch_des),
                                     _wrap(yaw))
                # 乘法序（R71~R73 教训：conj(q_des)⊗q_act 是当前相对
                # 期望的旋转，控制方向反；体轴系误差应为
                # conj(q_act)⊗q_des——期望相对当前的旋转）
                _q_e = _quat_mul(_quat_conj(_q_act), _q_des)
                if _q_e[0] < 0:
                    _q_e = -_q_e
                # 精确轴角（R71 教训：小角近似 2·vec 在发射段大误差下
                # 方向错乱即坠；全角度正确的轴角向量）
                _v = _q_e[1:4]
                _vn = float(np.linalg.norm(_v))
                _ang = 2.0 * math.atan2(_vn, float(_q_e[0]))
                _e_att = (_v / max(_vn, 1e-9)) * _ang
        wxd = _kp_a * _wrap(roll_des - roll)
        # 滚转误差慢积分（2026-09-29 转弯线 R14~R24 教训，ROLL_KI 默认
        # 0=行为不变）：九轮在环实录共同形态——协调段建立后滚转角从
        # 指令值缓慢爬升 2~7°（切入后约 4s 累积越限后耦合爆发）。缓爬
        # 源=扑翼拍动重力矩在坡度姿态下的整流直流分量（翼面 34.5g×
        # 幅值 0.3rad×展向 0.16m 量级 0.016 N·m 峰峰，姿态相关整流出
        # 0.002~0.007 N·m 持续外滚矩），比例环只能以静差顶着。慢积分
        # 吸收常值扰矩，误差门与限幅同俯仰通道处方（抗饱和）。
        _rerr = _wrap(roll_des - roll)
        _roll_ki = getattr(f, "ROLL_KI", 0.0)
        if _roll_ki > 0.0 and abs(_rerr) < getattr(f, "ROLL_INT_GATE", 0.15):
            self._roll_i = float(np.clip(self._roll_i + _rerr * dt,
                                         -getattr(f, "ROLL_I_MAX", 0.3),
                                         getattr(f, "ROLL_I_MAX", 0.3)))
        elif _roll_ki > 0.0:
            pass  # 大误差瞬态冻结积分（条件积分，防 windup）
        if _e_att is not None:
            wxd = _kp_a * _e_att[0] + (_roll_ki * self._roll_i
                                       if _roll_ki > 0.0 else 0.0)
        elif _roll_ki > 0.0:
            wxd = _kp_a * _rerr + _roll_ki * self._roll_i
        # 俯仰角误差积分（第14轮）：比例角度环有限增益下存在稳态静差
        # （在环实测 0.13~0.19rad），恰为压不住配平富余上爬的缺口——
        # pitch_des 顶在 −TILT_MAX 而实际俯仰只到 −0.22~−0.26。慢积分
        # 顶出缺的角速度指令权限。抗饱和：指令触限冻结（同 KC_IZ 处方）；
        # 积分限幅 KI_A_MAX 封顶权限，防第13轮 KP_A 加倍甩摆类过激复发。
        _perr = _wrap(pitch_des - pitch)
        # 条件积分：误差门（PITCH_INT_GATE，历史硬编码 0.25rad）挡瞬态/
        # 失速后大误差。E4（2026-09-28 机主签字）将该门参数化：E3 在环
        # 实测指令-18°时误差 0.17~0.31rad 恒在门外，积分全程冻结、配平
        # 推力永不出（44 轮登记"积分门与稳态误差不匹配"的在环形态）；
        # 门放宽到 0.6 让积分在指令全程可介入。默认 0.25=历史行为逐位
        # 不变，环境变量 FLAPPER_PITCH_INT_GATE 覆盖（一键还原）。
        if abs(_perr) < getattr(f, "PITCH_INT_GATE", 0.25):
            self._pitch_i = float(np.clip(self._pitch_i + _perr * dt,
                                          -f.KI_A_MAX, f.KI_A_MAX))
        if _e_att is not None:
            wyd = _kp_a * _e_att[1] + _ki_a * self._pitch_i
        else:
            wyd = _kp_a * _perr + _ki_a * self._pitch_i
        # 第八班（协同转弯结构）：航向保持停用——航向环与位置环结构性
        # 对抗（位置压坡转弯→航向变→航向环用舵抵抗→持续侧滑→误差更大），
        # 是 t≈20 横侧发散的发动机（011536/020035 实录：roll_des 满幅猎振、
        # 舵全饱和、实际滚转仅指令 1/10）。教科书正解=坡度管转弯、方向舵
        # 只做角速率阻尼（wzd=0）。开关默认开阻尼模式。
        if getattr(f, "YAW_HOLD", False):
            wzd = f.KP_A * _wrap(self.yaw_ref - yaw)
        else:
            wzd = 0.0
        # 协调转弯率前馈（2026-09-29 转弯线 R13 重判定案，默认关）：
        # 重判读（真四元数=CSV qw/roll/pitch/qz 四列拼接）证明隔壁全部
        # 失败跑的滚转跟踪基本达标（偏差中位<2°），真凶=偏航：实测
        # wz 中位过转 2.4 倍，纯阻尼模式（wzd=0）让偏航阻尼持续对抗
        # 稳态转弯率，机体锁进大侧滑（β 稳态≈阻尼力矩/垂尾恢复梯度），
        # 侧滑滚转梯度 L_β·β≈尾翼差动满偏量→贴限 77%→失稳触地。前馈
        # 把协调盘旋所需的稳态转弯率直接注入指令，阻尼只管快摆分量。
        # ω=g·tanφ/V；方向系数 YAW_FF_DIR 按在环实测机体协调方向定
        # （R13 判读：+10° 坡度对应 wz 为负），默认 -1。
        if getattr(f, "YAW_FF_TURN", False) \
                and getattr(f, "BANK_TEST_ON", False) \
                and t_sim is not None and t_sim >= f.BANK_TEST_T:
            _v_ff = max(abs(getattr(self, "_v_eff_last", 4.0)), 1.0)
            # 前馈坡度用指令值（R34 教训：实测 roll 前馈在过冲时放大
            # 机头转速需求形成正反馈；R34 声明回退但漏改代码，R35~
            # R49 全部带病，2019-09-29 补正）
            _phi_ff = roll_des
            # 侧滑角闭环（2026-09-29 R14 教训，COORD_KB 默认 0=行为与
            # 纯前馈一致）：R14 前馈后机头转速仍持续超前轨迹角速度
            # （wz -0.66 vs 轨迹 -0.2~-0.4），β 单调积累至失稳门槛后
            # 12s 爆发。β=yaw−χ（χ=世界系轨迹角）可测，比例回正把
            # 机头锁到速度矢量，β 塌缩侧滑梯度扰动同步消失。
            _beta_corr = 0.0
            _kb = getattr(f, "COORD_KB", 0.0)
            if _kb > 0.0:
                # 轨迹角全象限原式（2026-09-29 转弯线 R57 定案：原实现
                # 分母 clamp(abs(vx),0.5)·sign 在机头转过 90°、世界 vx
                # 过零的 0<|vx|<0.5 窗口把 χ 钉死约 0.3s，脱离瞬间 χ 跳
                # 变 15°，β 修正冲击 wzd——R14~R56 各轮机头转过直角处
                # 崩点的最终触发器，R51 控制器口径细账实录）。atan2 对
                # vx 过零本身连续；速度模过低时轨迹角无意义，冻结修正。
                _vw = self._pv_filt
                _chi = math.atan2(_vw[1], _vw[0])
                if math.hypot(_vw[0], _vw[1]) < 1.0:
                    _beta_corr = 0.0
                else:
                    _beta_err = _wrap(_chi - yaw)
                    _beta_corr = _kb * _beta_err
                    # β 静差积分（R48 后自我核对定案：β 静差缓漂是协调段
                    # 先导，纯比例 β 环稳态静差不可避免，积分顶掉）
                    _ki_b = getattr(f, "COORD_KI", 0.0)
                    _i_max_b = getattr(f, "COORD_I_MAX", 0.3)
                    if _ki_b > 0.0 and abs(_beta_err) < 0.05:
                        self._beta_i = float(np.clip(
                            self._beta_i + _beta_err * dt,
                            -_i_max_b, _i_max_b))
                    elif _ki_b > 0.0:
                        # 泄放（守门轮九：R68 实录 β 跳变时旧积分满幅
                        # 反向反噬；大误差期积分按时间常数 1s 向零泄放）
                        self._beta_i *= max(0.0, 1.0 - dt / 1.0)
                    _beta_corr += _ki_b * getattr(self, "_beta_i", 0.0)
            # 前馈增益慢自校准（2026-09-30 守门轮五：R58~R61 四轮俯仰
            # 端修复全败后回归链条之根——偏航率稳态过转一成余产生 β
            # 静差，静差经侧滑梯度持续外推滚转，滚转角速度摆动再经欧
            # 拉耦合污染俯仰读数。前馈增益对稳态转速误差做慢积分自
            # 校准（YAW_FF_ADAPT=1 启用，校准时间常数 YAW_FF_ADAPT_TAU
            # 默认 8s，增益限幅 [0.7, 1.4]），稳态过转归零则 β 静差
            # 与其下游全链消失。
            _ff_gain = getattr(self, "_yaw_ff_gain", 1.0)
            if getattr(f, "YAW_FF_ADAPT", False):
                _ff_dir = 1.0 if wzd >= 0 else -1.0
                _wz_ref = _ff_dir * self._w_filt[2]      # 实测在期望方向幅值
                _wz_tgt = _ff_dir * wzd                  # 目标幅值（同号）
                _g_err = (_wz_tgt - _wz_ref) / max(abs(_wz_tgt), 1e-3)
                _lam = dt / max(getattr(f, "YAW_FF_ADAPT_TAU", 8.0), 0.5)
                _ff_gain = float(np.clip(_ff_gain + _lam * _g_err, 0.7, 1.4))
                self._yaw_ff_gain = _ff_gain
            wzd = float(np.clip(
                _ff_gain * getattr(f, "YAW_FF_DIR", -1.0) * 9.81
                * math.tan(_phi_ff) / _v_ff + _beta_corr, -1.5, 1.5))

        # ── 4. 角速度内环：→ 力矩 → 舵偏（舵效按工况调度）──
        # [67班] 滚转速率反馈独立标度 ROLL_DAMP（本体级补全，缺省 1.0 与
        # 旧行为逐位一致）：俯仰/偏航两路均有独立速率标度（PITCH_DAMP/
        # YAW_DAMP），滚转是三路中唯一缺独立标度的通道。定位证据
        # （logs/ident_stream_flight_0926_191728.csv，66 班 run3）：
        # a) wx 谱峰 10.58 Hz（幅值 0.16 rad/s），非拍频 1.9 Hz 亦非其
        #    二倍频——恰为滚转速率环相位过 180° 的频率（滤波 τ=0.030
        #    滞后 63° + 力矩→角速度积分 90° + 120 Hz 离散采样滞后 32°，
        #    合计 185°）；
        # b) ail 列自释放即 ±0.30 继电器式猎振（饱和 49~86%），同期滚转角
        #    本身仅 0.001~0.05 rad——继电器极限环特征，非姿态需求；
        # c) 新工作点尾翼差动效能塌缩至 b_ail=0.00483·veff²≈0.077 N·m/rad
        #    （215g 尾面积减半重标定），旧工作点整定的共享速率增益
        #    KP_R=0.25 在该效能下环路增益≈0.7（气动路径）+ 尾面位置驱动
        #    反作用路径（main_sim L1432 ail 同步写尾面关节目标，panel 惯量
        #    8e-6×2 在 10 Hz 上的反作用矩与气动路径同量级）→ 合计越过
        #    1，自激为 ±0.30 满偏极限环；通道同时失去增量滚转权限
        #    （66 班 run2/3 横侧缓发散死因链的一环）。
        # 整定：ROLLDAMP=0.30（部署经补丁键 roll_damp）把速率路径增益
        # 除以 3.3，极限环消除且姿态环阻尼 ζ≈0.26 保持（框架横侧闭环
        # 验证 logs/_run67_latsim.py：rd=0.30 全档稳定、小扰动恢复）。
        # 滚转速率反馈超前网络（2026-09-30 守门轮七：横侧 ~1Hz 模态
        # 在环实测持续正增长至爆；现阻尼路径在 1Hz 的总相位约一 百
        # 三十度（滤波+执行低通+气动积分），阻尼半成激励。一阶超前
        # wx_lead=wx+Td·dwx/dt 补相位，ROLL_LEAD_TD>0 启用（默认 0=
        # 直读=历史行为），整定域按 1Hz 处补三十至六十度相位定。
        _td_lead = getattr(f, "ROLL_LEAD_TD", 0.0)
        if _td_lead > 0.0:
            # 带通+双线性组合（守门轮二十：R81/R82 教训——全频段真超前
            # 在机动段过冲、误差门控不足；本版超前只作用于模态频段的
            # 带通分量（高通于半模态频、低通于一点五倍模态频），机动
            # 低频分量直通；带通分量经双线性超前放大 ROLL_LEAD_BP_K
            # 后叠加回反馈）
            _f_mod = max(getattr(f, "ROLL_LEAD_FMOD", 2.4), 0.3)
            _w_lo = 2.0 * math.pi * 0.5 * _f_mod
            _w_hi = 2.0 * math.pi * 1.5 * _f_mod
            _u = float(self._w_filt[0])
            _a1 = min(1.0, dt * _w_lo)
            self._bp_lo = getattr(self, "_bp_lo", 0.0) + _a1 * (
                _u - getattr(self, "_bp_lo", 0.0))
            _hp = _u - self._bp_lo
            _a2 = min(1.0, dt * _w_hi)
            self._bp_hi = getattr(self, "_bp_hi", 0.0) + _a2 * (
                _hp - getattr(self, "_bp_hi", 0.0))
            _bp = self._bp_hi
            _k = 2.0 / max(dt, 1e-4)
            _b0 = 1.0 + _k * _td_lead
            _b1 = 1.0 - _k * _td_lead
            _tl = _td_lead / max(getattr(f, "ROLL_LEAD_N", 10.0), 2.0)
            _ac = 1.0 - _k * _tl
            _den = 1.0 + _k * _tl
            _x1 = getattr(self, "_leadx_x1", None)
            _y1 = getattr(self, "_leadx_y1", None)
            if _x1 is None:
                _x1, _y1 = _bp, _bp
            _y = ((_b0 * _bp + _b1 * _x1) - _ac * _y1) / _den
            self._leadx_x1 = _bp
            self._leadx_y1 = _y
            _wx_lead = _u + getattr(f, "ROLL_LEAD_BP_K", 1.0) * float(
                np.clip(_y, -3.0, 3.0))
        else:
            _wx_lead = float(self._w_filt[0])
        # 边带陷波（守门轮五十二：谐波域干预第一步——边带耦合实测
        # 模态邻域最强至六成，控制器在边带频段与周期时变对象的相互
        # 作用是平均模型口径失配主源；对反馈链在拍频边带族加二阶
        # 陷波，切除边带增益实现解耦。ROLL_NOTCH_F>0 启用（默认 0
        # =历史行为），Q 为陷波品质因数）
        _nf = getattr(f, "ROLL_NOTCH_F", 0.0)
        if _nf > 0.0:
            # 标准双线性陷波（轮五十三重实现：轮五十二自推导系数错
            # 呈全频反相致发射即崩；本式 H(s)=(s²+ωn²)/(s²+ωn/Q·s+ωn²)
            # 经双线性替换，通带增益恒一，已静态扫频验证）
            _nq = max(getattr(f, "ROLL_NOTCH_Q", 8.0), 0.5)
            _wn_n = 2.0 * math.pi * _nf
            _A = _wn_n * _wn_n * dt * dt
            _B = 4.0 * _wn_n * dt / _nq
            _n0 = 4.0 + _A
            _n1 = 2.0 * _A - 8.0
            _d0 = 4.0 + _A + _B
            _d2 = 4.0 + _A - _B
            _x_n = _wx_lead
            _x1n = getattr(self, "_notch_x1", _x_n)
            _x2n = getattr(self, "_notch_x2", _x_n)
            _y1n = getattr(self, "_notch_y1", _x_n)
            _y2n = getattr(self, "_notch_y2", _x_n)
            _y_n = (_n0 * _x_n + _n1 * _x1n + _n0 * _x2n
                    - _n1 * _y1n - _d2 * _y2n) / _d0
            self._notch_x2 = _x1n
            self._notch_x1 = _x_n
            self._notch_y2 = _y1n
            self._notch_y1 = _y_n
            _wx_lead = float(_y_n)
        self._wx_lead = _wx_lead
        # 横侧模态带通阻尼（2026-09-30 守门轮九：超前补相位之外，
        # 对模态频段直接增阻。一阶带通=高通减低通，中心频率
        # ROLL_BP_F（默认 1.0Hz），带通分量乘 ROLL_BP_K 加进速率
        # 反馈；默认 K=0=关=历史行为）
        _bp_k = getattr(f, "ROLL_BP_K", 0.0)
        if _bp_k > 0.0:
            _w0 = 2.0 * math.pi * max(getattr(f, "ROLL_BP_F", 1.0), 0.2)
            _hp = self._w_filt[0] - getattr(self, "_bp_lp", 0.0)
            _a_bp = min(1.0, dt * _w0)
            self._bp_lp = getattr(self, "_bp_lp", 0.0)                 + _a_bp * (self._w_filt[0] - getattr(self, "_bp_lp", 0.0))
            _wx_lead = _wx_lead + _bp_k * _hp
        # 拍同步平均反馈（守门轮五十四：宽带边带解耦——滑动窗等于
        # 当前拍周期的整数周期平均，对拍频各谐波与边带族有结构性
        # 抑制（整周期平均零点），保留基带机动分量；单点陷波只切一
        # 刀已证不足（R99）。ROLL_STROKE_AVG=1 启用，默认关=历史行为。
        # 已按上环铁律静态扫频验证贴档（判据文档十五节）。
        if getattr(f, "ROLL_STROKE_AVG", False) and t_sim is not None:
            _per = 1.0 / max(self._freq_last, 0.2)
            _nsa = max(int(round(_per / max(dt, 1e-4))), 4)
            _buf = getattr(self, "_sa_buf", None)
            if _buf is None or len(_buf) != _nsa:
                _buf = [float(self._w_filt[0])] * _nsa
            _buf.pop(0)
            _buf.append(float(self._w_filt[0]))
            self._sa_buf = _buf
            _wx_fb_avg = sum(_buf) / _nsa
            _wx_lead = (_wx_lead - float(self._w_filt[0])
                        + _wx_fb_avg)   # 超前项按拍平均后的信号重锚
        mx = f.KP_R * (wxd - getattr(f, "ROLL_DAMP", 1.0) * _wx_lead)
        # 定常滚转矩前馈配平（2026-09-30 I-006 盘旋线：G3/G4 准稳态窗实测
        # 常驻扰矩零点零一牛米量级、吃掉盘旋权限大半，环外直接抵偿解放
        # 环路；值为数据测得，扰矩源头调查 I-005 并行。默认 0=历史行为）
        _roll_trim_m = float(getattr(f, "ROLL_TRIM_M", 0.0))
        # 滚转速率反馈专用二阶低通（2026-09-29 转弯线 R4 教训，
        # ROLL_FILT_TAU 默认 0=用三轴共用 _w_filt=行为不变）：R4 在环
        # 实录——wx 实测含 ±1.5 rad/s 大幅值纹波（谱峰 5.7Hz 及
        # 2.9Hz 分量），三轴共用 RATE_FILT_TAU=0.030 对其衰减不足，
        # wx_filt 被纹波主导后 mx 符号随纹波换向，dA 均值近零、机体
        # 钉死配平坡度（6~8s dA 均值≈0 实录）。滚转机动需求角速度
        # 0.3~0.8 rad/s 远小于纹波幅，专用慢滤波把纹波压到信噪比
        # 可用，0.5Hz 机动带宽相位损失约 20°，代价登记。
        _tau_rf = getattr(f, "ROLL_FILT_TAU", 0.0)
        if _tau_rf > 0.0:
            _a_rf = min(1.0, dt / _tau_rf)
            if self._wx_roll_lpf is None:
                self._wx_roll_lpf = float(self._w_filt[0])
            self._wx_roll_lpf += _a_rf * (float(self._w_filt[0])
                                          - self._wx_roll_lpf)
            mx = f.KP_R * (wxd - getattr(f, "ROLL_DAMP", 1.0)
                           * self._wx_roll_lpf)
        # 滚转环探针（2026-09-29 转弯线 R5~R9 判别用，ROLL_DBG=1 时
        # 每 ~0.1s 打印指令链各环节，默认关零开销）：R9 在环实录 dA
        # 满幅恒定但机体不动，且 dA 方向与手推符号相反，需逐环节对账。
        if getattr(f, "ROLL_DBG", False):
            self._roll_dbg_n = getattr(self, "_roll_dbg_n", 0) + 1
            if self._roll_dbg_n % 12 == 0:
                print(f"[ROLL-DBG] t={t_sim:.2f} roll={roll:.3f} "
                      f"rdes={roll_des:.3f} wxd={wxd:.3f} "
                      f"wxf={self._w_filt[0]:.3f} "
                      f"wxlpf={getattr(self, '_wx_roll_lpf', float('nan')):.3f} "
                      f"mx={mx:.4f}", flush=True)
        # 翼差动三维效能重标定（2026-09-28，DA_SCHED_3D_ON 默认关）：
        # dA 按力矩需求逆映射——三维实测效能 0.046 N·m/rad（dA 语义，
        # 巡航姿态俯仰 −13°~+3° 全程正号稳健，见
        # docs/科目一重跑_俯仰保持版_根因定位.md 第三节），尾翼差动吃
        # 剩余力矩；关闭时 mx_tail=mx，行为逐位不变。
        # 动压门控（2026-09-29 转弯线 R1 教训）：R1 在环实录
        # （ident_stream_flight_0929_163126）——释放瞬态 wx 尖峰在
        # 低速爬升段（v_eff 低、尾翼无权限）驱动 dA 满幅 ±0.175，
        # 翼差动权限不依赖动压，满幅差动注入远超低速段可承受的扰动，
        # 机体释放后 1~3 s 被自身差动摇翻坠地（旧调度 da_sched≈0.002
        # 压死 dA 恰是历史爬升稳定的原因之一）。门控 v_eff 低于
        # DA3D_VMIN 时 dA 走旧调度分支（≈0，基线行为），差动只在
        # 巡航动压域放出。DA3D_VMIN=0 时门控恒放行，行为与门控前
        # 逐位一致。
        _dA_3d = None
        _da3d_ok = getattr(f, "DA_SCHED_3D_ON", False) \
            and v_eff >= getattr(f, "DA3D_VMIN", 0.0)
        if getattr(f, "DA3D_TMIN", 0.0) > 0.0:
            _da3d_ok = _da3d_ok and t_sim is not None \
                and t_sim >= getattr(f, "DA3D_TMIN", 0.0)
        if _da3d_ok:
            # 方向系数（2026-09-29 转弯线 R5/R6 在环判别）：两轮互证
            # 在环 dA 正偏置产生负滚转力矩（R5 实录 dA=-0.166 恒定半秒
            # 机体正转 42°；R6 实录 dA=+0.175 满幅 1.5s 机体反转 43°），
            # 与离线钉固标定 B_DA_3D=+0.0955 的方向定义差一个镜像
            # （dA 到两翼拍幅的映射方向未曾在环标定）。DA3D_DIR=-1
            # 翻转映射方向；默认 +1=翻转前行为。
            _da3d_dir = getattr(f, "DA3D_DIR", 1.0)
            _dA_3d = float(np.clip(_da3d_dir * (mx + _roll_trim_m) / f.B_DA_3D,
                                   -f.DA_MAX_3D, f.DA_MAX_3D))
            _mx_tail = mx + _roll_trim_m - f.B_DA_3D / _da3d_dir * _dA_3d
        else:
            _mx_tail = mx + _roll_trim_m
        # 第九班：俯仰阻尼独立旋钮（浮沉治理）——跟踪增益不变，仅俯仰
       # 角速率反馈加强，压 t≈30-45 的浮沉泵能（022210 实录 elev 打满）
        # 俯仰率反馈超前（2026-09-30 守门轮十四：R74 逐物理步定案
        # 崩盘为横-俯两轴反相的耦合振荡增长（wx/wy 同频反摆，频率与
        # 精谱主峰吻合），单轴超前压不住耦合模态；PITCH_LEAD_TD>0 时
        # wy 反馈过同款两级超前滞后网络（复用滚转侧实现），两轴联合
        # 阻尼。默认 0=历史行为）
        _td_pl = getattr(f, "PITCH_LEAD_TD", 0.0)
        if _td_pl > 0.0:
            # wy 侧带通+双线性（守门轮二十四：与 wx 侧同制——真超前只
            # 作用于模态频段带通分量，机动低频直通；两轴完全体）
            _f_mod = max(getattr(f, "ROLL_LEAD_FMOD", 2.4), 0.3)
            _w_lo = 2.0 * math.pi * 0.5 * _f_mod
            _w_hi = 2.0 * math.pi * 1.5 * _f_mod
            _u = float(self._w_filt[1])
            _a1 = min(1.0, dt * _w_lo)
            self._bpy_lo = getattr(self, "_bpy_lo", 0.0) + _a1 * (
                _u - getattr(self, "_bpy_lo", 0.0))
            _hp = _u - self._bpy_lo
            _a2 = min(1.0, dt * _w_hi)
            self._bpy_hi = getattr(self, "_bpy_hi", 0.0) + _a2 * (
                _hp - getattr(self, "_bpy_hi", 0.0))
            _bp = self._bpy_hi
            _k = 2.0 / max(dt, 1e-4)
            _b0 = 1.0 + _k * _td_pl
            _b1 = 1.0 - _k * _td_pl
            _tl = _td_pl / max(getattr(f, "ROLL_LEAD_N", 10.0), 2.0)
            _ac = 1.0 - _k * _tl
            _den = 1.0 + _k * _tl
            _x1 = getattr(self, "_leady_x1", None)
            _y1 = getattr(self, "_leady_y1", None)
            if _x1 is None:
                _x1, _y1 = _bp, _bp
            _y = ((_b0 * _bp + _b1 * _x1) - _ac * _y1) / _den
            self._leady_x1 = _bp
            self._leady_y1 = _y
            _wy_lead = _u + getattr(f, "PITCH_LEAD_BP_K",
                                     getattr(f, "ROLL_LEAD_BP_K", 1.0)) * float(
                np.clip(_y, -3.0, 3.0))
        else:
            _wy_lead = float(self._w_filt[1])
        self._wy_lead = _wy_lead
        # 延迟对消注入口（I-008 前置结构，判据第七节"含滑流延迟状态的
        # 相位整形"）：对俯仰与偏航速率反馈加一阶延迟补偿
        # fb = w + τ̂·dw/dt（dw/dt 用滤波态一步差分并限幅防噪声）。
        # τ̂ 经环境键给定（部署时绑矩阵延迟辨识值），默认 0=历史行为。
        _tau_e = float(_os_env("FLAPPER_TAU_HAT_ELEV", "0") or 0)
        _tau_r = float(_os_env("FLAPPER_TAU_HAT_RUD", "0") or 0)
        _fb_wy = self._w_filt[1]
        _fb_wz = self._w_filt[2]
        if _tau_e > 0.0:
            _dw = (self._w_filt[1] - getattr(self, "_wf1_prev",
                                            self._w_filt[1])) / max(dt, 1e-3)
            _fb_wy = self._w_filt[1] + _tau_e * float(
                np.clip(_dw, -20.0, 20.0))
        self._wf1_prev = self._w_filt[1]
        if _tau_r > 0.0:
            _dwz = (self._w_filt[2] - getattr(self, "_wf2_prev",
                                             self._w_filt[2])) / max(dt, 1e-3)
            _fb_wz = self._w_filt[2] + _tau_r * float(
                np.clip(_dwz, -20.0, 20.0))
        self._wf2_prev = self._w_filt[2]
        my = f.KP_R * (wyd - _pdamp * _wy_lead)
        if _tau_e > 0.0:
            # 俯仰阻尼路径的延迟对消叠加项（与偏航同构）
            my = my - f.KP_R * _pdamp * (_fb_wy - self._w_filt[1])
# 模态交叉阻尼（2026-09-30 守门轮十五：R76 互相关实测 wx~wy
        # 强反相关，模态坐标=两轴之差；反馈矩阵加非对角项，两轴同时
        # 强阻该反相组合。MODAL_CROSS_K>0 启用（默认 0=历史行为），
        # 在超前输出之上叠加交叉项）
        _mk = getattr(f, "MODAL_CROSS_K", 0.0)
        if _mk > 0.0:
            _m_pair = getattr(self, "_wx_lead", self._w_filt[0])                 - getattr(self, "_wy_lead", self._w_filt[1])
            mx = mx - f.KP_R * _mk * _m_pair
            my = my + f.KP_R * _pdamp / max(f.KP_R, 1e-3) * _mk * _m_pair
        # 第七班末：偏航阻尼独立加倍（横侧向发散模态治理）——偏航角环
        # 增益不变，仅偏航角速率反馈加倍，压住 t≈20 起的发散（011836 实录）
        mz = f.KP_R * (wzd - f.YAW_DAMP * _fb_wz)
        if v_eff >= f.V_TAIL_MIN:
            # 尾翼三通道：B(v_eff) = k·v_eff²（动压调度表，滑流计入）
            b_elev = f.K_B_TAIL_ELEV * v_eff * v_eff
            b_ail = f.K_B_TAIL_AIL * v_eff * v_eff
            b_rud = f.K_B_TAIL_RUD * v_eff * v_eff
            # 尾翼攻角前馈（2026-09-20 夜间调试第5轮）：尾面有效攻角=
            # 机体攻角+舵偏，巡航稳态爬升时攻角扰动矩（vz+俯仰诱发，
            # 实测 −0.14N·m）恰好对消舵输出矩、内环纯比例冻结俯仰
            # （平衡态解剖：my_dem −0.06/合矩 0.00）。前馈抵消攻角项，
            # 舵偏只承担需求矩。体轴速度由滤波反馈+姿态旋转重建
            _cb, _sb = np.cos(pitch), np.sin(pitch)
            _vbx = _cb * vf[0] + _sb * vf[2] + getattr(self, "_slip_ff", 0.0)
            _vbz = _sb * vf[0] + _cb * vf[2]
            _alpha_ff = float(np.arctan2(_vbz, max(_vbx, 0.1)))
            _alpha_ff *= getattr(f, "ALPHA_FF_GAIN", 1.0)
            self.alpha_ff_last = _alpha_ff
            elev = float(np.clip(my / b_elev - _alpha_ff,
                                 -f.DELTA_MAX, f.DELTA_MAX))
            # 升降舵正弦注入口（守门轮二十七：同差动口，默认 0=关）
            _pe = getattr(f, "ELEV_PROBE_AMP", 0.0)
            if _pe > 0.0 and t_sim is not None:
                if getattr(f, "MULTISINE_ON", False):
                    elev = float(np.clip(elev + multisine(
                        f, "elev", t_sim) * _pe, -f.DELTA_MAX,
                        f.DELTA_MAX))
                else:
                    elev = float(np.clip(elev + _pe * math.sin(
                        2.0 * math.pi * getattr(f, "AIL_PROBE_FREQ", 2.33)
                        * t_sim), -f.DELTA_MAX, f.DELTA_MAX))
            # 升降舵通道多音键（二倍拍频带内容属升降舵驱动的俯仰动力
            # 学——两轮差动通道负结果的正确通道；错相同款每音递增0.7）
            _add_e = 0.0
            _tk = 0
            for _tone in str(_os_env("FLAPPER_MODAL_TONES_E", "")).split(","):
                if ":" in _tone:
                    try:
                        _tf, _ta = _tone.split(":")
                        _tk += 1
                        _add_e += float(_ta) * math.sin(
                            2.0 * math.pi * float(_tf) * t_sim
                            + 1.1 + 0.7 * _tk)
                    except ValueError:
                        pass
            if _add_e != 0.0:
                elev = float(np.clip(elev + _add_e, -f.DELTA_MAX,
                                     f.DELTA_MAX))
            # 第九班（接手·滚转权限修复）：去除 0.15 折扣系数——该系数使尾翼
            # 差动只交付需求力矩的 15%（另一会话"指令 20° 实际 2°"记录的泄漏点）；
            # 翼差动通道在 215 g 已实测塌缩（0.041 N·m/rad 且 2.2 Hz 上变号，
            # 调度已按真值重新标定 B_DA_REF=0.0406），尾翼差动是唯一有效的滚转通道，
            # 全额映射 AIL_AUTH=1.0
            ail = float(np.clip(f.AIL_AUTH * _mx_tail / b_ail,
                                -f.DELTA_MAX, f.DELTA_MAX))
            # 舵正弦注入口（守门轮二十六：开环传函相位辨识用，
            # AIL_PROBE_AMP>0 时叠加，默认 0=历史行为）
            _pa = getattr(f, "AIL_PROBE_AMP", 0.0)
            if _pa > 0.0 and t_sim is not None:
                if getattr(f, "MULTISINE_ON", False):
                    ail = float(np.clip(ail + multisine(
                        f, "ail", t_sim) * _pa, -f.DELTA_MAX, f.DELTA_MAX))
                else:
                    ail = float(np.clip(ail + _pa * math.sin(
                        2.0 * math.pi * getattr(f, "AIL_PROBE_FREQ", 2.33)
                        * t_sim), -f.DELTA_MAX, f.DELTA_MAX))
            # 模态频专属音（I-028：施罗德相位在 2.33Hz 泄漏自消致差动
            # 通道外注入盲区，谱诊断外注入对测量之比为零；多正弦之上
            # 叠加模态频单音补盲。FLAPPER_MODAL_TONE_AMP 单音旧键；
            # FLAPPER_MODAL_TONES="2.33:0.05,2.6:0.05" 多音键，把共振
            # 频段网格间隙整段填平（本线诊断：仿真在无约束间隙自生假
            # 振铃）。默认空=行为不变）
            _mt = float(_os_env("FLAPPER_MODAL_TONE_AMP", "0") or 0)
            _add = 0.0
            if _mt > 0.0:
                _add += _mt * math.sin(
                    2.0 * math.pi * getattr(f, "AIL_PROBE_FREQ", 2.33)
                    * t_sim + 1.1)
            _tk = 0
            for _tone in str(_os_env("FLAPPER_MODAL_TONES", "")).split(","):
                if ":" in _tone:
                    try:
                        _tf, _ta = _tone.split(":")
                        # 音序错相（本线设计错误修正：同相多音周期性同相
                        # 相加产生尖峰与谱泄漏，每音递增 0.7 弧度）
                        _tk += 1
                        _add += float(_ta) * math.sin(
                            2.0 * math.pi * float(_tf) * t_sim
                            + 1.1 + 0.7 * _tk)
                    except ValueError:
                        pass
            if _add != 0.0:
                ail = float(np.clip(ail + _add, -f.DELTA_MAX, f.DELTA_MAX))
            rud = float(np.clip(mz / b_rud, -f.DELTA_MAX, f.DELTA_MAX))
            _pr = getattr(f, "RUD_PROBE_AMP", 0.0)
            if _pr > 0.0 and t_sim is not None:
                if getattr(f, "MULTISINE_ON", False):
                    rud = float(np.clip(rud + multisine(
                        f, "rud", t_sim) * _pr, -f.DELTA_MAX,
                        f.DELTA_MAX))
                else:
                    rud = float(np.clip(rud + _pr * math.sin(
                        2.0 * math.pi * getattr(f, "AIL_PROBE_FREQ", 2.33)
                        * t_sim), -f.DELTA_MAX, f.DELTA_MAX))
        else:
            # 静气/低速且无滑流：尾舵无动压即无控制权限（B=0 物理事实），通道关闭
            elev = ail = rud = 0.0
        # 滚转主通道：翼差动 PD（角度误差+角速率直驱），输出按
        # B_dA(f)=5.2f² 归一到悬停整定点——拍频升高时舵效增强，
        # 同等滚转误差所需的拍幅偏移等比例减小。
        # 2026-09-19：速率项改用滤波后角速率（与尾舵通道同处方）——
        # flap 关节轴为 X，拍动反作用直接进滚转，瞬时 wx 含大幅值拍频
        # 成分；未滤波时差动逐拍满幅摆动，在真实滚转惯量（~1e-3 量级）
        # 上指数发散并整流出恒定侧力（212619 run 实测 vy→-36m/s）
        b_da = f.B_DA_SLOPE * freq * freq
        da_sched = f.B_DA_REF / max(b_da, 1.0)
        # 分幅共存（2026-09-28，DM_COEXIST_ON 默认关）：旧规则是逐拍类
        # 通道开启即一刀旁路 dA，导致巡航段小修正阻尼空缺（横侧 1.32Hz
        # 模态自由发散，科目一重跑根因二）；共存规则下 dA 存活并按
        # 三维效能映射承担小误差，密度通道只在大误差越死区后出手。
        _sd_active = (getattr(f, "STROKE_DIFF_ON", False)
                      or getattr(f, "DUTY_MOD_ON", False))
        if _sd_active and not getattr(f, "DM_COEXIST_ON", False):
            dA = 0.0   # 逐拍差动/占空比调制开启时旁路老幅值差动，两差动不打架
        elif _dA_3d is not None:
            dA = _dA_3d
        else:
            dA = float(np.clip((f.K_DA * _wrap(roll_des - roll) - f.K_DA_R * self._w_filt[0])
                               * da_sched,
                               -f.DA_MAX, f.DA_MAX))
        # 偏航抵消组合通道（2026-09-27，默认关闭）：实测幅值差动的偏航副作用
        # 与滞后差动的偏航效能等大反号（+0.2093 对 −0.2092，
        # docs/侧滑工况横侧通道效能.md），按比值混合可保留滚转效能并消除
        # 偏航注入；dlag 限幅取时机差动已验证域。
        # 逐拍差动决策（2026-09-27，STROKE_DIFF_ON 默认关）：拍边界到点
        # 读滚转误差与滤波角速率出密度差指令 δρ，映射两翼拍/停闸门；
        # 开启时老幅值差动 dA 旁路。决策节拍=1/当前拍频（控制器侧计时，
        # 与翼相位对齐误差至多半拍，口径登记）。
        # 占空比连续调制（2026-09-27 阶段二，DUTY_MOD_ON 默认关，与二值
        # 闸门互斥且优先）：δρ 连续取值，经误差扩散累加器映射每翼本拍
        # 拍/停——acc += ρ，acc≥1 则拍并 acc−=1，否则停；平均密度精确
        # 等于 ρ，切换波纹最小。δρ>0（需正滚转）减右翼密度，方向沿用
        # 首日标定（右停左拍得正滚转 +0.00742 N·m，SD_ROLL_DIR=+1）。
        if getattr(f, "DUTY_MOD_ON", False):
            _per = 1.0 / max(self._freq_last, 0.2)
            if self._sd_t_last is None or (t_sim - self._sd_t_last) >= _per:
                self._sd_t_last = t_sim
                # 拍平均角速率：上一整拍的 wx 均值（拍边结算），供本拍
                # 决策使用——冲量控制的时间尺度即拍周期，拍平均对纹波
                # 零敏感且无滤波滞后（R36/R37 教训的物理正解）
                self._wx_pa = (self._wx_acc / self._wx_acc_n
                               if self._wx_acc_n > 0 else self._w_filt[0])
                self._wx_acc = 0.0
                self._wx_acc_n = 0
                _dr = float(np.clip(
                    f.SD_ROLL_DIR * (f.DM_KP * _wrap(roll_des - roll)
                                     - f.DM_KD * getattr(
                                         self, "_wx_pa", self._w_filt[0])),
                    -1.0, 1.0))
                if abs(_dr) < f.DM_DEAD:
                    _dr = 0.0
                # 密度通道俯仰门控（2026-09-28 权限包线定案，默认关）：
                # 权限在俯仰约 −4° 过零、0° 反号（巡航姿态实测 −0.0073
                # N·m）；高于 DM_PITCH_ZERO_DEG 强制归零，低于
                # DM_PITCH_FULL_DEG 全额，中间线性。依据：
                # docs/科目一重跑_俯仰保持版_根因定位.md 第二节。
                if getattr(f, "DUTY_PITCH_GATE_ON", False):
                    _pdg = math.degrees(pitch)
                    _g = float(np.clip(
                        (f.DM_PITCH_ZERO_DEG - _pdg)
                        / (f.DM_PITCH_ZERO_DEG - f.DM_PITCH_FULL_DEG),
                        0.0, 1.0))
                    _dr *= _g
                _rho_L = 1.0 - max(0.0, -_dr)
                _rho_R = 1.0 - max(0.0, +_dr)
                self._dm_acc_L += _rho_L
                self._dm_acc_R += _rho_R
                if self._dm_acc_L >= 1.0:
                    self._sd_gate_L = 1.0
                    self._dm_acc_L -= 1.0
                else:
                    self._sd_gate_L = 0.0
                if self._dm_acc_R >= 1.0:
                    self._sd_gate_R = 1.0
                    self._dm_acc_R -= 1.0
                else:
                    self._sd_gate_R = 0.0
        elif getattr(f, "STROKE_DIFF_ON", False):
            _per = 1.0 / max(self._freq_last, 0.2)
            if self._sd_t_last is None or (t_sim - self._sd_t_last) >= _per:
                self._sd_t_last = t_sim
                _dr = float(np.clip(
                    f.SD_ROLL_DIR * (f.SD_KP * _wrap(roll_des - roll)
                                     - f.SD_KD * self._w_filt[0]),
                    -1.0, 1.0))
                if _dr >= f.SD_DEAD:
                    self._sd_gate_L, self._sd_gate_R = 1.0, 0.0
                elif _dr <= -f.SD_DEAD:
                    self._sd_gate_L, self._sd_gate_R = 0.0, 1.0
                else:
                    self._sd_gate_L = self._sd_gate_R = 1.0
        else:
            self._sd_gate_L = self._sd_gate_R = 1.0

        # 滚转执行量低通（2026-09-29 转弯线 R3 教训，DA_LPF_TAU 默认 0=
        # 不滤波=行为与该通道前逐位一致）：wx 实测含 5.73Hz（≈3×拍频）
        # 大幅值反作用纹波，τ=0.030 的速率滤波对其衰减仅 25%，wx_filt
        # 残余纹波驱动 dA/ail 满幅换边（3.7Hz，R3 实录 6~12s 换边 44
        # 次），平均修正力矩归零，机体钉死在配平坡度。执行端一阶低通
        # 把换边分量滤平、直流分量全通过，同时顺带压尾面伺服反作用。
        _tau_da = getattr(f, "DA_LPF_TAU", 0.0)
        if _tau_da > 0.0:
            _a_da = min(1.0, dt / _tau_da)
            if self._da_lpf is None:
                self._da_lpf = (dA, ail)
            else:
                self._da_lpf = (
                    self._da_lpf[0] + _a_da * (dA - self._da_lpf[0]),
                    self._da_lpf[1] + _a_da * (ail - self._da_lpf[1]))
            dA, ail = self._da_lpf

        dlag = 0.0
        if getattr(f, "WING_MIX_ON", False):
            dlag = float(np.clip(f.WING_MIX_RATIO * dA,
                                 -f.WING_MIX_DLAG_MAX, f.WING_MIX_DLAG_MAX))

        # 频率差动偏航通道（2026-09-27 阶段二，FREQ_DIFF_ON 默认关）：
        # 偏航阻尼经频率差实现——左翼 f+δf、右翼 f−δf，骑行在能量环的
        # 集体拍频之上；秒均升力对频率近常数（阶段一探针：−0.028 N/Hz）
        # 且两翼差动对称，高度环所受扰动为一阶对消。每翼频率硬约束
        # [FREQ_DIFF_FLO, FREQ_DIFF_FHI]（2.2 Hz=失效区边界，约束一）。
        # 权限依据：ΔJ_Tz≈+0.0052 N·m·s/拍 每 Δf=0.1 Hz（巡航点实测）。
        _df = 0.0
        if getattr(f, "FREQ_DIFF_ON", False):
            # 滚转源档（2026-09-29 转弯线：FD_ROLL_SOURCE=1 时频率差动
            # 改做滚转执行——两翼拍频差产生连续升力差与滚转力矩，
            # 无停拍瞬态；依据升力律 dL̄/df=2·1.95·f，Δf=0.02Hz 即得
            # 与尾翼差动满偏同量级的滚转力矩，总升力两翼对称对消。
            # 反馈用拍平均角速率（与逐拍决策同口径，R36~R41 教训）。
            if getattr(f, "FD_ROLL_SOURCE", False):
                _df = float(np.clip(
                    f.FD_DIR * (f.FD_KP * _wrap(roll_des - roll)
                                - f.FD_KD * getattr(self, "_wx_pa",
                                                    self._w_filt[0])),
                    -f.FD_MAX, f.FD_MAX))
            else:
                _df = float(np.clip(
                    f.FD_DIR * (f.FD_KP * _wrap(self.yaw_ref - yaw)
                                - f.FD_KD * self._w_filt[2]),
                    -f.FD_MAX, f.FD_MAX))
            _df = _freq_diff_clamp(_df, freq, f)
        self._df_cmd = _df

        # 拍频调制注入口（辨识产线五通道之四，FREQ_PROBE_AMP>0 启用，默认 0=
        # 历史行为；只叠加在输出频率上，斜坡与 TECS 状态不受注入污染，相位
        # 积分器随输出频率自然调制=物理通道）
        _pf = getattr(f, "FREQ_PROBE_AMP", 0.0)
        _freq_out = freq
        if _pf > 0.0 and t_sim is not None:
            if getattr(f, "MULTISINE_ON", False):
                _ms_f = multisine(f, "freq", t_sim) * _pf
            else:
                _ms_f = _pf * math.sin(
                    2.0 * math.pi * getattr(f, "AIL_PROBE_FREQ", 2.33) * t_sim)
            _freq_out = float(np.clip(freq + _ms_f,
                                      cfg.MANUAL_FREQ_MIN, f.FREQ_MAX_AP))
        # 翼幅值差动注入口（五通道之五，DA_PROBE_AMP>0 启用，默认 0；
        # 叠加在返回值上，dlag/逐拍通道等内部消费量不受注入污染）
        _pd = getattr(f, "DA_PROBE_AMP", 0.0)
        _dA_out = dA
        if _pd > 0.0 and t_sim is not None:
            if getattr(f, "MULTISINE_ON", False):
                _ms_d = multisine(f, "da", t_sim) * _pd
            else:
                _ms_d = _pd * math.sin(
                    2.0 * math.pi * getattr(f, "AIL_PROBE_FREQ", 2.33) * t_sim)
            _dA_out = float(np.clip(dA + _ms_d, -f.DA_MAX, f.DA_MAX))

        # ── 相位域波形发生器（I-172 接入，I-139 定稿 Hopf 基底）──
        # WAVEGEN_ON=False（默认）时本段零执行，行为与现行逐位一致。
        # 开启时以 Hopf 振荡器单步推进产生本拍拍频调制量：发生器相位速
        # 率经慢度剖面调制后与现行拍频指令融合，输出即时频率供下游翼
        # 模型消费。实现为 ltv/wavegen_family.py hopf_step 的标量移植
        # （纯 numpy，无 Isaac 依赖，出处 I-139 预登记）。
        _wg_out = None
        if getattr(f, "WAVEGEN_ON", False) and dt > 0.0:
            _wg_A = getattr(f, "WAVEGEN_AMBDA", -1.0)
            if _wg_A <= 0.0:
                _wg_A = np.radians(cfg.FlapKinematicsConfig.AMPLITUDE)
            _wg_k = max(getattr(f, "WAVEGEN_KAPPA", 4.0), 0.5)
            _wg_b1 = getattr(f, "WAVEGEN_B1", 0.0)
            _wg_b3 = getattr(f, "WAVEGEN_B3", 0.0)
            if self._wavegen_state is None:
                # 初值取目标幅值单位圆（收敛最快路径，I-139 预登记口径）
                self._wavegen_state = (_wg_A, 0.0)
            _sx, _sy = self._wavegen_state
            _r2 = _sx * _sx + _sy * _sy
            _th = math.atan2(_sy, _sx)
            _u = _th / (2.0 * math.pi) if _th >= 0.0 \
                else _th / (2.0 * math.pi) + 1.0
            _H = 1.0 + _wg_b1 * math.sin(2.0 * math.pi * _u) \
                + _wg_b3 * math.sin(6.0 * math.pi * _u)
            _w = 2.0 * math.pi * max(_freq_out, 0.5) / max(_H, 0.1)
            _rad = _wg_k * (_wg_A * _wg_A - _r2)

            def _wg_rhs(zx, zy):
                return (_rad * zx - _w * zy, _rad * zy + _w * zx)

            _k1x, _k1y = _wg_rhs(_sx, _sy)
            _k2x, _k2y = _wg_rhs(_sx + 0.5 * dt * _k1x,
                                 _sy + 0.5 * dt * _k1y)
            _k3x, _k3y = _wg_rhs(_sx + 0.5 * dt * _k2x,
                                 _sy + 0.5 * dt * _k2y)
            _k4x, _k4y = _wg_rhs(_sx + dt * _k3x, _sy + dt * _k3y)
            _sx2 = _sx + dt * (_k1x + 2 * _k2x + 2 * _k3x + _k4x) / 6.0
            _sy2 = _sy + dt * (_k1y + 2 * _k2y + 2 * _k3y + _k4y) / 6.0
            self._wavegen_state = (_sx2, _sy2)
            # 发生器输出：瞬时相位速率换算拍频调制量（相对指令拍频的
            # 归一偏差乘指令拍频），叠加回输出拍频。默认 b1=b3=0 时
            # _H=1、_w=2πf，发生器输出与现行拍频同值（差异仅来自幅值
            # 收敛瞬态，稳态为零）。
            _th2 = math.atan2(_sy2, _sx2)
            _dth = (_th2 - _th + math.pi) % (2.0 * math.pi) - math.pi
            _f_inst = _dth / (2.0 * math.pi) / dt
            _wg_out = float(_f_inst)

        return {"freq": _freq_out, "elevator": elev, "aileron": ail, "rudder": rud,
                "dA": _dA_out, "dlag": dlag, "gate_L": self._sd_gate_L,
                "gate_R": self._sd_gate_R, "dfreq": self._df_cmd,
                "vxd": vxd, "v_eff": v_eff, "wyd": wyd,
                "my_dem": my,
                "alpha_ff": getattr(self, "alpha_ff_last", 0.0),
                "roll": roll, "pitch": pitch, "roll_des": roll_des,
                "pitch_des": pitch_des,
                "wavegen_freq": _wg_out}
