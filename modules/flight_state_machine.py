# -*- coding: utf-8 -*-
"""
flight_state_machine.py — 两段飞行状态机调度器（任务三，2026-09-25）
====================================================================
规格出处：docs/任务三_状态机规格.md。状态单向流转：

  LAUNCH（起飞段）→ TRANSIT（切换瞬态，2 s 斜坡）→ CRUISE（巡航段）

  【第三十一班·签核第16项核对记档】下文 0.436 rad 为发射窗口时代值
  （5c：4.0 m/s / 25° 窗口中心）；第六跑起弹射口径已改为配平态直入
  （LAUNCH_VX=3.5 / LAUNCH_PITCH=0.374=PITCH_TRIM），本模块处于休眠态
  （config 无 FlightStateMachineConfig，flight_controller 经 getattr 防护
  走单段路径）。重启两段状态机前须把 LAUNCH_PITCH_TGT 与激活阈值组按
  配平直入口径重设（0.436→0.374），否则发射段目标俯仰与配平基准偏差
  +0.062 rad。

  LAUNCH：俯仰保持锁定律——目标俯仰 0.436 rad 经 2 s 斜坡；反馈增益取
          任务二巡航增益减半（低带宽防 bang-bang）；高度/速度环冻结
          （拍频锁定 1.25 Hz 不推频）；升降舵限幅 ±0.30 rad 沿用 DELTA_MAX。
  切换 ：三条件同真且连续保持十拍——vx≥3.3 m/s；z≥3.5 m；
          |pitch−0.374|≤0.087 rad。"拍"取控制器更新拍（120 Hz 下
          十拍约 0.083 s），与离散控制"一拍=一个采样周期"的通行口径一致。
  TRANSIT：全部参数（目标俯仰、增益 KP_A/KI_A/PITCH_DAMP、PITCH_TRIM）
          经 2 s 线性斜坡过渡，禁止阶跃。PITCH_TRIM 含于巡航段俯仰目标
          （PITCH_TRIM+TECS 项）之内，目标整体参与斜坡即完成其过渡。
  CRUISE：清单 v2 十七处工作点 + 任务二保调度增益。

本文件只做状态/计数/斜坡调度，不产生任何控制量；控制律由
modules/flight_controller.py 的状态分支执行（规格指定的实现位置：
modules/ 平行新增调度器文件 + flight_controller 接一个状态分支，
平行新增不动现有单段路径）。

时间基准：控制器每更新拍传入 dt（由仿真时间差分），本类内部累计，
不读墙钟——与 FlightController.update 的 t_sim 口径一致（RTF≠1 时
墙钟会让斜坡/计数的时间尺度漂移）。
"""
import config.sim_config as cfg


def _lerp(a, b, s):
    return a + (b - a) * s


class FlightStateMachine:
    """LAUNCH→TRANSIT→CRUISE 单向状态机。

    属性：
      state      当前状态（"LAUNCH"/"TRANSIT"/"CRUISE"）
      armed_now  锁定律激活事件标志：仅激活当拍为 True（控制器据此把
                 pitch_des 斜坡状态同步到当前实际俯仰，消除释放瞬间的
                 指令阶跃）
    """

    LAUNCH = "LAUNCH"
    TRANSIT = "TRANSIT"
    CRUISE = "CRUISE"

    # 锁定律激活检测阈值：台架锁定期（STARTUP_PIN_S 内）vx≈0、pitch≈0；
    # 发射事件（main_sim 释放块注入 LAUNCH_VX 初速并做 LAUNCH_PITCH 姿态
    # 同步）后 vx≈4.0 m/s、pitch≈0.436 rad，双阈值任一越线即判激活。
    _ARM_VX = 1.0     # m/s
    _ARM_PITCH = 0.2  # rad

    def __init__(self):
        c = cfg.FlightStateMachineConfig
        self._c = c
        self.state = self.LAUNCH
        self.armed_now = False
        self._t = 0.0          # 内部累计仿真时间（控制器节拍求和）
        self._hold = 0         # 切换条件连续满足拍计数（任一失真即清零）
        self._t_arm = None     # 锁定律激活时刻（None=尚未激活）
        self._p_anchor = 0.0   # 激活时刻实际俯仰（目标斜坡锚点）
        self._t_switch = None  # 切换瞬态起点时刻
        # 切换瞬间三条件读数（供控制台留档与事后归因）
        self.switch_vx = None
        self.switch_z = None
        self.switch_pitch = None

    # ── 每控制器更新拍调用一次 ──────────────────────────────
    def tick(self, dt, vx, z, pitch):
        """推进状态机。dt为拍间隔（s），vx/z/pitch 为滤波后反馈读数。"""
        c = self._c
        self._t += float(dt)
        self.armed_now = False

        if self._t_arm is None:
            # 激活前目标跟随实际俯仰（台架锁定期保持零误差、无积分漂移）
            self._p_anchor = float(pitch)
            if abs(vx) > self._ARM_VX or abs(pitch) > self._ARM_PITCH:
                self._t_arm = self._t
                self._p_anchor = float(pitch)
                self.armed_now = True
                print("[FSM] LAUNCH 俯仰锁定律激活: t=%.3f s, "
                      "anchor_pitch=%+.3f rad, vx=%+.2f m/s"
                      % (self._t, self._p_anchor, vx), flush=True)

        if self.state == self.LAUNCH:
            ok = (vx >= c.SWITCH_VX_MIN
                  and z >= c.SWITCH_Z_MIN
                  and abs(pitch - c.SWITCH_PITCH_REF) <= c.SWITCH_PITCH_TOL)
            self._hold = self._hold + 1 if ok else 0
            if self._hold >= c.SWITCH_HOLD_TICKS:
                self.state = self.TRANSIT
                self._t_switch = self._t
                self.switch_vx = float(vx)
                self.switch_z = float(z)
                self.switch_pitch = float(pitch)
                print("[FSM] 切换条件满足（连续 %d 拍）: t=%.3f s, "
                      "vx=%.2f m/s, z=%.2f m, pitch=%+.3f rad → 2 s 斜坡开始"
                      % (c.SWITCH_HOLD_TICKS, self._t, vx, z, pitch),
                      flush=True)
        elif self.state == self.TRANSIT:
            if self.blend() >= 1.0:
                self.state = self.CRUISE
                print("[FSM] 切换瞬态完成: t=%.3f s → CRUISE"
                      % self._t, flush=True)
        # CRUISE 为终态：单向流转，条件失效不回退（规格：两段）
        return self.state

    # ── 斜坡/参数查询（控制器分支调用） ──────────────────────
    def blend(self):
        """切换瞬态进度 s∈[0,1]：0=纯起飞段律，1=纯巡航段律。"""
        if self._t_switch is None:
            return 0.0
        return min(1.0, (self._t - self._t_switch)
                   / max(self._c.SWITCH_RAMP_S, 1e-6))

    def launch_pitch_target(self):
        """起飞段目标俯仰（rad）：激活时刻实际俯仰 → 0.436 的 2 s 线性斜坡。

        激活前返回当前锚点（即实际俯仰，保持零误差）。发射姿态同步使
        锚点≈0.436，斜坡退化为常值；锚点偏离目标时斜坡保证指令无阶跃。
        """
        c = self._c
        if self._t_arm is None:
            return self._p_anchor
        k = min(1.0, (self._t - self._t_arm) / max(c.LAUNCH_RAMP_S, 1e-6))
        return _lerp(self._p_anchor, c.LAUNCH_PITCH_TGT, k)

    def gains(self):
        """分段反馈增益 (KP_A, KI_A, PITCH_DAMP)，切换瞬态内线性插值。"""
        c = self._c
        s = self.blend()
        return (_lerp(c.LAUNCH_KP_A, c.CRUISE_KP_A, s),
                _lerp(c.LAUNCH_KI_A, c.CRUISE_KI_A, s),
                _lerp(c.LAUNCH_PITCH_DAMP, c.CRUISE_PITCH_DAMP, s))
