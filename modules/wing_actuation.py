"""wing_actuation — 拍动关节驱动环
===================================
2026-09-30 T6.3 拆分：_drive_wing_joints（含内部函数 cfg_EXPERIMENT_MODE
与随迁计数器 _DRIVE_N）自 main_sim.py 机械迁出，逻辑、数值、默认值、
语句顺序零改动。

命名空间适配（唯一的非逐字改动）：原函数体内经 main_sim 模块 globals()
读写的跨函数共享量——_WING_TGT_CACHE（示波器/数据流读取）、_DA_CACHE /
_FOLD_T_CACHE / _CTRL_MODE_CACHE / _WING_STATE_CACHE（数据流读取）、
_SIDE_PULSE_ON / _T / _DVY / _DONE（sim_env_bridge 写入）、_FW_IID_ULP
（main() 装订）、_DP_STATE（_apply_tether_and_dp 每步整体重绑定）——
迁出后统一改经 _G（main_sim 宿主命名空间字典）访问同一批变量，运行时
行为不变。计数器 _DRIVE_N 仅本函数使用，随迁为本模块全局（初值 0 与
原 main_sim 模块级定义一致）；_DRIVE_T0 原即为已声明未使用的死名，原样
保留 global 声明。

依赖说明：PHYSICS_DT / LOG_DIR 为 config.sim_config 纯常量，顶层导入；
math / numpy / 拍动参数保持函数内导入（与原文件一致）。
"""

import os
import sys
import logging

from config.sim_config import PHYSICS_DT, LOG_DIR

logger = logging.getLogger("main_sim")

# 宿主命名空间（main_sim 以 __main__ 运行或被 import 时的模块字典）：
# 共享缓存与 _DP_STATE 住在该命名空间
_G = (sys.modules.get("main_sim") or sys.modules["__main__"]).__dict__

_DRIVE_N = 0

# I-210 无效输出限次告警状态
_WG_WARNED = set()


def select_effective_freq(ap_out):
    """统一有效频率选择（事项 I-210，wing_actuation 与 mock_backend 共用）。

    默认 WAVEGEN_DRIVE_ON=False：与原路径逐位一致，实际频率即名义
    ap_out["freq"]，控制器 Hopf 输出 wavegen_freq 不被消费。开启且为
    自动模式输出时：wavegen_freq 有效（有限、正值）则实际采用之，越界
    限幅到自动驾驶仪频率界 [FREQ_AP_MIN, FREQ_MAX_AP]；无效（None、
    非有限、非正）回退名义频率并限次告警。手动模式优先级由调用侧分支
    保证，本函数只在自动模式分支调用。名义路径的 FREQ_SLEW
    由控制器内部施加；这里对 wavegen 只做值域限幅，没有输出速率保证。
    有限取证保留原调制，不增加第二级斜坡；不能据此宣称执行器带宽合格。返回
    (effective_freq, nominal_freq, source)，source 取 nominal、wavegen、
    wavegen_clamped 或 fallback_invalid。
    """
    import math
    import config.sim_config as _c
    nominal = float(ap_out["freq"])
    wg = ap_out.get("wavegen_freq")
    if not getattr(_c.FlightControllerConfig, "WAVEGEN_DRIVE_ON", False):
        return nominal, nominal, "nominal"
    ok = isinstance(wg, (int, float)) and not isinstance(wg, bool)         and math.isfinite(wg) and wg > 0.0
    if not ok:
        global _WG_WARNED
        if "invalid" not in _WG_WARNED:
            _WG_WARNED.add("invalid")
            print("[WG-DRIVE] wavegen_freq 无效（%r），回退名义频率 %s"
                  % (wg, nominal), flush=True)
        return nominal, nominal, "fallback_invalid"
    lo = float(_c.FlightControllerConfig.FREQ_AP_MIN)
    hi = float(_c.FlightControllerConfig.FREQ_MAX_AP)
    v = float(wg)
    if v < lo or v > hi:
        clamped = min(max(v, lo), hi)
        if "clamped" not in _WG_WARNED:
            _WG_WARNED.add("clamped")
            print("[WG-DRIVE] wavegen_freq %s 越界，限幅至 %s"
                  % (v, clamped), flush=True)
        return clamped, nominal, "wavegen_clamped"
    return v, nominal, "wavegen"


def _i210_observe_drive(art, wk, aero_cb, ap_out, man_ch, step, t,
                         consumed_freq, evaluated, branch, submitted,
                         api_status="api_succeeded", api_error=None):
    """Read-only receipt; measurements precede the next solver step."""
    if os.environ.get("FLAPPER_I210_EVIDENCE") != "1":
        return
    try:
        from modules import i210_evidence
        import config.sim_config as cfg
        actual, errors = {}, {}
        for key, getter in (("joint_position_rad", "get_joint_positions"),
                            ("joint_velocity_rad_s", "get_joint_velocities")):
            try:
                actual[key] = getattr(art, getter)()
            except Exception as exc:
                actual[key] = None
                errors[key] = repr(exc)
        model = getattr(aero_cb, "model", None)
        wings = {}
        for side, name in (("L", "wing_left"), ("R", "wing_right")):
            wing = getattr(model, name, None)
            wings[side] = {key: getattr(wing, key, None) for key in
                ("frequency", "omega", "amplitude", "pitch_lag_s",
                 "stroke_gate", "_tau_state", "_t_prev")}
        i210_evidence.observe(_G.get("_FW_IID_REC"), "drive", {
            "main_step": step, "main_t_s": t, "phys_dt_s": PHYSICS_DT,
            "stage": "target_api_attempt_before_next_solve"}, {
            "ap_out": ap_out, "manual_channel": man_ch,
            "selection": branch, "frequency_consumed_by_evaluate_hz": consumed_freq,
            "frequency_after_set_hz": wk.frequency,
            "evaluated_kinematics": evaluated,
            "joint_names": list(art.joint_names),
            "target_api_float32_rad": submitted,
            "target_api_status": api_status, "target_api_error": api_error,
            "actual_readback": actual, "query_errors": errors,
            "aero_wing_readback": wings,
            "effective_switches": {key: getattr(cfg.FlightControllerConfig, key, None)
                for key in ("WAVEGEN_ON", "WAVEGEN_DRIVE_ON", "WAVEGEN_B1", "WAVEGEN_B3")},
            "ordering": "This target is for the next solve; actual joint reads are current state, not target attainment."})
    except Exception as exc:
        logger.warning("I-210 驱动观测失败: %s", exc)




def _drive_wing_joints(world, art, wk, man_ch=None, aero_cb=None, ap_out=None,
                       step_count=0):
    """拍动驱动：WingKinematics → 9 关节位置目标（ArticulationAction）。

    自动模式：flap=±φ, twist=α(同号), fold=±F, 尾舵=配平+慢速摆动。
    手动模式（man_ch 非 None，课题组真机逻辑）：
      油门→拍频（FREQ_MIN~MAX），升降→尾同向，副翼→尾差动，方向→方向舵偏转；
      舵偏同步送入 aero_cb.tail_body 产生真实气动力矩。
    """
    import math
    import numpy as np
    from config.sim_config import (WING_DRIVE_CONFIG, MANUAL_FREQ_MIN,
                                   MANUAL_FREQ_MAX, MANUAL_ELEV_GAIN,
                                   MANUAL_AIL_GAIN, MANUAL_RUDD_GAIN,
                                   STARTUP_AMP_RAMP_S)

    def cfg_EXPERIMENT_MODE():
        import config.sim_config as _c
        return getattr(_c, "EXPERIMENT_MODE", "")
    try:
        names = art.joint_names
        if names is None or len(names) == 0:
            return
        t = step_count * PHYSICS_DT   # 权威物理时钟（第12轮：GUI 下 current_time 膨胀）
        _i210_on = os.environ.get("FLAPPER_I210_EVIDENCE") == "1"
        _i210_consumed = wk.frequency if _i210_on else None
        _i210_branch = "unchanged"
        w = wk.evaluate(t)
        phi, alpha = w["phi"], w["alpha"]
        fold = WING_DRIVE_CONFIG["fold_amp"] * 0.5 * \
            (1.0 + math.sin(wk.omega * t + 2.2))
        # 拍幅软启动（2026-09-19）：拍幅/扭转/折叠从 0 斜坡到全幅，消除
        # t=0 位置驱动目标给机体的反作用冲击（实测起动 0.12s 机体即带
        # 1.3~1.8 m/s 水平速度，该冲击是泵能回路的首要驱动来源）。
        # 注意：本函数没有 cfg 别名（顶层是按名 import），2026-09-19 曾因
        # 误写 cfg.STARTUP_AMP_RAMP_S 每帧 NameError 未被上报，翅膀驱动整段
        # 失效三个 run（机翼停摆状态坠机，拍频恒 2.5Hz 无任何调节）。
        _ramp = min(1.0, t / max(STARTUP_AMP_RAMP_S, 0.05))
        phi, alpha, fold = phi * _ramp, alpha * _ramp, fold * _ramp

        if man_ch is not None and (cfg_EXPERIMENT_MODE()
                                   or abs(man_ch["throttle"]) > 0.05):
            # 手动油门优先。试验模式下油门为绝对指令（含 0 位=2.5Hz 中点），
            # 否则 thr=0 会漏到 DP 分支：DP 读取状态锁定台架的单步积分速度抖动，
            # 把 2.5Hz 拖成 2.2~2.4Hz 漂移（2026-09-19 dastep 实测证实）
            freq = MANUAL_FREQ_MIN + (MANUAL_FREQ_MAX - MANUAL_FREQ_MIN) \
                * (man_ch["throttle"] + 1.0) / 2.0
            _G["_CTRL_MODE_CACHE"] = 2
            wk.set_frequency(freq)
            _i210_branch = "manual"
        elif ap_out is not None:
            # 有效频率选择（事项 I-210）：默认关闭时与原路径逐位一致；
            # 开启且 wavegen_freq 有效时关节侧实际采用 Hopf 输出（下方
            # 气动集体同步与 dfreq 差动均读 wk.frequency，随之统一）。
            # 名义与实际频率分列记入 _WING_FREQ_CACHE 供核对，本步设置
            # 下一步求值生效（既有相位积分次序不变：set_frequency 只改
            # 频率不改相位）。
            _f_eff, _f_nom, _f_src = select_effective_freq(ap_out)
            wk.set_frequency(_f_eff)      # 飞行控制器（有效频率）
            _G["_WING_FREQ_CACHE"] = (_f_nom, _f_eff, _f_src)
            _i210_branch = _f_src
        elif _G["_DP_STATE"]["freq"] is not None:
            wk.set_frequency(_G["_DP_STATE"]["freq"])   # 定高 DP
            _G["_CTRL_MODE_CACHE"] = 0
            _i210_branch = "dp"

        # 力模型拍频同步（2026-09-18 修复）：力模型内部的 WingKinematics 与关节
        # 驱动 _wk 是两个独立实例，此前扫频/手动/自动驾驶仪改拍频只改了关节侧，
        # 气动力始终按默认 2.5Hz 计算 → 扫频辨识测到的 L̄(f) 是无变化的错误曲线。
        if aero_cb is not None and hasattr(aero_cb, "model"):
            try:
                aero_cb.model.set_wing_command(frequency=float(wk.frequency))
            except Exception:
                pass
        # 频率差动注入（2026-09-27 阶段二，默认关）：控制器输出 dfreq 时，
        # 在集体拍频之上对力模型两翼分别设频 f±δf（必须在上面集体设频
        # 之后覆盖）；相位为积分状态，set_frequency 只改频率不改相位。
        # 关节视觉频率暂不跟随（视觉同步待办，与闸门视觉口径一致）。
        if aero_cb is not None and hasattr(aero_cb, "model") \
                and ap_out is not None:
            _dfq = float(ap_out.get("dfreq", 0.0) or 0.0)
            if abs(_dfq) > 1e-12:
                try:
                    _fc = float(wk.frequency)
                    aero_cb.model.wing_left.set_frequency(_fc + _dfq)
                    aero_cb.model.wing_right.set_frequency(_fc - _dfq)
                except Exception:
                    pass

        if ap_out is not None:
            elev = ap_out["elevator"]
            ail = ap_out["aileron"]
            rudd = ap_out["rudder"]
            tail_L, tail_R = elev + ail, elev - ail
            tail_yaw = rudd
        elif man_ch is not None:
            elev = MANUAL_ELEV_GAIN * man_ch["elevator"]
            ail = MANUAL_AIL_GAIN * man_ch["aileron"]
            rudd = MANUAL_RUDD_GAIN * man_ch["rudder"]
            tail_L, tail_R = elev + ail, elev - ail
            tail_yaw = rudd
        else:
            tail_yaw = WING_DRIVE_CONFIG["tail_yaw_amp"] * \
                math.sin(wk.omega * t * 0.5)
            tail_L = tail_R = WING_DRIVE_CONFIG["tail_trim"]

        dA = 0.0
        if ap_out is not None:
            dA = ap_out.get("dA", 0.0)
        elif man_ch is not None and "da" in man_ch:
            dA = float(man_ch.get("da", 0.0))   # 试验通道：翼差动直接指令 (rad)
        _G["_DA_CACHE"] = float(dA)
        _G["_FOLD_T_CACHE"] = float(fold)
        if ap_out is not None:
            _G["_CTRL_MODE_CACHE"] = 1
        # dA 同步注入气动模型左右翼幅（力模型必须获得不对称信息，否则差动不产生力矩）
        # 事项 I-129 原子发布：左右成对的离散输入改为单次元组赋值发布，
        # 由气动回调线程在求值入口一次性快照，消除逐翼两条赋值之间的
        # 回调插入窗口（分岔事故根因）。
        if aero_cb is not None and hasattr(aero_cb, "model"):
            try:
                import config.sim_config as _cfg
                _A0 = float(_cfg.FlapKinematicsConfig.AMPLITUDE) * math.pi / 180.0
                aero_cb.model.amp_cmd = (_A0 * _ramp + dA,
                                         _A0 * _ramp - dA)
            except Exception:
                pass
        # 逐拍差动闸门注入（2026-09-27 冲量积分线）：控制器的拍/停决策
        # 经原子元组发布到两翼运动学；关闭态 gate 恒 1.0，行为逐位不变
        if aero_cb is not None and hasattr(aero_cb, "model") and ap_out is not None:
            try:
                aero_cb.model.sd_gate_cmd = (
                    float(ap_out.get("gate_L", 1.0)),
                    float(ap_out.get("gate_R", 1.0)))
            except Exception:
                pass
        # 回放档案缓存（2026-09-28 用户令全自由度可见）：每翼频率/闸门/
        # 滞后差动写入全局缓存，供数据流行尾追加列落盘；只进记录不改
        # 行为，缺省值（闸门 1、dlag 0）与关闭态一致
        try:
            _mdl = getattr(aero_cb, "model", None) if aero_cb is not None else None
            _G["_WING_STATE_CACHE"] = [
                float(getattr(_mdl.wing_left, "frequency", float("nan"))) if _mdl is not None else float("nan"),
                float(getattr(_mdl.wing_right, "frequency", float("nan"))) if _mdl is not None else float("nan"),
                float(getattr(_mdl.wing_left, "stroke_gate", 1.0)) if _mdl is not None else 1.0,
                float(getattr(_mdl.wing_right, "stroke_gate", 1.0)) if _mdl is not None else 1.0,
                float(ap_out.get("dlag", 0.0)) if ap_out is not None else 0.0,
            ]
        except Exception:
            _G["_WING_STATE_CACHE"] = None
        # 偏航抵消组合通道注入（2026-09-27）：控制器输出的 dlag 同步写入
        # 气动模型左右翼的俯仰滞后参数（左翼加、右翼减）；只在力模型层
        # 生效，关节可视化目标不跟随（俯仰程序差异不进关节目标）。
        if aero_cb is not None and hasattr(aero_cb, "model") \
                and ap_out is not None and ap_out.get("dlag"):
            try:
                import config.sim_config as _cfg_lg
                _dl = float(ap_out["dlag"])
                _lag0 = float(_cfg_lg.FlapKinematicsConfig.PITCH_LAG_S)
                aero_cb.model.wing_left.pitch_lag_s = _lag0 + _dl
                aero_cb.model.wing_right.pitch_lag_s = _lag0 - _dl
            except Exception:
                pass
        # 侧滑脉冲试验（默认关闭，2026-09-27）：t 越过阈值时一次性给
        # 机体世界系横向速度加脉冲，考核横侧通道的恢复能力。
        try:
            if _G.get("_SIDE_PULSE_ON") \
                    and not _G.get("_SIDE_PULSE_DONE") \
                    and t >= _G["_SIDE_PULSE_T"]:
                _G["_SIDE_PULSE_DONE"] = True
                _view = getattr(aero_cb, "flapper_view", None)
                if _view is None:
                    raise RuntimeError("flapper_view 不可用")
                _vel = np.asarray(_view.get_velocities(),
                                  dtype=np.float32).reshape(1, 6).copy()
                _vel[0, 1] += _G["_SIDE_PULSE_DVY"]
                _view.set_velocities(_vel)
                print(f"[SIDESLIP] vy += {_G['_SIDE_PULSE_DVY']} m/s "
                      f"@t={t:.2f}", flush=True)
        except Exception as _e:
            print(f"[SIDESLIP] 施加失败: {_e}", flush=True)
        # dA 关节目标幅值语义开关（事项 I-190，默认关=旧角度偏置语义逐位
        # 不变）：契约定义 dA 为左翼幅值加 dA、右翼幅值减 dA。开启时关节
        # 目标的差动增量与气动侧 amp_cmd 用同一物理定义——增量 = ±dA·sin(ph)，
        # ph 取本层运动学相位（stroke_unit 在同一 t 只读复读相位，不额外
        # 推进）；增量与气动侧同口径不随启动斜坡衰减。关闭时 _dA_j=dA，
        # 目标表达式与旧代码逐位相同。
        import config.sim_config as _cfg190
        _dA_j = dA
        if getattr(_cfg190.FlapKinematicsConfig, "DA_JOINT_AMP_ON", False):
            _dA_j = dA * wk.stroke_unit(t)
        target = {
            "flap_L": phi + _dA_j, "flap_R": -(phi - _dA_j),
            "twist_L": alpha, "twist_R": alpha,
            "fold_L": fold, "fold_R": -fold,
            "tail_yaw": tail_yaw, "tail_L": tail_L, "tail_R": tail_R,
        }
        vec = [float(target.get(n, 0.0)) for n in names]
        # FW-IID 仪器校验注入口（2026-09-29 方案阶段零）：在装订步把指定
        # 关节目标的 32 位浮点位型翻转最低有效位一次（恰一个末位单位），
        # 供台账比较器做端到端首差校验；未装订时零行为。
        _ulp = _G.get("_FW_IID_ULP")
        if _ulp is not None and step_count == _ulp[0]:
            import struct as _st_u
            if _ulp[1] in names:
                _ui = names.index(_ulp[1])
                _ubits = _st_u.unpack("<I", _st_u.pack("<f", float(vec[_ui])))[0] ^ 1
                vec[_ui] = _st_u.unpack("<f", _st_u.pack("<I", _ubits))[0]
                print(f"[ULP-INJ] step={step_count} joint={_ulp[1]} "
                      f"目标位型末位已翻转（写入值 {vec[_ui]:.9f}）", flush=True)
            else:
                print(f"[ULP-INJ] 关节 {_ulp[1]} 不在关节表中，注入未执行",
                      flush=True)
            _G["_FW_IID_ULP"] = None   # 只注入一次
        _G["_WING_TGT_CACHE"] = vec
        # 显式按名写目标（apply_action 的未设字段在部分版本会覆写出问题）
        _submitted_target = np.asarray([vec], dtype=np.float32)
        _i210_target_copy = _submitted_target.copy() if _i210_on else None
        try:
            art.set_joint_position_targets(
                positions=_submitted_target,
                joint_names=names)
        except Exception as _target_exc:
            if _i210_on:
                _i210_observe_drive(art, wk, aero_cb, ap_out, man_ch, step_count,
                    t, _i210_consumed, w, _i210_branch, _i210_target_copy,
                    "api_failed", repr(_target_exc))
            raise  # Retain the original drive exception path.
        if _i210_on:
            _i210_observe_drive(art, wk, aero_cb, ap_out, man_ch, step_count,
                t, _i210_consumed, w, _i210_branch, _i210_target_copy)
        global _DRIVE_N, _DRIVE_T0
        _DRIVE_N += 1
        if _DRIVE_N % 120 == 1:
            p = np.asarray(art.get_joint_positions()).flatten()
            i_fl = names.index("flap_L") if "flap_L" in names else 0
            i_fo = names.index("fold_L") if "fold_L" in names else 0
            i_tw = names.index("twist_L") if "twist_L" in names else 0
            tgt_fo = target.get("fold_L", 0.0)
            tgt_tw = target.get("twist_L", 0.0)
            with open(os.path.join(LOG_DIR, "drive_diag.txt"), "a", encoding="utf-8") as f:
                f.write("n=%d t=%.2f tgt_flap=%+.3f act_flap=%+.3f "
                        "tgt_fold=%+.3f act_fold=%+.3f tgt_tw=%+.3f act_tw=%+.3f\n"
                        % (_DRIVE_N, t, phi, p[i_fl], tgt_fo, p[i_fo], tgt_tw, p[i_tw]))
                f.flush()

        # 尾舵偏 → 尾翼气动（自动驾驶仪/手动模式的控制面）
        if aero_cb is not None and hasattr(aero_cb, "tail_body"):
            if ap_out is not None:
                aero_cb.tail_body.set_command(
                    elevator=ap_out["elevator"],
                    aileron=ap_out["aileron"],
                    rudder=ap_out["rudder"])
            elif man_ch is not None:
                aero_cb.tail_body.set_command(
                    elevator=MANUAL_ELEV_GAIN * man_ch["elevator"],
                    aileron=MANUAL_AIL_GAIN * man_ch["aileron"],
                    rudder=MANUAL_RUDD_GAIN * man_ch["rudder"])
            else:
                aero_cb.tail_body.set_command(0.0, 0.0, 0.0)
    except Exception as e:
        # 驱动环异常必须进普通日志：曾以 debug 级静默隐藏 NameError，
        # 翅膀驱动失效三个 run 未被察觉（2026-09-19 机翼停摆事故）
        logger.warning("拍动驱动异常: %s", e)
