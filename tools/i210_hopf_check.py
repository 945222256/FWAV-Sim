# -*- coding: utf-8 -*-
"""i210_hopf_check.py — I-210 Hopf 输出接入实际翼驱动的隔离验证套。

判据（冻结）：既有 Hopf 输出在批准开关和条件下实际进入关节侧与气动侧
频率路径，默认关闭兼容、非默认动作、真实消费者故障反例和实际翼动作取
证逐条通过。

方法：驱动真实生产函数 modules/wing_actuation._drive_wing_joints 与
select_effective_freq；伪件仅在关节接口与气动回调两个外部边界（与 Doro
探针同法），不启动物理仿真。覆盖：默认关闭逐位回归、开启有效消费（关
节侧、气动集体、dfreq 差动统一）、零值、负值、减半、越界限幅、手动优
先、无效回退、停拍、相位次序、名义与实际分列、六百步调制序列、故意
断开消费者反例（检查必须失败）。mock_backend 复用同一选择函数作结构
性佐证（其逐位行为由同函数默认分支保证）。

用法：python tools/i210_hoph_check.py   （退出码零为全部通过）
"""
import datetime
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# A new run must not overwrite the historical delivery or Doro's reviewed
# records. All freshly produced data and negative cases share this run root.
CHECK_OUTPUT_ROOT = os.path.join(ROOT, "logs", "i210_check_runs",
    datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    + "_%s" % os.getpid())

import config.sim_config as C
from modules import wing_actuation as WA
from modules.aero.kinematics import WingKinematics

JOINTS = ["flap_L", "flap_R", "twist_L", "twist_R",
          "fold_L", "fold_R", "tail_yaw", "tail_L", "tail_R"]
PHYS_DT = 1.0 / 120.0
RESULTS = []
_SW = C.FlightControllerConfig


class FakeArt:
    def __init__(self):
        self.joint_names = list(JOINTS)
        self.targets = None

    def set_joint_position_targets(self, positions, joint_names):
        self.targets = (positions.copy(), list(joint_names))

    def get_joint_positions(self):
        return [0.0] * len(self.joint_names)


class FakeWing:
    def __init__(self):
        self.frequency = 1.8
        self.stroke_gate = 1.0
        self.amplitude = 0.3
        self.pitch_lag_s = -0.0674

    def set_frequency(self, f):
        self.frequency = float(f)


class FakeAeroModel:
    def __init__(self):
        self.wing_left = FakeWing()
        self.wing_right = FakeWing()
        self.cmd_frequency = None

    def set_wing_command(self, frequency=None, **k):
        if frequency is not None:
            self.cmd_frequency = float(frequency)


class FakeTail:
    def set_command(self, *a, **k):
        pass


class FakeAeroCb:
    def __init__(self):
        self.model = FakeAeroModel()
        self.tail_body = FakeTail()


def record(cid, desc, ok, detail=""):
    RESULTS.append({"id": cid, "desc": desc, "pass": bool(ok),
                    "detail": str(detail)[:260]})
    print("%s %s %s | %s" % ("过" if ok else "拒", cid, desc, detail))


def ap(**kw):
    base = {"freq": 1.8, "dfreq": 0.0, "dA": 0.0, "dlag": 0.0,
            "gate_L": 1.0, "gate_R": 1.0, "elevator": 0.0,
            "aileron": 0.0, "rudder": 0.0, "wavegen_freq": None}
    base.update(kw)
    return base


def drive(step, ap_out, wk, art=None, aero=None, man=None):
    globals().setdefault("_DP_STATE", {"freq": None})
    art = art or FakeArt()
    aero = aero or FakeAeroCb()
    WA._drive_wing_joints(None, art, wk, man, aero, ap_out, step)
    return art, aero


def fresh_wk():
    return WingKinematics(frequency=1.8, amplitude_deg=17.73,
                          pitch_mode="aero", alpha_set_deg=12.0)


def main():
    # A 默认回归：开关关，含与不含 wavegen 键逐位一致
    _SW.WAVEGEN_DRIVE_ON = False
    wk1, wk2 = fresh_wk(), fresh_wk()
    a1, ae1 = drive(1, ap(wavegen_freq=2.5), wk1)
    a2, ae2 = drive(1, ap(), wk2)
    okA = (wk1.frequency == wk2.frequency == 1.8
           and ae1.model.cmd_frequency == ae2.model.cmd_frequency == 1.8
           and a1.targets[0].tolist() == a2.targets[0].tolist())
    record("A", "默认关闭：含 wavegen 键与不含键目标与频率逐位一致", okA,
           "wk=%s 源=%s" % (wk1.frequency,
                             WA.select_effective_freq(ap(wavegen_freq=2.5))[2]))

    # B 开启有效：关节侧、气动集体、dfreq 差动统一实际采用
    _SW.WAVEGEN_DRIVE_ON = True
    wk = fresh_wk()
    art, aero = drive(2, ap(wavegen_freq=2.2, dfreq=0.05), wk)
    okB = (wk.frequency == 2.2
           and aero.model.cmd_frequency == 2.2
           and abs(aero.model.wing_left.frequency - 2.25) <= 1e-12
           and abs(aero.model.wing_right.frequency - 2.15) <= 1e-12)
    record("B", "开启有效：关节 2.2、气动集体 2.2、差动翼 2.25/2.15",
           okB, "wk=%s 集体=%s 左=%s 右=%s"
           % (wk.frequency, aero.model.cmd_frequency,
              aero.model.wing_left.frequency,
              aero.model.wing_right.frequency))

    # C/D 零与负：回退名义
    for v, tag in ((0.0, "C"), (-1.0, "D")):
        wk = fresh_wk()
        drive(3, ap(wavegen_freq=v), wk)
        record(tag, "无效值 %s：回退名义 1.8" % v, wk.frequency == 1.8,
               "wk=%s" % wk.frequency)

    # E 减半对照：实际动作改变（界内减半 2.0→1.0；界下减半被限幅另证）
    wk = fresh_wk()
    drive(4, ap(freq=2.0, wavegen_freq=1.0), wk)
    okE = wk.frequency == 1.0
    wk2 = fresh_wk()
    drive(4, ap(freq=1.8, wavegen_freq=0.9), wk2)
    record("E", "减半对照：界内 2.0→1.0 实际采用；界下 0.9 限幅至 1.0",
           okE and wk2.frequency == 1.0,
           "界内 wk=%s 界下 wk=%s" % (wk.frequency, wk2.frequency))

    # F 越界限幅
    wk = fresh_wk()
    drive(5, ap(wavegen_freq=99.0), wk)
    record("F", "越界 99.0：限幅至上界 3.5", wk.frequency == 3.5,
           "wk=%s" % wk.frequency)

    # G 手动优先：手动分支先于自动，wavegen 不干扰
    wk = fresh_wk()
    art, aero = drive(6, ap(wavegen_freq=2.5), wk,
                      man={"throttle": 0.5, "elevator": 0.0,
                           "aileron": 0.0, "rudder": 0.0})
    want = C.MANUAL_FREQ_MIN + (C.MANUAL_FREQ_MAX - C.MANUAL_FREQ_MIN) \
        * (0.5 + 1.0) / 2.0
    record("G", "手动优先：手动频率 %.3f 生效，wavegen 不干扰"
           % want, wk.frequency == want, "wk=%s" % wk.frequency)

    # J 停拍（力模型闸门零）：频率仍被消费，关节按既有口径继续拍动
    wk = fresh_wk()
    art, aero = drive(7, ap(wavegen_freq=2.2, gate_L=0.0), wk)
    okJ = (wk.frequency == 2.2
           and aero.model.sd_gate_cmd == (0.0, 1.0))
    record("J", "停拍左翼：频率仍消费，闸门命令达气动模型（既有口径："
                "闸门只作用力模型）",
           okJ, "wk=%s 闸命令=%s" % (wk.frequency, aero.model.sd_gate_cmd))

    # K 名义与实际分列
    globals().setdefault("_DP_STATE", {"freq": None})
    cache = None
    wk = fresh_wk()
    drive(8, ap(wavegen_freq=2.2), wk)
    cache = WA._G.get("_WING_FREQ_CACHE")
    record("K", "名义与实际分列缓存", cache == (1.8, 2.2, "wavegen"),
           "cache=%s" % (cache,))

    # I 相位次序：频率改变后相位积分连续不跳变（本步设置下一步生效）
    wk = fresh_wk()
    t1 = PHYS_DT
    phi1 = wk.evaluate(t1)["phi"]
    drive(9, ap(wavegen_freq=2.2), wk)      # 本步设置
    t2 = t1 + PHYS_DT
    phi2 = wk.evaluate(t2)["phi"]           # 下一步生效
    rate_bound = (2.0 * math.pi * 2.2 / 0.05) * PHYS_DT + 1e-9
    okI = (abs(phi2 - phi1) <= rate_bound
           and math.isfinite(phi2) and wk.frequency == 2.2)
    record("I", "相位次序：设置后下一步生效且相位连续（界 %.2f 弧度）"
           % rate_bound, okI,
           "|Δphi|=%.4f wk=%s" % (abs(phi2 - phi1), wk.frequency))

    # L 六百步调制序列：每步实际消费，记录最大步间频率差与目标变化
    wk = fresh_wk()
    max_df = 0.0
    consumed_all = True
    tgt_span = []
    prev_f = None
    for k in range(1, 601):
        t = k * PHYS_DT
        wg = 1.8 + 0.3 * math.sin(2.0 * math.pi * 0.2 * t)
        art, aero = drive(k, ap(wavegen_freq=wg), wk)
        if abs(wk.frequency - wg) > 1e-12:
            consumed_all = False
        if aero.model.cmd_frequency != wk.frequency:
            consumed_all = False
        if prev_f is not None:
            max_df = max(max_df, abs(wk.frequency - prev_f))
        prev_f = wk.frequency
        tgt_span.append(float(art.targets[0][0][0]))
    okL = consumed_all and max(tgt_span) - min(tgt_span) > 0.05
    record("L", "六百步调制：逐步实际消费，目标随频率变化，最大步间差 "
           "%.4f 赫兹" % max_df, okL,
           "consumed=%s 目标幅差=%.3f" % (consumed_all,
                                          max(tgt_span) - min(tgt_span)))

    # H 故障反例：故意断开消费者（set_frequency 空转），检查必须失败
    wk = fresh_wk()
    real_set = wk.set_frequency
    wk.set_frequency = lambda f: None      # 断开关节侧消费者
    drive(10, ap(wavegen_freq=2.2), wk)
    wk.set_frequency = real_set
    detected = wk.frequency != 2.2
    record("H", "断开消费者反例：检查检出未实际消费", detected,
           "wk=%s（未变即检出）" % wk.frequency)

    # M mock_backend 复用同一选择（结构性佐证；逐位行为由同函数默认分支保证）
    import inspect
    import modules.backend.mock_backend as MB
    src = inspect.getsource(MB)
    record("M", "mock_backend 复用统一选择函数（结构性佐证）",
           "from modules.wing_actuation import select_effective_freq" in src,
           "同函数默认分支保证逐位一致")

    _SW.WAVEGEN_DRIVE_ON = False
    stage2 = run_stage2()
    stage3 = run_stage3()
    n = sum(1 for r in RESULTS if r["pass"])
    summary = {"total": len(RESULTS), "pass": n,
               "fail": len(RESULTS) - n,
               "verdict": "pass" if n == len(RESULTS) else "fail"}
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    ev_dir = CHECK_OUTPUT_ROOT
    os.makedirs(ev_dir, exist_ok=True)
    path = os.path.join(ev_dir, "i210_hopf隔离验证_%s.json" % ts)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"item": "I-210", "generated_at": ts, "stage2": stage2,
                   "stage3": stage3,
                   "summary": summary, "checks": RESULTS},
                  f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False))
    print("产物:", path)
    return 0 if summary["verdict"] == "pass" else 1


# === 第二阶段（2026-10-15 阶段补证）：真实发生器六档与默认隔离全记录 ===
# 调真实 FlightController.update（内含 Hopf 生产发生器，输出 wavegen_freq）、
# 真实 select_effective_freq 与 _drive_wing_joints、真实 WingKinematics 与气
# 动消费者桩（关节接口与气动回调两个外部边界，与第一阶段同法）。人工机体
# 状态固定为巡航配平悬停点（z=3m、vx=3.95m/s），仅作隔离输入边界登记，
# 不代表实测飞行。六档 R0—R5 按 i210_measurement_rules_v1：B3=0、dfreq=0、
# 其余共同输入固定。
FIXED_POSE = [0.0, 0.0, 3.0, 1.0, 0.0, 0.0, 0.0]
FIXED_VEL = [3.95, 0.0, 0.0, 0.0, 0.0, 0.0]
WARMUP_STEPS = 360          # 3 秒暖机（斜坡与发生器幅值收敛）
RECORD_STEPS = 600          # 记录窗（5 秒）
FAULT_WINDOW = (200, 260)   # R3 内关节侧断开窗口（记录步，含两端）


def run_case(case_id, wg_on, drive_on, b1, record_fault=False,
             warmup=WARMUP_STEPS, rec=RECORD_STEPS):
    """跑一档真实链，返回暖机后记录窗的逐步原值记录。"""
    from modules.flight_controller import FlightController
    _SW.WAVEGEN_ON = wg_on
    _SW.WAVEGEN_DRIVE_ON = drive_on
    _SW.WAVEGEN_B1 = b1
    _SW.WAVEGEN_B3 = 0.0
    ctrl = FlightController()
    wk = fresh_wk()
    art, aero = FakeArt(), FakeAeroCb()
    steps = []
    fault_flags = []
    real_set = wk.set_frequency
    for k in range(1, warmup + rec + 1):
        t = k * PHYS_DT
        ap_out = ctrl.update(FIXED_POSE, FIXED_VEL, t)
        wk_before = wk.frequency
        in_fault = (record_fault and warmup + FAULT_WINDOW[0] <= k
                    <= warmup + FAULT_WINDOW[1])
        if in_fault:
            wk.set_frequency = lambda f: None      # 断开关节侧消费者
        art, aero = drive(k, ap_out, wk, art, aero)
        if in_fault:
            wk.set_frequency = real_set
        cache = WA._G.get("_WING_FREQ_CACHE") or (None, None, None)
        fault_flags.append(bool(
            in_fault and wk.frequency != cache[1]))
        if k > warmup:
            tgt = art.targets[0]
            steps.append({
                "step": k, "t_s": t,
                "nominal_freq": ap_out["freq"],
                "wavegen_raw": ap_out.get("wavegen_freq"),
                "effective_freq": cache[1], "source": cache[2],
                "wk_freq_consumed_by_eval": wk_before,
                "wk_freq_after_set": wk.frequency,
                "aero_collective": aero.model.cmd_frequency,
                "aero_wing_L": aero.model.wing_left.frequency,
                "aero_wing_R": aero.model.wing_right.frequency,
                "flap_target_L": float(tgt[0][0]),
                "flap_target_R": float(tgt[0][1]),
                "dfreq": ap_out.get("dfreq", 0.0),
            })
    _SW.WAVEGEN_ON = False
    _SW.WAVEGEN_DRIVE_ON = False
    return {"case": case_id, "config": {"WAVEGEN_ON": wg_on,
                                         "WAVEGEN_DRIVE_ON": drive_on,
                                         "B1": b1, "B3": 0.0},
            "input_identity": {"pose": FIXED_POSE, "vel": FIXED_VEL,
                               "dt": PHYS_DT, "warmup": warmup,
                               "record": rec,
                               "state_note": "人工隔离配平状态，非实测飞行"},
            "steps": steps, "fault_detected_in_window": fault_flags}


def _dump(ev_dir, name, obj):
    path = os.path.join(ev_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    return path


def run_stage2():
    import hashlib
    from modules.flight_controller import FlightController
    ev_dir = os.path.join(CHECK_OUTPUT_ROOT, "stage2")
    os.makedirs(ev_dir, exist_ok=True)
    cases = {}
    for cid, on, dr, b1 in (("R0", False, False, 0.0),
                            ("R1", True, False, 0.2),
                            ("R2", True, True, 0.0),
                            ("R3", True, True, 0.2),
                            ("R4", True, True, -0.2),
                            ("R5", True, True, 0.1)):
        cases[cid] = run_case(cid, on, dr, b1)
        _dump(ev_dir, "case_%s_records.json" % cid, cases[cid])

    def eff(cid):
        return [st["effective_freq"] for st in cases[cid]["steps"]]

    def nom(cid):
        return [st["nominal_freq"] for st in cases[cid]["steps"]]

    def raw(cid):
        return [st["wavegen_raw"] for st in cases[cid]["steps"]]

    def span(cid):
        vals = [st["flap_target_L"] for st in cases[cid]["steps"]]
        return max(vals) - min(vals)

    # N 六档链完整与共同输入
    okN = (all(len(c["steps"]) == RECORD_STEPS for c in cases.values())
           and all(all(math.isfinite(st["effective_freq"])
                       and st["effective_freq"] > 0.0
                       for st in c["steps"]) for c in cases.values())
           and nom("R2") == nom("R3") == nom("R4") == nom("R5")
           == nom("R1"))
    record("N", "真实发生器六档链完整（各 600 步原值，共同名义输入逐位"
                "一致，实际频率全部有限正值）", okN,
           "名义末值=%s 各档步数=%s" % (
               nom("R3")[-1], [len(c["steps"]) for c in cases.values()]))

    # P R1 对 R3：只改驱动开关
    okP = (raw("R1") == raw("R3")
           and all(st["source"] == "nominal" and st["wk_freq_after_set"]
                   == st["nominal_freq"] for st in cases["R1"]["steps"])
           and all(st["source"] == "wavegen" and st["wk_freq_after_set"]
                   == st["wavegen_raw"] for st in cases["R3"]["steps"]))
    record("P", "R1 对 R3 只改驱动开关：原始发生器序列逐位相同，R1 走名"
                "义、R3 实际采用发生器输出，目标幅差扩大", okP
           and span("R3") > span("R1"),
           "raw 同=%s R1 目标幅差=%.4f R3 目标幅差=%.4f"
           % (raw("R1") == raw("R3"), span("R1"), span("R3")))

    # Q B1 组：真实参数零正负减半
    e2, e3, e4, e5 = eff("R2"), eff("R3"), eff("R4"), eff("R5")
    okQ = (e2 != e3 and e3 != e4 and e4 != e5 and e2 != e5
           and all(f > 0.0 for f in e4)
           and max(abs(a - b) for a, b in zip(e2, e3)) > 1e-6
           and len(set(e5)) > 10)
    record("Q", "B1 组（0、+0.2、-0.2、+0.1）经真实发生器形成互异有效频"
                "率序列；负调制不是负频率，减半不是改名义", okQ,
           "R4 最小=%.4f（>0） R5 互异值=%d 名义全程一致=%s"
           % (min(e4), len(set(e5)), nom("R2") == nom("R5")))

    # S 相位时点、时基与下一步消费（600 步逐一核对）
    st_all_ok = True
    detail_s = []
    for cid in ("R3", "R4", "R5"):
        seq = cases[cid]["steps"]
        nxt = all(seq[i + 1]["wk_freq_consumed_by_eval"]
                  == seq[i]["wk_freq_after_set"]
                  for i in range(len(seq) - 1))
        cont = True
        for i in range(1, len(seq)):
            d = abs(seq[i]["flap_target_L"] - seq[i - 1]["flap_target_L"])
            if d > 2.0 * math.pi * C.FlightControllerConfig.FREQ_MAX_AP \
                    * PHYS_DT * 3.0 + 1e-6:
                cont = False
        if not (nxt and cont):
            st_all_ok = False
        detail_s.append("%s 下一步消费=%s 相位连续=%s" % (cid, nxt, cont))
    record("S", "时点次序：本步设置下一步 evaluate 消费（600 步逐一核"
                "对），时基单调步号驱动，目标相位连续有界", st_all_ok,
           "；".join(detail_s))

    # T 限幅与速率来源
    srcs = {st["source"] for c in ("R2", "R3", "R4", "R5")
            for st in cases[c]["steps"]}
    okT = (srcs == {"wavegen"}
           and all(C.FlightControllerConfig.FREQ_AP_MIN
                   <= st["effective_freq"]
                   <= C.FlightControllerConfig.FREQ_MAX_AP
                   for c in ("R2", "R3", "R4", "R5")
                   for st in cases[c]["steps"]))
    record("T", "限幅与速率来源：开启档全部走 wavegen 源（界 [%s, %s] "
                "内无限幅回退），名义斜坡在控制器内、发生器速率由振荡"
                "动力学与限幅界共同约束（无第二级斜坡）" % (
                    C.FlightControllerConfig.FREQ_AP_MIN,
                    C.FlightControllerConfig.FREQ_MAX_AP), okT,
           "出现源=%s" % sorted(srcs))

    # U 断开故障与恢复（独立 R3 复跑中做窗口断开）
    fault_case = run_case("R3_fault", True, True, 0.2, record_fault=True)
    _dump(ev_dir, "case_R3_fault_records.json", fault_case)
    a, b = FAULT_WINDOW
    # 标志列表按全程步序（含暖机），换算到记录窗下标再取窗口
    in_w = fault_case["fault_detected_in_window"][WARMUP_STEPS + a - 1:
                                                  WARMUP_STEPS + b]
    after = fault_case["steps"][b - a + 1: b - a + 6]
    okU = (len(in_w) == b - a + 1 and all(in_w)
           and all(st["wk_freq_after_set"] == st["wavegen_raw"]
                   for st in after))
    record("U", "断开消费者故障：窗口内逐步检出不消费（故障检查必须失"
                "败），恢复后逐步实际采用", okU,
           "窗口 %s 检出=%d/%d 恢复后采用=%s"
           % (FAULT_WINDOW, sum(in_w), len(in_w),
              all(st["wk_freq_after_set"] == st["wavegen_raw"]
                  for st in after)))

    # V 默认隔离完整双跑逐字节比较（R0 与 R2 各两次同序列）
    byte_results = {}
    for cid in ("R0", "R2"):
        if cid == "R0":
            on, dr, b1 = False, False, 0.0
        else:
            on, dr, b1 = True, True, 0.0
        r1 = run_case(cid, on, dr, b1)
        r2 = run_case(cid, on, dr, b1)
        p1 = _dump(ev_dir, "doublerun_%s_run1.json" % cid, r1)
        p2 = _dump(ev_dir, "doublerun_%s_run2.json" % cid, r2)
        bb1 = open(p1, "rb").read()
        bb2 = open(p2, "rb").read()
        byte_results[cid] = {
            "run1_bytes": len(bb1), "run2_bytes": len(bb2),
            "byte_identical": bb1 == bb2,
            "steps": len(r1["steps"]),
            "sha256_run1": hashlib.sha256(bb1).hexdigest(),
            "sha256_run2": hashlib.sha256(bb2).hexdigest()}
    okV = all(v["byte_identical"] and v["steps"] == RECORD_STEPS
              for v in byte_results.values())
    record("V", "默认关闭与开启零调制各双跑：同输入序列完整原始记录逐字"
                "节一致（非仅哈希，字节数与步数登记）", okV,
           "R0 %d 字节×2 同=%s；R2 %d 字节×2 同=%s"
           % (byte_results["R0"]["run1_bytes"],
              byte_results["R0"]["byte_identical"],
              byte_results["R2"]["run1_bytes"],
              byte_results["R2"]["byte_identical"]))

    # W 关态沿旧实现来源核（R0 全序列对不含 wavegen 键的旧路径逐位一致）
    _SW.WAVEGEN_ON = False
    _SW.WAVEGEN_DRIVE_ON = False
    ctrl = FlightController()
    wk = fresh_wk()
    art, aero = FakeArt(), FakeAeroCb()
    oldpath_steps = []
    for k in range(1, WARMUP_STEPS + RECORD_STEPS + 1):
        t = k * PHYS_DT
        ap_out = ctrl.update(FIXED_POSE, FIXED_VEL, t)
        ap_old = {kk: vv for kk, vv in ap_out.items()
                  if kk != "wavegen_freq"}
        art, aero = drive(k, ap_old, wk, art, aero)
        if k > WARMUP_STEPS:
            oldpath_steps.append({
                "nominal": ap_old["freq"], "wk_freq": wk.frequency,
                "aero_collective": aero.model.cmd_frequency,
                "flap_target_L": float(art.targets[0][0][0])})
    _dump(ev_dir, "oldpath_R0_reference.json", {"steps": oldpath_steps})
    r0 = cases["R0"]["steps"]
    okW = all(a_["nominal_freq"] == b_["nominal"]
              and a_["wk_freq_after_set"] == b_["wk_freq"]
              and a_["aero_collective"] == b_["aero_collective"]
              and a_["flap_target_L"] == b_["flap_target_L"]
              for a_, b_ in zip(r0, oldpath_steps))
    record("W", "关态沿旧实现来源核：含 wavegen 键（None）与不含键的全序"
                "列指令、关节目标、力模型输入逐位一致", okW,
           "600 步逐位比对（源码身份见 chain_summary）")

    # X 默认关闭与开启零调制分列（数值差异明确列出，不合并）
    e2v = eff("R2")
    n2v = [st["nominal_freq"] for st in cases["R2"]["steps"]]
    d = [abs(x - y) for x, y in zip(e2v, n2v)]
    okX = (all(st["source"] == "nominal" for st in cases["R0"]["steps"])
           and all(st["source"] == "wavegen" for st in cases["R2"]["steps"])
           and max(d) > 0.0 and d[-1] < 1e-3)
    record("X", "默认关闭与开启零调制分列：R0 全程名义源，R2 全程发生器"
                "源，瞬态差异显式（最大 %.6g Hz，末段 %.3g Hz）"
           % (max(d), d[-1]), okX, "两态分别核验，不以近似相等合并")

    def _sha(rel):
        return hashlib.sha256(
            open(os.path.join(ROOT, rel), "rb").read()).hexdigest()
    chain_summary = {
        "item": "I-210", "stage": "supplement_20261015",
        "measurement_rules": "logs/hq_intake/20261014_mainline_recovery/"
                             "i210_measurement_rules_v1.json",
        "run_id": "run-kt-anni-i210-stage-evidence-20261015-a1",
        "source_identity": {
            "modules/flight_controller.py": _sha("modules/flight_controller.py"),
            "modules/wing_actuation.py": _sha("modules/wing_actuation.py"),
            "modules/aero/kinematics.py": _sha("modules/aero/kinematics.py"),
            "modules/backend/mock_backend.py":
                _sha("modules/backend/mock_backend.py"),
            "config/sim_config.py": _sha("config/sim_config.py"),
            "tools/i210_hopf_check.py": _sha("tools/i210_hopf_check.py")},
        "limits_and_rates": {
            "freq_ap_min": C.FlightControllerConfig.FREQ_AP_MIN,
            "freq_max_ap": C.FlightControllerConfig.FREQ_MAX_AP,
            "freq_slew_hz_per_s": C.FlightControllerConfig.FREQ_SLEW,
            "note": "名义斜坡由控制器内部施加；wavegen 速率由 Hopf 振荡"
                    "动力学与限幅界共同约束，选择函数不加第二级斜坡"},
        "case_stats": {cid: {
            "effective_min": min(eff(cid)), "effective_max": max(eff(cid)),
            "effective_last": eff(cid)[-1],
            "flap_target_span": span(cid)} for cid in cases},
        "double_run": byte_results,
        "fault_window": list(FAULT_WINDOW),
        "windows": {"warmup": WARMUP_STEPS, "record": RECORD_STEPS,
                    "dt": PHYS_DT},
        "input_state_note": "人工隔离配平状态（巡航配平悬停点），仅作输入"
                            "边界登记，非实测飞行，不用于整机结论",
    }
    _dump(ev_dir, "chain_summary.json", chain_summary)
    return {"evidence_dir": os.path.relpath(ev_dir, ROOT),
            "case_stats": chain_summary["case_stats"],
            "double_run": byte_results}


# === 第三阶段（2026-10-15 余项）：真旧实现回归与速率处置材料 ===
# 20261015 比较门禁返修：产物目录改 logs/i210_comparator_rework_20261015
# （旧 baseline_rate 目录的旧元数据文件按 archive_note 原样归档）。
def run_stage3():
    import subprocess as _sp
    base = os.path.join(ROOT, "logs", "i210_baseline_rate_20261015")
    out_dir = os.path.join(CHECK_OUTPUT_ROOT, "stage3")
    os.makedirs(out_dir, exist_ok=True)
    runner = os.path.join(base, "run_chain.py")
    old_tree = os.path.join(base, "old_src")
    out = {}

    def _chain(tree, dst, extra=()):
        cmd = [sys.executable, runner, "--tree", tree, "--out", dst,
               *extra]
        r = _sp.run(cmd, capture_output=True, text=True, timeout=600,
                    cwd=tree, env={**os.environ, "PYTHONPATH": tree})
        if r.returncode != 0:
            raise RuntimeError("链运行失败：%s" % r.stderr[-300:])
        return json.loads(r.stdout)

    # Y 真旧实现默认关闭对照（20261015 返修后重生成，实采快照）
    ro = _chain(old_tree, os.path.join(out_dir, "old_default_v2.json"))
    rn = _chain(ROOT, os.path.join(out_dir, "new_default_v2.json"))
    rn2 = _chain(ROOT, os.path.join(out_dir, "new_default_v2_run2.json"))
    cmp_path = os.path.join(out_dir, "baseline_compare_v2.json")
    rc = _sp.run([sys.executable, os.path.join(base, "compare_baseline.py"),
                  os.path.join(out_dir, "old_default_v2.json"),
                  os.path.join(out_dir, "new_default_v2.json"), cmp_path],
                 capture_output=True, text=True, timeout=300)
    cmp_doc = json.load(open(cmp_path, encoding="utf-8"))
    closure_ok = (all(v.startswith(old_tree + os.sep)
                      for v in ro["loaded"].values())
                  and all(v.startswith(ROOT + os.sep)
                          for v in rn["loaded"].values()))
    record("Y", "真旧实现默认关闭对照：旧源码树（摘要绑定提取）与当前"
                "树同序列 960 行完整契约结构化比较", closure_ok
           and rc.returncode == 0
           and cmp_doc["verdict"] == "identical"
           and cmp_doc["rows_compared"] == 960,
           "判定=%s 差异=%s 旧树闭包=%s"
           % (cmp_doc["verdict"], cmp_doc.get("n_differences"), closure_ok))

    # 同版双跑原始整档逐字节门槛（与新旧结构化比较分列）
    b1_ = open(os.path.join(out_dir, "new_default_v2.json"), "rb").read()
    b2_ = open(os.path.join(out_dir, "new_default_v2_run2.json"),
               "rb").read()
    import hashlib as _hl
    json.dump({"run1_sha256": _hl.sha256(b1_).hexdigest(),
               "run2_sha256": _hl.sha256(b2_).hexdigest(),
               "byte_identical": b1_ == b2_, "bytes": len(b1_)},
              open(os.path.join(out_dir, "same_version_byte_gate.json"),
                   "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    record("V2", "同版双跑原始整档逐字节一致（结构化比较之外单列的字"
                 "节门槛）", b1_ == b2_,
           "%d 字节 ×2 逐字节相等=%s" % (len(b1_), b1_ == b2_))

    # Z 速率处置场景（实采快照 + 切换事件）
    rf = _chain(ROOT, os.path.join(out_dir, "scenario_flip_v2.json"),
                ("--mode", "flip-drive", "--flip-at", "480"))
    rs = _chain(ROOT, os.path.join(out_dir, "scenario_b1_v2.json"),
                ("--mode", "switch-b1", "--flip-at", "480"))
    flip_doc = json.load(open(os.path.join(out_dir, "scenario_flip_v2.json"),
                              encoding="utf-8"))
    sw_doc = json.load(open(os.path.join(out_dir, "scenario_b1_v2.json"),
                            encoding="utf-8"))
    flip = flip_doc["steps"]
    sw = sw_doc["steps"]
    jump_flip = abs(flip[479]["wk_freq_after_set"]
                    - flip[478]["wk_freq_after_set"]) / PHYS_DT
    jump_sw = abs(sw[479]["cmd_wavegen_freq"]
                  - sw[478]["cmd_wavegen_freq"]) / PHYS_DT
    has_material = all(os.path.isfile(os.path.join(base, f)) for f in
                       ("rate_disposition.md",
                        "physical_baseline_checklist.md"))
    record("Z", "速率处置材料：开关切换一步跳变 %.4fHz/s、参数切换一步"
                "跳变 %.4fHz/s 原值在案，处置文档与基准清单齐备（建议"
                "供 HQ 审理，不自行实现）" % (jump_flip, jump_sw),
           has_material and jump_flip > 2.5 and jump_sw > 2.5
           and rf["steps"] == rs["steps"] == 960,
           "切换无斜坡执行点；详见 rate_disposition.md")

    # AA 快照实采与切换事件（20261015 返修）
    old_doc = json.load(open(os.path.join(out_dir, "old_default_v2.json"),
                             encoding="utf-8"))
    new_doc = json.load(open(os.path.join(out_dir, "new_default_v2.json"),
                             encoding="utf-8"))
    snap_ok = (old_doc["config_snapshot"]["WAVEGEN_DRIVE_ON"] is False
               and new_doc["config_snapshot"]["WAVEGEN_DRIVE_ON"] is False
               and old_doc["config_snapshot"]["WAVEGEN_ON"] is False
               and new_doc["config_snapshot"]["WAVEGEN_ON"] is False
               and old_doc["switch_events"] == []
               and new_doc["switch_events"] == [])
    record("AA", "默认关闭旧新快照实采驱动 false（修复误写 true），无"
                 "切换事件如实为空", snap_ok,
           "旧 drive=%s 新 drive=%s"
           % (old_doc["config_snapshot"]["WAVEGEN_DRIVE_ON"],
              new_doc["config_snapshot"]["WAVEGEN_DRIVE_ON"]))
    ev = flip_doc["switch_events"]
    ev2 = sw_doc["switch_events"]
    record("AB", "运行中切换以事件登记（步、参数、旧值、新值），快照为"
                 "起点实采不涂成终态",
           ev == [{"step": 480, "param": "WAVEGEN_DRIVE_ON",
                   "from": True, "to": False}]
           and ev2 == [{"step": 480, "param": "WAVEGEN_B1",
                        "from": 0.0, "to": -0.2}]
           and flip_doc["config_snapshot"]["WAVEGEN_DRIVE_ON"] is True
           and sw_doc["config_snapshot"]["WAVEGEN_B1"] == 0.0,
           "flip=%s b1=%s" % (ev, ev2))

    # AC 门禁完整契约正负回归（Doro 两反例 + 同类 + 正常，无特判）
    import copy as _copy
    import tempfile as _tf

    def _gate(old_d, new_d, tag):
        d = _tf.mkdtemp(prefix="i210_gate_chk_", dir=out_dir)
        po = os.path.join(d, "o.json")
        pn = os.path.join(d, "n.json")
        ro_ = os.path.join(out_dir, "gate_%s_result.json" % tag)
        json.dump(old_d, open(po, "w", encoding="utf-8"),
                  ensure_ascii=False)
        json.dump(new_d, open(pn, "w", encoding="utf-8"),
                  ensure_ascii=False)
        if os.path.exists(ro_):
            os.remove(ro_)
        r = _sp.run([sys.executable,
                     os.path.join(base, "compare_baseline.py"),
                     po, pn, ro_], capture_output=True, text=True)
        doc = json.load(open(ro_, encoding="utf-8"))
        return r.returncode, doc

    rc_n, doc_n = _gate(old_doc, new_doc, "chk_normal")
    ok_n = rc_n == 0 and doc_n["verdict"] == "identical"
    extra = _copy.deepcopy(new_doc)
    extra["steps"].append(_copy.deepcopy(extra["steps"][-1]))
    rc_e, doc_e = _gate(old_doc, extra, "chk_extra_row")
    ok_e = rc_e != 0 and doc_e["verdict"] != "identical"
    midf = _copy.deepcopy(new_doc)
    midf["steps"][479]["cmd_probe_new"] = 1.23
    rc_f, doc_f = _gate(old_doc, midf, "chk_mid_field")
    ok_f = rc_f != 0 and doc_f["verdict"] in ("differ",
                                              "qualification_failed")
    drop = _copy.deepcopy(new_doc)
    del drop["steps"][500]
    rc_d, doc_d = _gate(old_doc, drop, "chk_row_missing")
    ok_d = rc_d != 0 and doc_d["verdict"] != "identical"

    def _hit(doc_, keyword):
        """两层门禁任一命中即算：比较差异定位或资格原因定位。"""
        return (any(keyword in d_["field"]
                    for d_ in doc_.get("differences", []))
                or any(keyword in (str(p_.get("where", ""))
                                   + str(p_.get("reason", "")))
                       for sd_ in ("old", "new")
                       for p_ in doc_.get("qualification", {})
                       .get(sd_, {}).get("problems", [])))

    blv = _copy.deepcopy(new_doc)
    blv["steps"][10]["targets"][0] = False
    rc_b, doc_b = _gate(old_doc, blv, "chk_bool_num")
    ok_b = rc_b != 0 and (_hit(doc_b, "布尔") or _hit(doc_b, "targets"))
    typ = _copy.deepcopy(new_doc)
    typ["steps"][50]["cmd_freq"] = str(typ["steps"][50]["cmd_freq"])
    rc_t, doc_t = _gate(old_doc, typ, "chk_type")
    ok_t = rc_t != 0 and (_hit(doc_t, "类型") or _hit(doc_t, "有限"))
    shrink = _copy.deepcopy(new_doc)
    shrink["steps"][100]["targets"] = shrink["steps"][100]["targets"][:8]
    rc_s, doc_s = _gate(old_doc, shrink, "chk_list_shrink")
    ok_s = rc_s != 0 and _hit(doc_s, "长度")
    record("AC", "比较门禁完整契约：正常通过且七类负例（末行多一、中段"
                 "少行、480 步新字段、布尔对数值、类型改变、列表缩短）"
                 "全部非通过并带定位与退出码——20261015 返修版二起负例"
                 "先经逐份资格门禁拦截（qualification_failed），仍携原"
                 "因定位；两层任一命中即非通过",
           ok_n and ok_e and ok_f and ok_d and ok_b and ok_t and ok_s,
           "正常 exit=%s；负例 exits=%s/%s/%s/%s/%s/%s"
           % (rc_n, rc_e, rc_f, rc_d, rc_b, rc_t, rc_s))
    # AD 逐份数据资格先于比较（20261015 返修版二，HQ 契约绑定）
    import copy as _cp2
    import tempfile as _tf2

    def _qual_pair(old_d, new_d, tag):
        td = os.path.join(out_dir, "tmp_inputs")
        os.makedirs(td, exist_ok=True)
        po = os.path.join(td, "qo_%s.json" % tag)
        pn = os.path.join(td, "qn_%s.json" % tag)
        ro_ = os.path.join(out_dir, "qual_%s_result.json" % tag)
        json.dump(old_d, open(po, "w", encoding="utf-8"),
                  ensure_ascii=False)
        json.dump(new_d, open(pn, "w", encoding="utf-8"),
                  ensure_ascii=False)
        if os.path.exists(ro_):
            os.remove(ro_)
        r = _sp.run([sys.executable,
                     os.path.join(base, "compare_baseline.py"),
                     po, pn, ro_], capture_output=True, text=True)
        doc = json.load(open(ro_, encoding="utf-8"))
        return r.returncode, doc

    rc_q0, doc_q0 = _qual_pair(old_doc, new_doc, "chk_qual_normal")
    ok_q0 = rc_q0 == 0 and doc_q0["verdict"] == "identical"         and doc_q0["qualification"]["old"]["qualified"]         and doc_q0["qualification"]["new"]["qualified"]

    def _both_mutate(fn, tag):
        a = _cp2.deepcopy(old_doc)
        b = _cp2.deepcopy(new_doc)
        fn(a)
        fn(b)
        rc_, doc_ = _qual_pair(a, b, tag)
        return rc_ != 0 and doc_["verdict"] == "qualification_failed"

    ok_d1 = _both_mutate(lambda d: d["steps"][479].pop("cmd_freq", None),
                         "chk_both_drop_freq")
    def _dup_prev(d):
        d["steps"][479] = _cp2.deepcopy(d["steps"][478])
    ok_d2 = _both_mutate(_dup_prev, "chk_both_dup_prev")
    ok_d3 = _both_mutate(lambda d: d.__setitem__("steps", d["steps"][:479]
                                                 + [{}]
                                                 + d["steps"][480:]),
                         "chk_both_empty_row")
    def _drop_channel(d):
        for row in d["steps"]:
            row.pop("cmd_freq", None)
    ok_d4 = _both_mutate(_drop_channel, "chk_both_drop_channel")
    mo_ = _cp2.deepcopy(old_doc)
    mo_["steps"][100].pop("cmd_elevator", None)
    rc_q5, doc_q5 = _qual_pair(mo_, new_doc, "chk_oneside_missing")
    ok_q5 = rc_q5 != 0 and doc_q5["verdict"] == "qualification_failed"         and doc_q5["qualification"]["old"]["qualified"] is False         and doc_q5["qualification"]["new"]["qualified"] is True
    mo_2 = _cp2.deepcopy(old_doc)
    mo_2["steps"][300]["step"] = 900
    rc_q6, doc_q6 = _qual_pair(mo_2, new_doc, "chk_oneside_step")
    ok_q6 = rc_q6 != 0 and doc_q6["verdict"] == "qualification_failed"
    dif_ = _cp2.deepcopy(new_doc)
    dif_["steps"][9]["cmd_freq"] = 1.9
    rc_q7, doc_q7 = _qual_pair(old_doc, dif_, "chk_valid_diff")
    ok_q7 = rc_q7 != 0 and doc_q7["verdict"] == "differ"         and doc_q7["qualification"]["old"]["qualified"]         and doc_q7["qualification"]["new"]["qualified"]
    record("AD", "逐份数据资格先于比较：Doro 四个双方同坏输入全部 "
                 "qualification_failed 非零退出（第480行同删名义频率、"
                 "同重复前一行、同空对象、整档同删名义频率通道），单侧"
                 "缺通道/步号错序阻断，双方合法相同 identical、合法不"
                 "同 differ、单侧不合格保留侧别资格状态",
           ok_q0 and ok_d1 and ok_d2 and ok_d3 and ok_d4 and ok_q5
           and ok_q6 and ok_q7,
           "正常=%s 四同坏=%s/%s/%s/%s 单侧缺=%s 步号=%s 合法不同=%s"
           % (ok_q0, ok_d1, ok_d2, ok_d3, ok_d4, ok_q5, ok_q6, ok_q7))

    return {"baseline_compare": cmp_doc["verdict"],
            "jump_flip_hz_per_s": jump_flip,
            "jump_switch_hz_per_s": jump_sw,
            "gate": {"normal": ok_n, "extra_row": ok_e,
                     "mid_field": ok_f, "row_missing": ok_d,
                     "bool_num": ok_b, "type_change": ok_t,
                     "list_shrink": ok_s},
            "qualification": {"normal": ok_q0, "both_drop_freq": ok_d1,
                              "both_dup_prev": ok_d2,
                              "both_empty_row": ok_d3,
                              "both_drop_channel": ok_d4,
                              "oneside_missing": ok_q5,
                              "oneside_step": ok_q6,
                              "valid_diff": ok_q7}}



if __name__ == "__main__":
    sys.exit(main())
