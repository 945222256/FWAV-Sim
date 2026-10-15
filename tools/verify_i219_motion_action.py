# -*- coding: utf-8 -*-
"""
verify_i219_motion_action.py — I-219 本地可验判据执行体（返修 v2）。

判据一：完整动作配方、参数对象、节律快照、当步动作和消费结果类型实
        现；九关节逐项元数据（机械限位、控制限幅、生成约束三层分列）；
        缺失、未支持与明确零值三态分列。
判据二：首个合作配方九行绑定表逐项要素齐全（绑定唯一键引用），中置
        上反保留，关节与气动参数来源分列，偏移唯一持有。
判据三：参数装配显式接收各频率来源与基准快照，优先级可执行，不读主
        程序全局字典，差频不加第二层集体限制；尾舵绝对命令与配平一
        次采用；逐通道策略经真实装配执行。
判据四：中心加尺度乘形状加控制修正分工，参考归一化与相位零点有版
        本；幅值差动与旧角偏置分列；初始相差从实际配置来源解析。
判据五：必需导数表四策略齐全；适配器必需导数契约表固定；同一输出
        规则导数不用假零补齐；速度/加速度分列有效身份。
判据六：旧配方默认选择、原指令键及抽象执行器实例化保持；新类型、可
        选消费协议和配方装载的生产入口检查通过，未知配方及非法绑定
        有可定位错误；非法变体逐个完整序列化后送真实装载入口。

用法：
  python tools/verify_i219_motion_action.py           全部检查
  python tools/verify_i219_motion_action.py types     仅判据一/四类型部分
  python tools/verify_i219_motion_action.py entry     仅判据六生产入口部分
仅使用 Python 标准库与本仓纯 Python 模块，不启动 Isaac。
"""
import copy
import json
import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

RESULTS = []

RECIPE_DIR = os.path.join(ROOT, "config", "motion_recipes")
RECIPE_PATH = os.path.join(RECIPE_DIR, "cooperative_v1.json")


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                         ("——" + detail) if detail else ""))
    return bool(ok)


def cmd_types():
    import modules.motion_action as ma
    from modules.motion_action import types as T

    # 判据一：类型实现（配方/快照/当步动作/消费结果在 types，参数对象
    # 在 parameters，均经包入口导出）
    check("判据一/类型实现",
          all(hasattr(ma, n) for n in (
              "MotionRecipe", "ChannelParameters", "RhythmSnapshot",
              "StepAction", "ConsumptionResult", "NINE_JOINT_CHANNELS")))

    # 判据一：九关节逐项元数据齐全且与契约名单同序一致
    from modules.contracts import ACTUATOR_JOINT_NAMES
    names = tuple(j.name for j in T.NINE_JOINT_CHANNELS)
    check("判据一/九关节名单与契约一致", names == ACTUATOR_JOINT_NAMES,
          "%s" % (names,))
    meta_ok = all(
        j.unit and j.coordinate and j.role and j.side in ("L", "R", "")
        and isinstance(j.mechanical_limit_rad, tuple)
        and len(j.mechanical_limit_rad) == 2
        and j.mechanical_limit_source
        for j in T.NINE_JOINT_CHANNELS)
    check("判据一/九关节元数据逐项齐全（名称/单位/坐标/机械限位/来源/角色）",
          meta_ok)

    # F07：机械限位与机构资产逐项活核对（URDF 原值）
    import xml.etree.ElementTree as ET
    urdf = ET.parse(os.path.join(
        ROOT, "assets", "flapper", "fwav_15dof_from_group.urdf"))
    asset = {j.attrib["name"]: dict(j.find("limit").attrib)
             for j in urdf.getroot().findall("joint")
             if j.find("limit") is not None}
    limit_ok = True
    detail = []
    for j in T.NINE_JOINT_CHANNELS:
        lim = asset.get(j.name)
        want = (float(lim["lower"]), float(lim["upper"])) if lim else None
        if want != j.mechanical_limit_rad:
            limit_ok = False
            detail.append("%s 元数据 %s vs 资产 %s"
                          % (j.name, j.mechanical_limit_rad, want))
    check("F07/机械限位与 URDF 资产逐项一致", limit_ok,
          "；".join(detail) if detail else "九关节全部与资产原值一致")

    # F07：三层来源分列且不新增限幅（尾舵与翼面各查一层归位）
    layers_ok = (
        T.NINE_JOINT_CHANNELS[6].control_demand_limit_source is not None
        and T.NINE_JOINT_CHANNELS[6].generation_constraint_source is None
        and T.NINE_JOINT_CHANNELS[0].generation_constraint_source is not None
        and T.NINE_JOINT_CHANNELS[0].control_demand_limit_source is None
        and all("URDF" in j.mechanical_limit_source
                or "urdf" in j.mechanical_limit_source
                for j in T.NINE_JOINT_CHANNELS))
    check("F07/机械限位、控制限幅、生成约束三层分列", layers_ok)

    # 判据一：三态分列
    check("判据一/三态分列",
          len({T.VALUE_PRESENCE_MISSING, T.VALUE_PRESENCE_UNSUPPORTED,
               T.VALUE_PRESENCE_ZERO}) == 3)

    # 判据五：必需导数表四策略
    need = {"continuous_analytic", "piecewise", "legacy_approximation",
            "event_boundary_unavailable"}
    check("判据五/导数策略四覆盖",
          need.issubset(set(T.DERIVATIVE_POLICIES)),
          "%s" % (T.DERIVATIVE_POLICIES,))

    # F08（v4）：需求表对照真实力核——附加质量与旋转力两个独立开关分
    # 行登记；旋转力开启时左右扭转通道必需扭转角速度
    reqs = T.ADAPTER_DERIVATIVE_REQUIREMENTS
    aero_rows = [r for r in reqs if r.adapter == "aero_force"]
    check("F08/需求表按附加质量与旋转力开关分行登记",
          sum(1 for r in aero_rows if "ENABLE_ADDED_MASS=True" in r.condition) >= 1
          and sum(1 for r in aero_rows if "ENABLE_ADDED_MASS=False" in r.condition) >= 1
          and sum(1 for r in aero_rows if "ENABLE_ROTATIONAL=True" in r.condition) >= 1,
          "条件=%s" % [r.condition[:26] for r in aero_rows])
    row_on = next((r for r in aero_rows
                   if "ENABLE_ADDED_MASS=True" in r.condition), None)
    row_off = next((r for r in aero_rows
                    if "ENABLE_ADDED_MASS=False" in r.condition), None)
    row_tw = next((r for r in aero_rows
                   if "ENABLE_ROTATIONAL=True" in r.condition), None)
    check("F08/附加质量开时拍动角速度与角加速度都必需且限拍动通道",
          row_on is not None
          and row_on.required_derivatives == (
              T.DERIV_Q_FLAP_ANGULAR_VELOCITY,
              T.DERIV_Q_FLAP_ANGULAR_ACCELERATION)
          and row_on.applies_to == ("flap_L", "flap_R")
          and row_on.on_unavailable == "invalidate_consumption",
          "必需=%s 通道=%s" % (row_on.required_derivatives if row_on else None,
                               row_on.applies_to if row_on else None))
    check("F08/附加质量关时只必需拍动角速度（不强制加速度）",
          row_off is not None
          and row_off.required_derivatives == (
              T.DERIV_Q_FLAP_ANGULAR_VELOCITY,))
    check("F08/旋转力开时左右扭转通道必需扭转角速度（单位 rad/s）",
          row_tw is not None
          and row_tw.required_derivatives == (
              T.DERIV_Q_TWIST_ANGULAR_VELOCITY,)
          and row_tw.applies_to == ("twist_L", "twist_R")
          and row_tw.on_unavailable == "invalidate_consumption"
          and "rad/s" in row_tw.frame,
          "必需=%s 通道=%s" % (row_tw.required_derivatives if row_tw else None,
                               row_tw.applies_to if row_tw else None))
    check("F08/角运动学与机身世界系状态分列说明",
          all("机身世界系" in r.frame for r in aero_rows)
          and "rad/s²" in row_on.frame)
    check("F08/两开关独立控制（切换附加质量不消除旋转力开启的扭转需求）",
          row_tw is not None
          and "ENABLE_ADDED_MASS" not in row_tw.condition
          and "ENABLE_ROTATIONAL" not in row_on.condition
          and "ENABLE_ROTATIONAL" not in row_off.condition
          and "无关" in row_tw.condition and "无关" in row_on.condition,
          "扭转行条件=%s；拍动行条件不含旋转力开关"
          % (row_tw.condition[:40] if row_tw else None))
    check("F08/不强制扭转角加速度与尾舵导数",
          not any("twist_angular_acceleration" in r.required_derivatives
                  for r in reqs)
          and not any("tail" in j for r in aero_rows
                      for j in r.applies_to))
    check("F08/表行完整性（必需量都在登记量名内，适用通道都在九关节内）",
          all(q in T.DERIV_Q_ALL for r in reqs
              for q in r.required_derivatives)
          and all(j == "all" or j in names for r in reqs
                  for j in r.applies_to))
    import inspect as _insp
    check("F08/适用边界声明（其他模型须另行登记）",
          "另行登记" in _insp.getsource(T),
          "表注释声明仅覆盖 sane_dickinson_3d，其他气动模型不默认沿用")

    # F08：调用真实力核验证拍动角加速度与扭转角速度的消费及缺失诊断
    import numpy as np
    from modules.aero.sane_dickinson_3d import SaneDickinson3DModel
    import config.sim_config as cfg_ae
    model = SaneDickinson3DModel()
    wing = {"phi": 0.1, "alpha": 0.2, "phi_dot": 2.0, "alpha_dot": 0.3,
            "phi_ddot": 0.0}
    f0, _t0 = model._wing_forces_canonical(
        model._r_centers.copy(), wing, np.zeros(3), np.zeros(3))
    f1, _t1 = model._wing_forces_canonical(
        model._r_centers.copy(), {**wing, "phi_ddot": 10.0},
        np.zeros(3), np.zeros(3))
    accel_consumed = float(np.linalg.norm(f1 - f0)) > 1e-9
    try:
        model._wing_forces_canonical(
            model._r_centers.copy(), {**wing, "phi_ddot": None},
            np.zeros(3), np.zeros(3))
        accel_missing_fatal = False
    except TypeError:
        accel_missing_fatal = True
    check("F08/真实力核默认附加质量开且实际消费拍动角加速度",
          model.enable_added_mass is True
          and cfg_ae.AeroModelConfig.ENABLE_ADDED_MASS is True
          and accel_consumed,
          "力差模=%s" % float(np.linalg.norm(f1 - f0)))
    check("F08/真实力核拍动角加速度缺失即异常（契约登记消费无效正确）",
          accel_missing_fatal,
          "缺失诊断与 invalidate_consumption 一致，不填假零")
    # 扭转角速度（v4）：旋转力开时实际消费（力与力矩都变）、缺失即异常
    f2, t2 = model._wing_forces_canonical(
        model._r_centers.copy(), {**wing, "alpha_dot": 0.0},
        np.zeros(3), np.zeros(3))
    f3, t3 = model._wing_forces_canonical(
        model._r_centers.copy(), {**wing, "alpha_dot": 2.0},
        np.zeros(3), np.zeros(3))
    twist_consumed = (float(np.linalg.norm(f3 - f2)) > 1e-9
                      and float(np.linalg.norm(t3 - t2)) > 1e-9)
    try:
        model._wing_forces_canonical(
            model._r_centers.copy(), {**wing, "alpha_dot": None},
            np.zeros(3), np.zeros(3))
        twist_missing_fatal = False
    except TypeError:
        twist_missing_fatal = True
    check("F08/真实力核默认旋转力开且实际消费扭转角速度（力与力矩都变）",
          model.enable_rotational is True
          and cfg_ae.AeroModelConfig.ENABLE_ROTATIONAL is True
          and twist_consumed,
          "力差=%s 力矩差模=%.6f" % ((f3 - f2).tolist(),
                                      float(np.linalg.norm(t3 - t2))))
    check("F08/旋转力开时扭转角速度缺失即异常（契约判无效正确）",
          twist_missing_fatal, "不填假零")
    model_nr = SaneDickinson3DModel({"enable_rotational": False})
    try:
        fr, _tr = model_nr._wing_forces_canonical(
            model_nr._r_centers.copy(), {**wing, "alpha_dot": None},
            np.zeros(3), np.zeros(3))
        fz, _tz = model_nr._wing_forces_canonical(
            model_nr._r_centers.copy(), {**wing, "alpha_dot": 0.0},
            np.zeros(3), np.zeros(3))
        rot_off_ok = (float(np.linalg.norm(fr - fz)) < 1e-12)
    except TypeError:
        rot_off_ok = False
    check("F08/旋转力关闭时扭转角速度可缺失（输出与零角速度一致）",
          rot_off_ok, "关闭开关后不强制，与契约登记一致")
    # 附加质量开关切换不消除旋转力开启时的扭转需求（表结构与真实核双查）
    model_am_off = SaneDickinson3DModel({"enable_added_mass": False})
    check("F08/附加质量关不影响旋转力开关与扭转消费成立",
          model_am_off.enable_rotational is True
          and "ENABLE_ADDED_MASS" not in row_tw.condition,
          "真实核附加质量关时旋转力仍开；扭转需求行条件独立")

    # F08：通道动作分列速度/加速度与各自有效身份
    act = T.ChannelAction(
        "flap_L", "value", 0.2, "flap_shape_v1@chi_L", True, "解析导数",
        velocity=0.5, acceleration=None,
        derivative_statuses={"velocity": "available",
                             "acceleration": "event_boundary_unavailable"})
    check("F08/通道动作速度加速度分列身份",
          act.velocity == 0.5 and act.acceleration is None
          and act.derivative_statuses["velocity"] == "available"
          and act.derivative_statuses["acceleration"]
          == "event_boundary_unavailable")

    # 判据四：分工字段与版本
    from modules.motion_action.parameters import ChannelParameters
    import dataclasses
    flds = {f.name for f in dataclasses.fields(ChannelParameters)}
    check("判据四/中心-尺度-形状-修正分工字段",
          {"center", "scale", "shape_weight", "control_correction",
           "center_source", "scale_source"}.issubset(flds))
    check("判据四/幅值差动与旧角偏置分列字段",
          {"amplitude_differential", "legacy_angular_offset"}.issubset(flds))
    rec = T.load_recipe(RECIPE_PATH)
    check("判据四/参考归一化与相位零点版本",
          bool(rec.version) and "refnorm" in rec.version and
          "phasezero" in rec.version, rec.version)

    # 判据二：首个合作配方九行绑定表逐项要素
    by_joint = {c.joint: c for c in rec.channels}
    check("判据二/九行绑定齐全", len(by_joint) == 9)
    fields = ("provider", "provider_binding", "reference_function",
              "reference_coordinate", "center_source", "scale_source",
              "initial_state", "mirrored", "differential_keys",
              "gating_key", "soft_start", "joint_mapping", "aero_mapping",
              "derivative_policy", "phase_offset_rad")

    def _blank(c, f):
        if f in ("mirrored", "differential_keys", "gating_key",
                  "phase_offset_rad"):
            return False
        return not getattr(c, f)

    blank = [(c.joint, f) for c in rec.channels for f in fields
             if _blank(c, f)]
    check("判据二/绑定行字段无空缺（False/空元组/None/0.0 为显式合法值）",
          not blank, "%s" % (blank,))
    check("判据二/中置上反保留",
          rec.preserves_mean_dihedral and
          by_joint["flap_L"].center_source == "kinematics.MEAN")
    check("判据二/关节与气动来源分列",
          by_joint["twist_L"].aero_mapping == "wing_left.pitch_lag_s"
          and "no force model fold consumer" in by_joint["fold_L"].aero_mapping)
    # 节律补充一节：节点、父子、耦合、提供者绑定进入配方类型
    check("补充一/节律节点与关系进入配方",
          len(rec.rhythm_nodes) >= 4 and len(rec.parent_child) >= 3
          and len(rec.couplings) >= 1 and len(rec.provider_bindings) >= 4,
          "nodes=%d pc=%d cp=%d pb=%d" % (
              len(rec.rhythm_nodes), len(rec.parent_child),
              len(rec.couplings), len(rec.provider_bindings)))

    # F04：绑定唯一键与通道引用一一对应，左右节点选择明确
    bid = {b.binding_id: b for b in rec.provider_bindings}
    f04_ok = (by_joint["flap_L"].provider_binding == "flap_shape_v1#chi_L"
              and by_joint["flap_R"].provider_binding == "flap_shape_v1#chi_R"
              and bid[by_joint["flap_L"].provider_binding].reads_node == "chi_L"
              and bid[by_joint["flap_R"].provider_binding].reads_node == "chi_R"
              and len(bid) == len(rec.provider_bindings))
    check("F04/左右拍动通道各自引用唯一绑定（左读 chi_L 右读 chi_R）",
          f04_ok)
    # F04：左右扭转差动键同一表声明（装配器按表处理）
    check("F04/左右扭转差动键同一表声明 dlag",
          by_joint["twist_L"].differential_keys == ("dlag",)
          and by_joint["twist_R"].differential_keys == ("dlag",))

    # F03：折翼偏移唯一持有（通道 2.2 rad，派生偏移为零）
    fold_rel = next(r for r in rec.parent_child if r.child == "chi_fold")
    f03_ok = (abs(by_joint["fold_L"].phase_offset_rad - 2.2) < 1e-12
              and abs(by_joint["fold_R"].phase_offset_rad - 2.2) < 1e-12
              and fold_rel.offset_value == 0.0
              and abs(2 * math.pi * fold_rel.offset_value
                      + by_joint["fold_L"].phase_offset_rad - 2.2) < 1e-12
              and by_joint["fold_L"].phase_offset_source)
    check("F03/折翼 2.2 rad 偏移唯一持有（派生偏移为零不叠加）", f03_ok,
          "派生偏移=%s 通道偏移=%s"
          % (fold_rel.offset_value, by_joint["fold_L"].phase_offset_rad))

    # F04：右翼初始相差从实际配置来源解析（PHASE_OFFSET_LR 现行 0 度）
    import config.sim_config as cfg
    delta_expect = float(cfg.FlapKinematicsConfig.PHASE_OFFSET_LR) / 360.0
    chi_r = next(r for r in rec.parent_child if r.child == "chi_R")
    check("F04/右翼初始相差从 PHASE_OFFSET_LR 实际解析",
          chi_r.offset_value == delta_expect
          and str(chi_r.offset_source).startswith("config:")
          and "m113" not in rec.version,
          "解析=%s（配置 %s 度）来源=%s"
          % (chi_r.offset_value, cfg.FlapKinematicsConfig.PHASE_OFFSET_LR,
             chi_r.offset_source[:52]))


def cmd_assembly():
    from modules.motion_action import (assemble_parameters, FreqSource,
                                       ParameterAssembly,
                                       FEEDBACK_ATTRIBUTION_TABLE,
                                       output_with_derivative)
    from modules.motion_action import types as T
    from modules.motion_action import parameters as P

    recipe = T.load_recipe(RECIPE_PATH)
    table = {c.joint: c for c in recipe.channels}
    bl = {"mean_rad": 0.346, "amplitude_rad": 0.3096, "freq_hz": 1.8,
          "tail_trim_rad": 0.08, "pitch_lag_s": -0.0674,
          "da_joint_amp_on": False}
    trim = bl["tail_trim_rad"]

    # 判据三：优先级 manual > auto > backup > baseline
    srcs = {"manual": FreqSource("manual", 2.0),
            "auto": FreqSource("auto", 1.8),
            "backup": FreqSource("backup", 1.6, "v7")}
    params, adoption = assemble_parameters(dict(srcs), dict(bl), {})
    check("判据三/手动优先", params is not None and
          adoption["freq"].startswith("manual"), adoption["freq"])
    del srcs["manual"]
    _, adoption = assemble_parameters(dict(srcs), dict(bl), {})
    check("判据三/自动次之且不重复叠加反馈",
          adoption["freq"].startswith("auto") and "不重复相加" in
          adoption["freq"], adoption["freq"])
    del srcs["auto"]
    _, adoption = assemble_parameters(dict(srcs), dict(bl), {})
    check("判据三/备用带版本", adoption["freq"].startswith("backup")
          and "v7" in adoption["freq"], adoption["freq"])
    del srcs["backup"]
    _, adoption = assemble_parameters(dict(srcs), dict(bl), {})
    check("判据三/全空保持基准", adoption["freq"].startswith("baseline"),
          adoption["freq"])
    try:
        assemble_parameters({}, {}, {})
        ok = False
    except ValueError:
        ok = True
    check("判据三/无任何来源显式失败", ok)

    # 判据三：差频不构成集体拍频第二层限制
    params2, adoption2 = assemble_parameters(
        {"auto": FreqSource("auto", 1.8)}, dict(bl),
        {"dfreq": 0.3, "dA": 0.05, "dlag": 0.01}, recipe_channels=table)
    check("判据三/差频不加集体第二层限制",
          "auto=1.8000Hz" in adoption2["freq"], adoption2["freq"])

    # 判据三：通道缺省/归零/保持/模式退出策略可执行（独立函数）
    r = ParameterAssembly.resolve_with_policy
    ok = (r("use_command_default", None, default=0.02) == 0.02
          and r("use_command_default", 0.11) == 0.11
          and r("zero", 0.11) == 0.0
          and r("hold_last", None, last_adopted=0.07, default=0.02) == 0.07
          and r("hold_last", None, default=0.02) == 0.02
          and r("revert_to_baseline", 0.11, baseline=0.03, default=0.02)
          == 0.03)
    check("判据三/通道策略四则可执行", ok)
    try:
        r("no_such_policy", None)
        ok = False
    except ValueError:
        ok = True
    check("判据三/未知策略显式失败", ok)

    # 判据三：装配层不读主程序全局字典（源码静态检查）
    src = open(os.path.join(ROOT, "modules", "motion_action",
                            "parameters.py"), encoding="utf-8").read()
    check("判据三/不引用主程序全局与全局字典",
          "main_sim" not in src and "globals()" not in src
          and "_G[" not in src)

    # ── F01：尾舵对照旧真实驱动（复用 Fil 复现的关节接口替身） ──
    from modules import wing_actuation as WA
    from modules.aero.kinematics import WingKinematics

    class ArticulationCapture:
        joint_names = list(T.NINE_JOINT_CHANNELS[0].__class__.__mro__) \
            and [j.name for j in T.NINE_JOINT_CHANNELS]

        def set_joint_position_targets(self, positions, joint_names):
            self.targets = dict(zip(
                joint_names, map(float, positions.reshape(-1))))

    WA._G["_DP_STATE"] = {"freq": None}
    WA._G["_SIDE_PULSE_ON"] = False
    WA._G["_FW_IID_ULP"] = None
    f01_all = True
    f01_detail = []
    for label, command in (
            ("无指令", {}),
            ("明确零值", {"elevator": 0.0, "aileron": 0.0, "rudder": 0.0}),
            ("非零自动指令", {"elevator": 0.11, "aileron": 0.03,
                            "rudder": 0.02})):
        params, _adoption = assemble_parameters(
            {"auto": FreqSource("auto", 1.8)}, dict(bl), dict(command),
            recipe_channels=table)
        new = {j: params[j].center + params[j].scale * 0.0
               + params[j].control_correction
               for j in ("tail_L", "tail_R")}
        art = ArticulationCapture()
        WA._DRIVE_N = 1
        ap = ({"freq": 1.8, "dA": 0.0, "dlag": 0.0, "dfreq": 0.0, **command}
              if command else None)
        WA._drive_wing_joints(None, art, WingKinematics(), ap_out=ap,
                              step_count=30)
        legacy = {j: art.targets[j] for j in new}
        match = all(abs(new[j] - legacy[j]) < 1e-6 for j in new)
        f01_all = f01_all and match
        f01_detail.append("%s 新%+.4f/%+.4f 旧%+.4f/%+.4f%s"
                          % (label, new["tail_L"], new["tail_R"],
                             legacy["tail_L"], legacy["tail_R"],
                             "" if match else " 不符"))
    check("F01/尾舵三情形与旧真实驱动逐值一致（配平只采用一次）",
          f01_all, "；".join(f01_detail))
    # F01：采用身份与最终目标（缺省/明确零值/非零自动）
    p_none, _ = assemble_parameters({"auto": FreqSource("auto", 1.8)},
                                    dict(bl), {}, recipe_channels=table)
    p_zero, _ = assemble_parameters(
        {"auto": FreqSource("auto", 1.8)}, dict(bl),
        {"elevator": 0.0, "aileron": 0.0}, recipe_channels=table)
    p_auto, _ = assemble_parameters(
        {"auto": FreqSource("auto", 1.8)}, dict(bl),
        {"elevator": 0.11, "aileron": 0.03}, recipe_channels=table)
    ok = (abs(p_none["tail_L"].control_correction - trim) < 1e-12
          and p_none["tail_L"].command_identity == P.ADOPT_TRIM_ONCE
          and abs(p_zero["tail_L"].control_correction) < 1e-12
          and p_zero["tail_L"].command_identity == P.ADOPT_ABSOLUTE_COMMAND
          and abs(p_auto["tail_L"].control_correction - 0.14) < 1e-12
          and abs(p_auto["tail_R"].control_correction - 0.08) < 1e-12
          and p_auto["tail_L"].command_identity == P.ADOPT_ABSOLUTE_COMMAND
          and p_none["tail_L"].trim_reference == trim)
    check("F01/缺省配平一次、明确零值与绝对命令三身份分列", ok,
          "无指令=%s(%s) 零=%s(%s) 自动=%s(%s)"
          % (p_none["tail_L"].control_correction,
             p_none["tail_L"].command_identity,
             p_zero["tail_L"].control_correction,
             p_zero["tail_L"].command_identity,
             p_auto["tail_L"].control_correction,
             p_auto["tail_L"].command_identity))

    # ── F05：逐通道策略经真实装配入口执行 ──
    pa = ParameterAssembly(
        {"auto": FreqSource("auto", 1.8)}, dict(bl), {"rudder": 0.2},
        recipe_channels=table)
    first = pa.assemble()
    pa.command = {}
    default_missing = pa.assemble()
    pa2 = ParameterAssembly(
        {"auto": FreqSource("auto", 1.8)}, dict(bl), {},
        channel_policies={"tail_yaw": "hold_last"},
        last_adopted={"tail_yaw": 0.2}, recipe_channels=table)
    held = pa2.assemble()
    pa3 = ParameterAssembly(
        {"auto": FreqSource("auto", 1.8)}, dict(bl), {"rudder": 0.2},
        channel_policies={"tail_yaw": "zero"}, recipe_channels=table)
    zeroed = pa3.assemble()
    pa4 = ParameterAssembly(
        {"auto": FreqSource("auto", 1.8)}, dict(bl), {"rudder": 0.2},
        channel_policies={"tail_yaw": "revert_to_baseline"},
        mode_baselines={"tail_yaw": 0.05}, recipe_channels=table)
    exited = pa4.assemble()
    ok = (abs(first["tail_yaw"].control_correction - 0.2) < 1e-12
          and abs(default_missing["tail_yaw"].control_correction) < 1e-12
          and default_missing["tail_yaw"].command_identity
          == P.ADOPT_MISSING_DEFAULT
          and abs(held["tail_yaw"].control_correction - 0.2) < 1e-12
          and held["tail_yaw"].command_identity == P.ADOPT_HOLD_LAST
          and abs(zeroed["tail_yaw"].control_correction) < 1e-12
          and zeroed["tail_yaw"].command_identity == P.ADOPT_ZERO
          and abs(exited["tail_yaw"].control_correction - 0.05) < 1e-12
          and exited["tail_yaw"].command_identity == P.ADOPT_MODE_EXIT)
    check("F05/缺省、归零、保持、模式退出四种策略经真实装配执行", ok,
          "首轮=%s 缺省缺失=%s(%s) 保持=%s(%s) 归零=%s(%s) 退出=%s(%s)"
          % (first["tail_yaw"].control_correction,
             default_missing["tail_yaw"].control_correction,
             default_missing["tail_yaw"].command_identity,
             held["tail_yaw"].control_correction,
             held["tail_yaw"].command_identity,
             zeroed["tail_yaw"].control_correction,
             zeroed["tail_yaw"].command_identity,
             exited["tail_yaw"].control_correction,
             exited["tail_yaw"].command_identity))
    # F05：门控与差频作为独立透传输入进入通道参数
    pg, ag = assemble_parameters(
        {"auto": FreqSource("auto", 1.8)}, dict(bl),
        {"gate_L": 0.0, "dfreq": 0.3}, recipe_channels=table)
    ok = (pg["flap_L"].gate_value == 0.0
          and pg["flap_R"].gate_value is None
          and abs(pg["flap_L"].differential_frequency_hz - 0.3) < 1e-12
          and "不叠加" in ag["dfreq"])
    check("F05/门控零与差频请求进入通道参数（登记不叠加）", ok,
          "gate_L=%s gate_R=%s dfreq=%s"
          % (pg["flap_L"].gate_value, pg["flap_R"].gate_value,
             pg["flap_L"].differential_frequency_hz))
    # F05：采用记录逐通道写明身份
    ok = ("channel:tail_yaw" in pa.adoption
          and "channel:flap_L" in pa.adoption
          and "missing_default" in pa.adoption["channel:tail_yaw"])
    check("F05/采用记录逐通道含身份", ok)

    # ── F05（v3 缺口一）：Fil 四个逐关节策略真实反例逐值复测 ──
    def _tail_targets(command, policies=None, last=None, mode=None):
        p, _a = assemble_parameters(
            {"auto": FreqSource("auto", 1.8)}, dict(bl), dict(command),
            channel_policies=policies or {},
            last_adopted=last or {}, mode_baselines=mode or {},
            recipe_channels=table)
        return p["tail_L"].control_correction, p["tail_R"].control_correction

    cases = (
        ("仅右尾归零", {"elevator": 0.11, "aileron": 0.03},
         {"tail_R": "zero"}, None, (0.14, 0.0)),
        ("仅左尾归零（含非零副翼）", {"elevator": 0.11, "aileron": 0.03},
         {"tail_L": "zero"}, None, (0.0, 0.08)),
        ("两尾保持不同上一值", {},
         {"tail_L": "hold_last", "tail_R": "hold_last"},
         {"tail_L": 0.2, "tail_R": -0.1}, (0.2, -0.1)),
        ("左尾保持且非零副翼", {"elevator": 0.11, "aileron": 0.03},
         {"tail_L": "hold_last"}, {"tail_L": 0.2}, (0.2, 0.08)),
    )
    all_ok = True
    details = []
    for label, command, pol, last, want in cases:
        got = _tail_targets(command, pol, last)
        good = abs(got[0] - want[0]) < 1e-12 and abs(got[1] - want[1]) < 1e-12
        all_ok = all_ok and good
        details.append("%s 得 %+.2f/%+.2f 应 %+.2f/%+.2f%s"
                       % (label, got[0], got[1], want[0], want[1],
                          "" if good else " 不符"))
    check("F05/逐关节最终目标策略四个真实反例（候选先形成再逐关节采用）",
          all_ok, "；".join(details))
    # 保持与退出基准对应最终关节目标，不与副翼二次混合（左保持 0.2 而
    # 候选含副翼 0.14 时结果仍为 0.2）
    got = _tail_targets({"elevator": 0.11, "aileron": 0.03},
                        {"tail_L": "hold_last"}, {"tail_L": 0.2})
    check("F05/保持值为最终关节目标不被副翼再混合", abs(got[0] - 0.2) < 1e-12,
          "左尾=%s（若被再混合会得 0.23）" % got[0])

    # F05（v3）：未知策略在所有声明通道均定位拒绝（含右尾与翼面）
    unk_all = True
    unk_why = []
    for joint in ("tail_yaw", "tail_R", "flap_L", "twist_R"):
        try:
            assemble_parameters(
                {"auto": FreqSource("auto", 1.8)}, dict(bl),
                {"elevator": 0.11},
                channel_policies={joint: "unknown_policy"},
                recipe_channels=table)
            unk_all = False
            unk_why.append("%s 未拒绝" % joint)
        except ValueError as exc:
            if joint not in str(exc):
                unk_all = False
                unk_why.append("%s 错误未定位：%s" % (joint, exc))
    check("F05/未知策略在所有声明通道定位拒绝（含右尾与翼面）", unk_all,
          "；".join(unk_why) or "tail_yaw、tail_R、flap_L、twist_R 全拒绝")
    # F05（v3）：折翼无指令适用量，声明策略定位拒绝（不静默忽略）
    try:
        assemble_parameters(
            {"auto": FreqSource("auto", 1.8)}, dict(bl), {},
            channel_policies={"fold_L": "zero"}, recipe_channels=table)
        fold_rejected = False
        fold_msg = "fold_L 策略被静默忽略"
    except ValueError as exc:
        fold_rejected = "fold_L" in str(exc) and "不支持" in str(exc)
        fold_msg = str(exc)[:80]
    check("F05/折翼声明策略定位拒绝（无指令适用量组合）", fold_rejected,
          fold_msg)
    # F05（v3）：翼面通道策略真实执行——拍动 dA 与扭转 dlag 逐关节采用
    p_w, _ = assemble_parameters(
        {"auto": FreqSource("auto", 1.8)}, {**bl, "da_joint_amp_on": True},
        {"dA": 0.05, "dlag": 0.01},
        channel_policies={"flap_L": "zero", "twist_R": "hold_last"},
        last_adopted={"twist_R": 0.02}, recipe_channels=table)
    ok = (abs(p_w["flap_L"].amplitude_differential) < 1e-12
          and abs(p_w["flap_R"].amplitude_differential + 0.05) < 1e-12
          and abs(p_w["twist_L"].lag_s - (-0.0674 + 0.01)) < 1e-12
          and abs(p_w["twist_R"].lag_s - (-0.0674 - 0.02)) < 1e-12)
    check("F05/翼面策略真实执行（左拍归零右拍保留，右扭保持 0.02）", ok,
          "ampL=%s ampR=%s lagL=%+.4f lagR=%+.4f"
          % (p_w["flap_L"].amplitude_differential,
             p_w["flap_R"].amplitude_differential,
             p_w["twist_L"].lag_s, p_w["twist_R"].lag_s))

    # 判据四：dA 双语义按基准开关分列；dlag 以秒叠加滞后不混入角度
    pa_a, da_off = assemble_parameters(
        {}, {**bl, "da_joint_amp_on": True},
        {"dA": 0.05, "dlag": 0.01}, recipe_channels=table)
    pa_b, _ = assemble_parameters(
        {}, {**bl, "da_joint_amp_on": False},
        {"dA": 0.05, "dlag": 0.01}, recipe_channels=table)
    check("判据四/dA 幅值差动与旧角偏置按开关分列",
          pa_a["flap_L"].amplitude_differential == 0.05
          and pa_a["flap_L"].legacy_angular_offset == 0.0
          and pa_b["flap_L"].amplitude_differential == 0.0
          and pa_b["flap_L"].legacy_angular_offset == 0.05)
    check("判据四/dlag 秒单位滞后差动左右异号且不入角度",
          abs(pa_a["twist_L"].lag_s - (-0.0674 + 0.01)) < 1e-12
          and abs(pa_a["twist_R"].lag_s - (-0.0674 - 0.01)) < 1e-12
          and pa_a["twist_L"].legacy_angular_offset == 0.0)
    check("判据四/中置上反保留", pa_a["flap_L"].center == 0.346
          and pa_a["flap_L"].center_source == "kinematics.MEAN")

    # F04：差动参与由配方表驱动（右扭转声明无 dlag 时滞后不改动）
    import dataclasses as dc
    table_no_rdlag = dict(table)
    table_no_rdlag["twist_R"] = dc.replace(table["twist_R"],
                                           differential_keys=())
    p_nr, _ = assemble_parameters(
        {}, dict(bl), {"dlag": 0.01}, recipe_channels=table_no_rdlag)
    ok = (abs(p_nr["twist_L"].lag_s - (-0.0674 + 0.01)) < 1e-12
          and abs(p_nr["twist_R"].lag_s - (-0.0674)) < 1e-12)
    check("F04/右扭转表声明无 dlag 时装配不改动其滞后（表驱动一致）", ok,
          "L=%+0.4f R=%+0.4f（期望 -0.0574/-0.0674）"
          % (p_nr["twist_L"].lag_s, p_nr["twist_R"].lag_s))

    # 补充二：反馈归属表五行可装配
    rows_ok = len(FEEDBACK_ATTRIBUTION_TABLE) == 5 and all(
        row.key and row.request_source and row.unit and row.final_adopter
        and row.effective_at for row in FEEDBACK_ATTRIBUTION_TABLE)
    check("补充二/反馈归属表五行逐项登记", rows_ok,
          "keys=%s" % [r.key for r in FEEDBACK_ATTRIBUTION_TABLE])
    check("补充二/装配采用记录含反馈归属",
          "feedback_attribution" in da_off and
          "resolved_freq" in da_off["feedback_attribution"])

    # 判据五：同一输出规则导数
    y, dy, note = output_with_derivative(0.3, 0.2, 1.0, 0.5, control=0.1,
                                         d_center=0.01, d_scale=0.02,
                                         d_control=0.03)
    expect = 0.01 + 0.02 * 1.0 + 0.2 * 0.5 + 0.03
    check("判据五/链式导数（含时变中心幅值）",
          abs(y - (0.3 + 0.2 + 0.1)) < 1e-12 and abs(dy - expect) < 1e-12)
    y2, dy2, n2 = output_with_derivative(0.3, 0.2, 1.0, None)
    check("判据五/必需导数不可用不填假零",
          dy2 is None and "不填假零" in n2, n2)
    _, dy_in, _ = output_with_derivative(0.0, 0.2, 0.0, 1.0,
                                         limits=(-1.0, 1.0))
    _, dy_sat, ns = output_with_derivative(0.0, 0.2, 5.0, 1.0,
                                           limits=(-1.0, 0.5))
    _, dy_bnd, nb = output_with_derivative(0.5, 1.0, 0.0, 1.0,
                                           limits=(-1.0, 0.5))
    check("判据五/限幅导数同一输出规则",
          abs(dy_in - 0.2) < 1e-12 and dy_sat == 0.0 and "真实导数" in ns
          and dy_bnd is None and "不可用" in nb,
          "in=%s sat=%s(%s) bnd=%s(%s)" % (dy_in, dy_sat, ns, dy_bnd, nb))

    # F07：不新增全通道限幅——装配输出不经机械限位截断（限位是登记层）
    p_over, _ = assemble_parameters(
        {"auto": FreqSource("auto", 1.8)}, dict(bl),
        {"elevator": 2.0}, recipe_channels=table)
    check("F07/装配不新增机械限幅（2.0 rad 指令原样进入修正通道）",
          abs(p_over["tail_L"].control_correction - 2.0) < 1e-12,
          "修正=%s（机械限位 ±0.5 仅登记，不在此执行）"
          % p_over["tail_L"].control_correction)

    # ── F06：发布帧只读（外部别名与消费者修改两类反例） ──
    node_values = {"chi_c": 0.1}
    snapshot = T.RhythmSnapshot(1, 0.01, node_values, {})
    node_values["chi_c"] = 9.0
    alias_ok = snapshot.node_values["chi_c"] == 0.1
    try:
        snapshot.node_values["chi_c"] = 5.0
        consumer_ok = False
        why = "快照映射可写"
    except TypeError:
        consumer_ok = True
        why = ""
    check("F06/外部别名修改不影响快照", alias_ok,
          "快照 chi_c=%s（外部字典已改为 9.0）" % snapshot.node_values["chi_c"])
    check("F06/消费者修改快照被阻", consumer_ok, why)
    ch = T.ChannelAction("flap_L", "value", 0.2, "probe", True, "analytic")
    frame = T.StepAction(1, 0.01, snapshot, {"flap_L": ch})
    try:
        frame.channels.clear()
        frame_ok = False
        frame_why = "帧通道字典可清空"
    except TypeError:
        frame_ok = len(frame.channels) == 1
        frame_why = ""
    check("F06/消费者清空帧通道被阻", frame_ok, frame_why)


def _load_illegal_variants():
    """由合法配方文档派生非法变体文档（F02：完整序列化送真实装载）。

    返回 (变体名, 变体文档) 列表；每个文档独立修改，逐个写入临时文件
    后调用真实 load_recipe 入口。
    """
    doc = json.load(open(RECIPE_PATH, encoding="utf-8"))

    def relation_cycle_only(d):
        d["parent_child"].append({
            "child": "chi_c", "parent": "chi_R", "frequency_ratio": 1.0,
            "offset_coordinate": "cumulative_beats", "offset_value": 0.0,
            "offset_source": "零", "differential_source": None,
            "effective_from_beat": 0.0,
        })

    def nonzero_offset_v1_style(d):
        d["parent_child"][1].update(
            offset_value=0.1136,
            offset_source="refnorm_v1_phasezero_m113.6 版本字符串来源")

    def node_parent_ghost(d):
        d["rhythm_nodes"][2].update(parent="ghost")

    variants = [
        ("节点父引用不存在", node_parent_ghost),
        ("同名节律节点", lambda d: d["rhythm_nodes"].append(
            copy.deepcopy(d["rhythm_nodes"][0]))),
        ("节点坐标单位冲突",
         lambda d: d["rhythm_nodes"][0].update(unit="rad")),
        ("同一子节点重复父子关系", lambda d: d["parent_child"].append(
            copy.deepcopy(d["parent_child"][0]))),
        ("关系表派生环", relation_cycle_only),
        ("差动来源不存在",
         lambda d: d["parent_child"][0].update(differential_source="ghost")),
        ("重复提供者绑定", lambda d: d["provider_bindings"].append(
            copy.deepcopy(d["provider_bindings"][0]))),
        ("节点未知字段",
         lambda d: d["rhythm_nodes"][0].update(typo_field=1)),
        ("频率比例非有限",
         lambda d: d["parent_child"][0].update(frequency_ratio=float("nan"))),
        ("通道绑定引用不存在",
         lambda d: d["channels"][0].update(provider_binding="no_such")),
        ("绑定行与通道提供者不一致",
         lambda d: d["channels"][0].update(provider="ghost_provider")),
        ("折翼偏移两处重复",
         lambda d: d["parent_child"][2].update(
             offset_value=0.3508,
             offset_source="recipe_choice:二次偏移（应被唯一持有规则拒绝）")),
        ("非零偏移无来源登记",
         lambda d: d["parent_child"][0].update(offset_value=0.2,
                                               offset_source=None)),
        ("v1 形态无来源右翼相差", nonzero_offset_v1_style),
        ("config 声明与解析不符",
         lambda d: d["parent_child"][1].update(offset_value=0.05)),
        ("空通道表", lambda d: d.update(channels=[])),
        ("通道参考坐标与绑定不一致",
         lambda d: d["channels"][0].update(reference_coordinate="seconds")),
        ("通道相位偏移为列表", lambda d: d["channels"][4].update(
            phase_offset_rad=[1])),
        ("带配置来源的偏移值为列表",
         lambda d: d["parent_child"][1].update(offset_value=[1])),
        ("镜像字段为字符串假值", lambda d: d["channels"][1].update(
            mirrored="false")),
        ("节点派生字段为字符串假值", lambda d: d["rhythm_nodes"][2].update(
            derived="false")),
        ("提供者绑定周期字段为字符串真值",
         lambda d: d["provider_bindings"][0].update(periodic="true")),
        ("中置上反保留字段为字符串", lambda d: d.update(
            preserves_mean_dihedral="true")),
    ]
    out = []
    for name, mutate in variants:
        d = copy.deepcopy(doc)
        mutate(d)
        out.append((name, d))
    return out


def cmd_entry():
    import modules.contracts as C
    from modules.motion_action import types as T, RecipeError

    # 判据六：原指令键与九关节名单保持
    check("判据六/指令十二键保持", len(C.COMMAND_KEYS) == 12
          and "freq" in C.COMMAND_KEYS and "duty" in C.COMMAND_KEYS)
    check("判据六/旧配方标识与契约常量",
          C.LEGACY_RECIPE_ID == "legacy_current"
          and C.MOTION_ACTION_TYPES_MODULE == "modules.motion_action.types")

    # 判据六：旧抽象执行器保持可实例化（无新增抽象方法）
    class _OldStyleActuator(C.Actuator):
        def initialize(self, config): pass
        def reset(self): pass
        def apply(self, commands, dt): return {}
        def compute(self, state): return {}
        def read(self): return {}
        def setup(self, config): pass
        def step(self, actions=None): return {}
        def close(self): pass

    class _Act(C.Actuator):
        def reset(self): pass
        def apply(self, commands, dt): return {}

    try:
        _Act()
        ok = True
    except TypeError as e:
        ok = False
        print("    实例化错误：%s" % e)
    check("判据六/抽象执行器实例化保持（无新增抽象方法）", ok)

    # 判据六：可选消费协议鸭子类型判定，不要求继承
    class _Consumer:
        def apply_frame(self, frame): return {}

    class _NotConsumer:
        def push(self, frame): return {}

    check("判据六/可选消费协议运行时判定",
          isinstance(_Consumer(), C.ActionFrameConsumer)
          and not isinstance(_NotConsumer(), C.ActionFrameConsumer))
    ma = C.import_motion_action_types()
    check("判据六/契约层经函数内导入取到新类型包",
          ma is not None and hasattr(ma, "StepAction"))

    # 判据六：默认配置为旧配方且记录默认关
    from config.sim_config import ActionExpressionConfig as AEC
    check("判据六/默认旧配方选择", AEC.RECIPE_ID == "legacy_current"
          and AEC.RECORD_ACTIONS is False)

    # 判据六：生产入口 resolve_action_recipe——默认 None（回归铁律）
    from modules import sim_env_bridge as SEB
    check("判据六/生产入口默认不装载新配方",
          SEB.resolve_action_recipe({}) is None)

    # 判据六：装载首个合作配方成功
    saved = (AEC.RECIPE_ID, AEC.RECIPE_PATH, AEC.RECIPES_DIR,
             AEC.RECORD_ACTIONS)
    try:
        rec = SEB.resolve_action_recipe(
            {"FLAPPER_ACTION_RECIPE": "cooperative_v1"})
        check("判据六/生产入口装载 cooperative_v1",
              rec is not None and rec.name == "cooperative_v1"
              and len(rec.channels) == 9)
        AEC.RECORD_ACTIONS = False
        SEB.resolve_action_recipe(
            {"FLAPPER_ACTION_RECIPE": "cooperative_v1",
             "FLAPPER_ACTION_RECORD": "1"})
        check("判据六/记录开关覆盖生效", AEC.RECORD_ACTIONS is True)

        # 判据六：未知配方可定位错误，不回退
        try:
            SEB.resolve_action_recipe(
                {"FLAPPER_ACTION_RECIPE": "no_such_recipe"})
            ok, msg = False, ""
        except RecipeError as e:
            ok = "no_such_recipe" in str(e) and "cooperative_v1" in str(e)
            msg = str(e)
        check("判据六/未知配方可定位报错", ok, msg[:120])

        # 判据六（返修）：请求名与路径装载结果不符时报错，不静默装回
        AEC.RECIPE_PATH = RECIPE_PATH
        try:
            SEB.resolve_action_recipe(
                {"FLAPPER_ACTION_RECIPE": "unknown_recipe_name"})
            ok, msg = False, ""
        except RecipeError as e:
            ok = "unknown_recipe_name" in str(e) and "cooperative_v1" \
                in str(e)
            msg = str(e)
        check("判据六/请求名与路径装载配方不符时报错（不静默回退）", ok,
              msg[:120])

        # ── F02：非法变体逐个完整序列化后送真实装载入口 ──
        import tempfile as _tf
        with _tf.TemporaryDirectory(
                prefix="i219_v2_") as td:
            for name, doc in _load_illegal_variants():
                p = os.path.join(td, "variant.json")
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(doc, f, ensure_ascii=False, indent=1)
                try:
                    T.load_recipe(p)
                    ok, msg = False, "被接受"
                except RecipeError as e:
                    msg = str(e)
                    ok = ("（位置：" in str(e)) and len(str(e)) > 20
                except Exception as e:
                    ok, msg = False, "外泄异常 %s：%s" % (type(e).__name__, e)
                check("F02/非法变体经真实装载拒绝（%s）" % name, ok,
                      msg[:110])

        # ── v3 导出边界：asdict 导出对象再装载回环 ──
        import dataclasses as dc_rt
        import tempfile as _tf_rt
        loaded = T.load_recipe(RECIPE_PATH)
        exported = dc_rt.asdict(loaded)
        with _tf_rt.TemporaryDirectory(prefix="i219_v3_rt_") as td:
            rt_path = os.path.join(td, "roundtrip.json")
            with open(rt_path, "w", encoding="utf-8") as f:
                json.dump(exported, f, ensure_ascii=False, indent=1)
            try:
                reloaded = T.load_recipe(rt_path)
                rt_ok = reloaded.name == loaded.name
                rt_msg = "回环装载成功"
            except Exception as e:
                reloaded = None
                rt_ok = False
                rt_msg = "回环失败 %s：%s" % (type(e).__name__, e)
        chi_r_src = next(r for r in loaded.parent_child
                         if r.child == "chi_R")
        check("v3导出边界/asdict 导出再装载回环成功", rt_ok, rt_msg)
        check("v3导出边界/机器来源保持纯引用、解析值在独立字段",
              chi_r_src.offset_source
              == "config:FlapKinematicsConfig.PHASE_OFFSET_LR:deg/360"
              and chi_r_src.offset_resolution is not None
              and "解析" in chi_r_src.offset_resolution,
              "source=%s resolution=%s" % (chi_r_src.offset_source,
                                           chi_r_src.offset_resolution))
    finally:
        (AEC.RECIPE_ID, AEC.RECIPE_PATH, AEC.RECIPES_DIR,
         AEC.RECORD_ACTIONS) = saved

    # 回归：默认入口再次确认返回 None 且配置还原
    check("判据六/检查后配置还原默认",
          AEC.RECIPE_ID == "legacy_current"
          and SEB.resolve_action_recipe({}) is None)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in ("all", "types"):
        cmd_types()
    if mode in ("all", "assembly"):
        cmd_assembly()
    if mode in ("all", "entry"):
        cmd_entry()
    fails = [r for r in RESULTS if not r[1]]
    print("\n==== I-219 检查：%d 项，通过 %d，失败 %d ===="
          % (len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    for name, _, detail in fails:
        print("FAIL %s %s" % (name, detail))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
