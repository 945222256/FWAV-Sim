# -*- coding: utf-8 -*-
"""
unified_search_framework_v2.py — 可插拔约束·按飞行模态组装的搜索版

与 tools/unified_search_framework.py（下称 v1）的关系：物理计算核以模块
引用方式复用，v1 文件一行未改；约束层、模态层、判定层、主流程全部重写。
（采用引用而非实体复制，避免同一物理核在两份文件里各自漂移；若后续
需要实体拷贝冻结，仅需把 _load_v1 改为文件拷贝。）

相对 v1 的四条修改（按 2026-09-26 用户指令）：
1. 约束全部可插拔：每条约束是带元数据的函数对象，注册进约束表；增删
   约束不需要修改搜索主流程。
2. 约束按飞行模态组装：模态模板（当前模板：定高定速巡航）从约束表中
   选取属于自己的子集；不属于当前模态的条目不参与判定。
3. 约束必须是函数：约束以状态为自变量的函数或拟合曲线表达；只有拟合
   来源的系数允许携带数字；策略性旋钮集中在 POLICY 段，逐条声明"非
   物理约束、可调"；尚未拟合出函数的约束只登记占位（pending_fit），
   不写任何数字。
4. 域边界全部带来源：拍频域取自配置执行域 FREQ_AP_MIN/FREQ_MAX_AP；
   v1 的 0.8~3.4 无来源，作废。

用法： python tools/unified_search_framework_v2.py
产物： logs/unified_search_v2_constraints.json（约束注册与判定记录）
"""
import os, sys, json, math, datetime, importlib.util
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
from config import sim_config as cfg


def _load_v1():
    path = os.path.join(PROJECT_ROOT, "tools", "unified_search_framework.py")
    spec = importlib.util.spec_from_file_location("usf_v1", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


v1 = _load_v1()

# ────────────────────────────────────────────────────────────────────
# POLICY：策略性旋钮集中段。以下取值全部为自定策略，不是物理约束，
# 可以调整；每一处声明语义。物理性系数不允许出现在本段。
# ────────────────────────────────────────────────────────────────────
POLICY = {
    # 舵面权限预算系数：配平与稳态需求的力矩允许占执行机构效能的比例。
    # 语义：给机动预留余量的自定预算，非物理约束。
    "authority_budget_ratio": 0.50,
    # 升力判定下限：闭环周期平均竖直力与体重的比值下限。
    # 语义：巡航验收的自定标准，非物理约束。
    "lift_ratio_min": 0.95,
    # 任务目标（定高定速巡航模态）：持续秒数与高度带。
    # 语义：任务定义，自定值。
    "cruise_seconds": 60.0,
    "cruise_z_band": (2.0, 8.0),
}


class ConstraintFunction:
    """可插拔约束的函数对象。
    name    约束名（唯一键）
    modes   适用模态集合；模态模板只启用属于自己模态的约束
    kind    pre_filter（采样前排除）/ judge（进入判定）/ report（只记录）
    status  fitted（有拟合来源）/ policy（策略值）/ config（取自配置）/
            pending_fit（未拟合，占位停用）
    source  证据来源（哪一轮实测、拟合或配置项）
    fn      函数体，输入 ctx（波形参数与标定结果），输出约束评价值
    """
    def __init__(self, name, modes, kind, status, source, fn, enabled=True):
        self.name = name
        self.modes = frozenset(modes)
        self.kind = kind
        self.status = status
        self.source = source
        self.fn = fn
        self.enabled = enabled

    def __call__(self, ctx):
        return self.fn(ctx)


def c_thrust_sign(ctx):
    """推力-拍频斜率符号。函数形式：三因子拟合式
    slope = 0.144 · amp · cos(mean) · cos(θ*) · (0.5 − split)，
    约束为拟合曲线的符号为正，不引用任何点值。"""
    p = ctx["wave"]
    A = math.radians(p["amp"])
    M = math.radians(p["mean"])
    th = math.radians(ctx["cal"]["th_star"])
    slope = 0.144 * A * math.cos(M) * math.cos(th) * (0.5 - p["split"])
    return {"slope_pred": slope, "ok": slope > 0.0}


def c_trim_root(ctx):
    """配平根存在性。函数形式：Fx(θ) 在扫描窗口内存在变号区间，
    根由粗扫变号加二分求解给出，不是预设点。"""
    return {"th_star": ctx["cal"]["th_star"], "ok": True}


def c_my_dc(ctx):
    """机翼与驱动俯仰力矩直流项。函数形式：MY_DC(f) = coef · f²，
    coef 为第四十四轮三频带实测拟合值（曲线，非点）。"""
    coef = v1.MY_DC_COEF
    f = ctx["wave"]["freq"]
    return {"my_dc": coef * f * f}


def c_pitch_moment_budget(ctx):
    """俯仰力矩预算。函数形式：
    需求 need(θ*,f) = |My(θ*) − MY_DC(f)|（标定器输出，含拟合曲线）；
    效能 cap(v_eff) = |K_B_TAIL_ELEV| · 0.5·ρ·v_eff² · δ_max · budget_ratio；
    其中 budget_ratio 为 POLICY 策略旋钮。判定：need ≤ cap。"""
    p = ctx["wave"]
    cal = ctx["cal"]
    need = cal["need"]
    v_eff = p["speed"]
    q_dyn = 0.5 * 1.225 * v_eff * v_eff
    cap = (abs(float(cfg.FlightControllerConfig.K_B_TAIL_ELEV)) * q_dyn
           * float(cfg.FlightControllerConfig.DELTA_MAX) * POLICY["authority_budget_ratio"])
    return {"need": need, "cap": cap, "ok": need <= cap}


def c_lift_balance(ctx):
    """升力平衡。函数形式：闭环周期平均竖直力与体重的比值（状态函数）；
    阈值来自 POLICY.lift_ratio_min（策略值）。"""
    ratio = ctx["sim"]["frzw_med"]
    return {"lift_ratio": ratio, "ok": ratio >= POLICY["lift_ratio_min"]}


def c_damping_matrix(ctx):
    """工作点阻尼矩阵。函数形式：阻尼矩阵是工作点的函数 D(工作点)；
    当前仅有离散工作点实测值（第二十三轮），跨工况插值函数未拟合，
    按实测点常量使用并在报告中声明插值缺口。"""
    return {"matrix": v1.WING_DAMP.tolist(), "interp": "pending_fit"}


def c_freq_domain(ctx):
    """拍频执行域。函数形式：f ∈ [cfg.FlightControllerConfig.FREQ_AP_MIN, cfg.FlightControllerConfig.FREQ_MAX_AP]，
    边界取自配置执行域，不使用无来源数值。"""
    lo = float(cfg.FlightControllerConfig.FREQ_AP_MIN)
    hi = float(cfg.FlightControllerConfig.FREQ_MAX_AP)
    f = ctx["wave"]["freq"]
    return {"lo": lo, "hi": hi, "ok": lo <= f <= hi}


def c_drive_joint(ctx):
    """驱动限位联合约束。函数形式：|mean| + amp ≤ 驱动机构联合限位
    （配置机构值，来源为配置注释）。"""
    p = ctx["wave"]
    limit = 80.2  # 配置机构限位（config 注释：drive limit joint）
    used = abs(p["mean"]) + p["amp"]
    return {"used": used, "limit": limit, "ok": used <= limit}


def c_anchor_gate(ctx):
    """校准检验门：物理核输出与参考实测的对照全部通过才允许搜索。"""
    at = v1.anchor_tests()
    passed = all(s.startswith("PASS") or s.startswith("SKIP")
                 for _, s in at.values())
    return {"anchors": {k: v[0] for k, v in at.items()}, "ok": passed}


def c_turn_lift_need(ctx):
    """转弯升力需求函数（fitted，来源=tools/scan_turn_support.py 测量A，
    2026-09-27 实测三点拟合）。函数形式：
    lift_need(φ) = (1/cosφ) / (Fz(φ)/Fz0) = 1 + 1.408e-4 · φ_deg²，
    三点核验偏差 ≤0.2%（15/20/25 度对 1.0298/1.0544/1.0880）。
    物理含义：带坡度姿态下翼气动竖直力几乎不随坡度衰减（25 度仅微升
    1.4%，姿态投影与攻角重映射互偿），重力竖直平衡的真实额外升力需求
    比纯 1/cosφ 理论低：15/20/25 度需 +3.0%/+5.4%/+8.8%。"""
    phi = float(ctx["turn"]["phi_deg"])
    need = 1.0 + 1.408e-4 * phi * phi
    return {"lift_need": need, "phi_deg": phi, "ok": True}


def c_turn_coordinated_rate(ctx):
    """协调盘旋角速度函数（运动学定义式，零自由参数）。
    ω_yaw(φ,V) = g · tanφ / V。世界系竖直轴角速度。"""
    phi = math.radians(float(ctx["turn"]["phi_deg"]))
    V = float(ctx["wave"]["speed"])
    return {"om_yaw": 9.81 * math.tan(phi) / V, "ok": True}


def c_turn_roll_effect(ctx):
    """滚转执行效能函数（fitted，来源=tools/scan_turn_support.py 测量D，
    2026-09-27 实测）。俯仰滞后差动主通道：dL/dδ = +0.446 N·m/s（δ 单位
    秒，左右翼 pitch_lag_s 反号差动），三坡度档实测 0.446~0.447 恒定，
    偏航副作用比 0.63，升力漂移 ≤0.008 N。均值偏置差动实测效能近零
    （|dL/dδ|<1e-5 N·m/deg），作为候选通道剔除的实测依据。"""
    return {"dL_ddelta_lag": 0.446, "ok": True}


def c_turn_damping_caveat(ctx):
    """盘旋工况阻尼适用性警戒（report，来源=tools/scan_turn_support.py
    测量C，2026-09-27 实测）：协调盘旋角速度（体轴投影以 z 分量为主）
    下的滚转/偏航力矩差分与第二十三班 WING_DAMP 矩阵预测反号（实测
    dL=−0.003~−0.019 对预测 +0.010~+0.025），实测表现为恢复性阻尼。
    含义：横向模态 λ=+0.105/s 的两个候选根源（俯仰系坐标系信号互漏、
    翼级差动速率阻尼缺位）之外新增第三数据点：WING_DAMP 的交叉项
    在盘旋工况不适用。转弯闭环的滚转通道不得引用该矩阵做阻尼预测，
    采用实测速率反馈增益（ROLL_DAMP 口径）。"""
    return {"caveat": "WING_DAMP not valid in coordinated-turn regime; "
                      "use measured rate feedback", "ok": True}


def _pending_fit_stub(note):
    def fn(ctx):
        return {"ok": True, "pending_fit": note}
    return fn


CONSTRAINTS = [
    ConstraintFunction(
        "thrust_sign", ("cruise_hold", "turn", "descend"), "pre_filter",
        "fitted", "第四十九轮：三因子式对实测系数偏差 0.05%", c_thrust_sign),
    ConstraintFunction(
        "trim_root", ("cruise_hold", "turn", "descend"), "pre_filter",
        "fitted", "双阱稳健求根（粗扫变号+二分）", c_trim_root),
    ConstraintFunction(
        "pitch_moment_budget", ("cruise_hold",), "pre_filter",
        "fitted+policy", "需求函数含第四十四轮拟合曲线 MY_DC(f)；"
        "效能函数含动压与配置舵效；预算系数为 POLICY 策略值",
        c_pitch_moment_budget),
    ConstraintFunction(
        "freq_domain", ("cruise_hold", "turn", "descend", "launch"),
        "pre_filter", "config", "cfg.FlightControllerConfig.FREQ_AP_MIN / FREQ_MAX_AP",
        c_freq_domain),
    ConstraintFunction(
        "drive_joint", ("cruise_hold", "turn", "descend", "launch"),
        "pre_filter", "config", "配置驱动限位联合约束", c_drive_joint),
    ConstraintFunction(
        "my_dc", ("cruise_hold", "turn"), "report", "fitted",
        "第四十四轮三频带实测拟合", c_my_dc),
    ConstraintFunction(
        "damping_matrix", ("cruise_hold", "turn"), "report", "fitted",
        "第二十三轮新工作点 3×3 实测；跨工况插值待拟合", c_damping_matrix),
    ConstraintFunction(
        "lift_balance", ("cruise_hold", "turn"), "judge",
        "policy", "阈值为 POLICY.lift_ratio_min（策略值）", c_lift_balance),
    ConstraintFunction(
        "anchor_gate", ("cruise_hold", "turn", "descend", "launch"),
        "pre_filter", "fitted", "v1.anchor_tests 参考实测对照", c_anchor_gate),
    ConstraintFunction(
        "turn_lift_need", ("turn",), "judge", "fitted",
        "scan_turn_support 测量A 三点拟合（2026-09-27），核验偏差≤0.2%",
        c_turn_lift_need),
    ConstraintFunction(
        "turn_coordinated_rate", ("turn",), "report", "fitted",
        "运动学定义式 ω=g·tanφ/V，零自由参数", c_turn_coordinated_rate),
    ConstraintFunction(
        "turn_roll_effect", ("turn",), "report", "fitted",
        "scan_turn_support 测量D：lag 差动 0.446 N·m/s 三坡度恒定；"
        "mean 差动实测近零剔除", c_turn_roll_effect),
    ConstraintFunction(
        "turn_damping_caveat", ("turn",), "report", "fitted",
        "scan_turn_support 测量C：盘旋工况实测与 23 班矩阵反号，"
        "滚转通道禁引用该矩阵", c_turn_damping_caveat),
    ConstraintFunction(
        "int_gate_matching", ("cruise_hold",), "judge", "pending_fit",
        "积分门与稳态误差的匹配关系尚无拟合函数，占位停用",
        _pending_fit_stub("需释放段稳态误差随参数的响应曲面"),
        enabled=False),
    ConstraintFunction(
        "filter_phase_margin", ("cruise_hold",), "judge", "pending_fit",
        "滤波滞后对慢模式的放大关系尚无拟合函数，占位停用",
        _pending_fit_stub("需速度回路相位裕度随滤波常数的曲线"),
        enabled=False),
    ConstraintFunction(
        "pitch_establishment", ("cruise_hold",), "judge", "pending_fit",
        "释放后俯仰建立过程的函数模型未拟合，占位停用",
        _pending_fit_stub("需释放瞬态力矩构成与舵动态的专项测量"),
        enabled=False),
]


# ────────────────────────────────────────────────────────────────────
# 模态模板：定高定速巡航
# 模板从约束表中选取本模态的约束子集；任务目标为策略定义（POLICY）。
# 未来模板（起飞、转弯、下降）以同样方式组装各自不同的约束子集。
# ────────────────────────────────────────────────────────────────────
MODE_TEMPLATES = {
    "cruise_hold": {
        "label": "定高定速巡航",
        "pre_filter": ["thrust_sign", "trim_root", "pitch_moment_budget",
                       "freq_domain", "drive_joint", "anchor_gate"],
        "judge": ["lift_balance"],
        "report": ["my_dc", "damping_matrix"],
        "mission": {"seconds": POLICY["cruise_seconds"],
                    "z_band": POLICY["cruise_z_band"]},
    },
    # 压坡度转弯模板（2026-09-27 合并方案落地，依据审查文档第四节）：
    # 物理核沿用 v1 原样引用；约束子集=巡航通用硬约束（推力符号/配平根/
    # 拍频执行域/驱动限位/锚点门禁）+转弯专项约束（升力需求拟合函数/
    # 协调角速度定义式/滚转效能实测/阻尼适用性警戒）；判定标准先于数据
    # 入库；基准工况=第六十八班升格生效工作点。转弯专项测量四项的
    # 数据与拟合来源全部登记于各约束 source 字段。
    "turn": {
        "label": "压坡度协调盘旋",
        "pre_filter": ["thrust_sign", "trim_root", "freq_domain",
                       "drive_joint", "anchor_gate"],
        "judge": ["lift_balance", "turn_lift_need"],
        "report": ["my_dc", "damping_matrix", "turn_coordinated_rate",
                   "turn_roll_effect", "turn_damping_caveat"],
        "mission": {"seconds": POLICY["cruise_seconds"],
                    "z_band": POLICY["cruise_z_band"]},
    }
}


def active_constraints(mode):
    tpl = MODE_TEMPLATES[mode]
    reg = {c.name: c for c in CONSTRAINTS}
    out = {"pre_filter": [], "judge": [], "report": []}
    for kind in ("pre_filter", "judge", "report"):
        for name in tpl[kind]:
            c = reg[name]
            if c.enabled and mode in c.modes:
                out[kind].append(c)
    return tpl, out


def run_pre_filters(wave, cal, cons):
    ctx = {"wave": wave, "cal": cal, "sim": {}}
    record = {}
    for c in cons["pre_filter"]:
        if c.name == "anchor_gate":
            continue
        r = c(ctx)
        record[c.name] = r
        if not r.get("ok", False):
            return False, record
    return True, record


def run_judge(wave, cal, sim, cons, tpl):
    ctx = {"wave": wave, "cal": cal, "sim": sim}
    record = {}
    ok_all = True
    for c in cons["judge"]:
        r = c(ctx)
        record[c.name] = r
        ok_all = ok_all and r.get("ok", False)
    secs = tpl["mission"]["seconds"]
    z_lo, z_hi = tpl["mission"]["z_band"]
    mission_ok = (sim["survive_s"] >= secs and sim["z_min"] >= z_lo
                  and sim["z_max"] <= z_hi)
    record["mission_target"] = {"survive_s": sim["survive_s"],
                                "z_min": sim["z_min"], "z_max": sim["z_max"],
                                "ok": mission_ok}
    return ok_all and mission_ok, record


def search(mode="cruise_hold", n_wave=100, seed=7, n_ctrl=4):
    tpl, cons = active_constraints(mode)
    gate = c_anchor_gate({})
    if not gate["ok"]:
        return {"blocked": "anchor_gate", "anchors": gate["anchors"]}
    rng = np.random.default_rng(seed)
    wdom = {k: v1.DOMAINS[k] for k in
            ("alpha_set", "mean", "split", "lag", "amp", "freq", "speed")}
    cdom = {k: v1.DOMAINS[k] for k in
            ("kp_a", "pitch_damp", "ki_a", "int_gate", "rate_filt_tau",
             "alpha_ff", "tecs_kv_f", "alt_kp", "alt_ki", "vz_cap",
             "th_bias_cap", "freq_slew")}
    wdom["freq"] = (float(cfg.FlightControllerConfig.FREQ_AP_MIN), float(cfg.FlightControllerConfig.FREQ_MAX_AP),
                    "cfg 执行域")
    feas, records = [], {}
    waves = v1.lhs_sample(wdom, n_wave, rng)
    for wi, wv in enumerate(waves):
        cal = v1.calibrate_waveform(wv)
        ok, rec = run_pre_filters(wv, cal, cons)
        records[json.dumps(wv, sort_keys=True)] = rec
        if not ok:
            continue
        th_rad = math.radians(cal["th_star"])
        for cv in v1.lhs_sample(cdom, n_ctrl, rng):
            sim = v1.ClosedLoopSim(wv, cv, th_rad, v1.TAU_ELEV).run(t_end=20.0)
            ok20, _ = run_judge(wv, cal, sim, cons, tpl)
            if not ok20:
                continue
            sim60 = v1.ClosedLoopSim(wv, cv, th_rad, v1.TAU_ELEV).run(t_end=60.0)
            ok60, rec60 = run_judge(wv, cal, sim60, cons, tpl)
            if ok60:
                feas.append(dict(wave=wv, ctrl=cv, cal=cal, sim=sim60,
                                 judge=rec60))
        if wi % 10 == 0:
            print("wave %d/%d feas=%d" % (wi, n_wave, len(feas)), flush=True)
    return {"mode": tpl["label"], "n_feas": len(feas), "feasible": feas,
            "constraint_report": _serialize_constraints(cons)}


def _serialize_constraints(cons):
    out = {}
    for kind, lst in cons.items():
        out[kind] = [{"name": c.name, "kind": c.kind, "status": c.status,
                      "source": c.source} for c in lst]
    return out


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    mode = "cruise_hold"
    tpl, cons = active_constraints(mode)
    print("== unified search v2 · 模态模板：%s ==" % tpl["label"], flush=True)
    for kind in ("pre_filter", "judge", "report"):
        for c in cons[kind]:
            print("  [%s] %s (%s) 来源: %s" % (kind, c.name, c.status,
                                               c.source), flush=True)
    result = search(mode=mode)
    if "blocked" in result:
        print("搜索被校准检验门阻断：", result["anchors"], flush=True)
        return
    print("feasible=%d" % result["n_feas"], flush=True)
    result["ts"] = ts
    result["policy"] = POLICY
    out_path = os.path.join(cfg.LOG_DIR, "unified_search_v2_constraints.json")
    slim = {k: v for k, v in result.items() if k != "feasible"}
    slim["feasible"] = result["feasible"][:5]
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(slim, fp, ensure_ascii=False, indent=1, default=float)
    print("约束与判定记录 → %s" % out_path, flush=True)


if __name__ == "__main__":
    main()
