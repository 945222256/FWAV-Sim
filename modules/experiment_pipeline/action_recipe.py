# -*- coding: utf-8 -*-
"""单变量动作实验的版本化配方与运行输入。

首次支持固定台架上的集体拍频。配方、唯一变量、允许联动、采样窗口和
资格规则先于数据固定；未知变量明确拒绝，不能临场换算法或补通过。
本模块只使用标准库，供离线编译器和实际输入控制器共同读取。
"""
import hashlib
import json
import math
import os
import re
from pathlib import Path

VERSION = 1
CASE_ENV = "FLAPPER_ACTION_EXPERIMENT_CASE"
CASE_SCALES = {"baseline": 0.0, "positive": 1.0, "negative": -1.0, "half": 0.5}
FIXED_KEYS = {"aero_model", "initial_position_world_m", "body_free_dofs",
              "wind_world_m_per_s", "tether_enabled", "controller_enabled",
              "tail_elevator_rad", "tail_aileron_rad", "tail_rudder_rad"}
TOP_KEYS = {"schema_version", "recipe_id", "item", "description", "variable",
            "fixed", "sampling", "qualification", "source_refs"}


def canonical_hash(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                              allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_recipe(recipe):
    errors = []
    if not isinstance(recipe, dict):
        return ["配方根须为对象"]
    if set(recipe) != TOP_KEYS:
        errors.append("配方字段不完整或包含未支持字段：%s" % sorted(set(recipe) ^ TOP_KEYS))
    if type(recipe.get("schema_version")) is not int or recipe["schema_version"] != VERSION:
        errors.append("配方版本未支持")
    for key in ("recipe_id", "item", "description"):
        if not isinstance(recipe.get(key), str) or not recipe[key].strip():
            errors.append("%s 须为非空字符串" % key)
    if not re.fullmatch(r"[a-z0-9_-]{1,64}", str(recipe.get("recipe_id", ""))):
        errors.append("配方标识须为安全的英文小写、数字或下划线短名")
    variable = recipe.get("variable") or {}
    if not isinstance(variable, dict):
        errors.append("variable 须为对象")
        variable = {}
    if set(variable) != {"name", "unit", "baseline", "delta", "allowed_linked_outputs", "input_range_hz"}:
        errors.append("变量定义缺字段或包含未支持字段")
    if variable.get("name") != "collective_frequency" or variable.get("unit") != "Hz":
        errors.append("当前配方只支持以赫兹声明的全局拍频，不自动替换变量")
    baseline, delta = variable.get("baseline"), variable.get("delta")
    bounds = variable.get("input_range_hz")
    if not (_number(baseline) and _number(delta) and delta > 0):
        errors.append("基准和扰动须为有限数，扰动须为正")
    if not (isinstance(bounds, list) and len(bounds) == 2 and all(_number(x) for x in bounds)
            and 0 < bounds[0] < bounds[1]):
        errors.append("输入范围须为正的上下界；范围不表示稳定能力")
    elif _number(baseline) and _number(delta):
        for case, scale in CASE_SCALES.items():
            if not bounds[0] <= baseline + scale * delta <= bounds[1]:
                errors.append("%s 档超出声明输入范围" % case)
    linked = variable.get("allowed_linked_outputs")
    if not isinstance(linked, list) or not linked or not all(isinstance(x, str) and x for x in linked):
        errors.append("须显式声明拍频引起的运动和作用量联动")
    fixed = recipe.get("fixed")
    if not isinstance(fixed, dict) or set(fixed) != FIXED_KEYS:
        errors.append("固定条件须完整声明当前受支持字段")
        fixed = {}
    dofs = fixed.get("body_free_dofs")
    if (not isinstance(dofs, dict) or set(dofs) != {"tx", "ty", "tz", "rx", "ry", "rz"}
            or any(v is not False for v in dofs.values())):
        errors.append("本配方仅支持六自由度逐物理步钉住台架")
    wind = fixed.get("wind_world_m_per_s")
    if not (isinstance(wind, list) and len(wind) == 3 and all(_number(v) and v == 0 for v in wind)):
        errors.append("当前配方限静气；其他来流须另行明确输入与来源")
    for key in ("tether_enabled", "controller_enabled"):
        if fixed.get(key) is not False:
            errors.append("%s 须明确关闭" % key)
    pos = fixed.get("initial_position_world_m")
    if not (isinstance(pos, list) and len(pos) == 3 and all(_number(v) for v in pos)
            and pos[:2] == [0.0, 0.0]):
        errors.append("当前台架初始位置须完整且水平位置为零")
    for key in ("tail_elevator_rad", "tail_aileron_rad", "tail_rudder_rad"):
        if not _number(fixed.get(key)):
            errors.append("%s 须为有限弧度值" % key)
    if not isinstance(fixed.get("aero_model"), str) or not fixed["aero_model"]:
        errors.append("须给出实际气动模型")
    sampling = recipe.get("sampling") or {}
    if not isinstance(sampling, dict) or set(sampling) != {"physics_dt_s", "settle_s", "measure_s", "minimum_cycles"}:
        errors.append("采样定义缺字段或包含未支持字段")
        sampling = {}
    if not all(_number(sampling.get(k)) and sampling[k] > 0 for k in
               ("physics_dt_s", "settle_s", "measure_s", "minimum_cycles")):
        errors.append("步长、预热、测量窗和最少周期数须为正有限数")
    elif _number(baseline) and _number(delta) and sampling["measure_s"] * (baseline - delta) < sampling["minimum_cycles"]:
        errors.append("最慢档测量窗不能覆盖预先声明的最少周期数")
    if sampling and all(_number(sampling.get(k)) and sampling[k] > 0 for k in
                        ("physics_dt_s", "settle_s", "measure_s")):
        for key in ("settle_s", "measure_s"):
            steps = sampling[key] / sampling["physics_dt_s"]
            if abs(steps - round(steps)) > 1e-9:
                errors.append("%s 须包含整数个物理步" % key)
    q = recipe.get("qualification") or {}
    if not isinstance(q, dict) or set(q) != {"frequency_readback_tolerance_hz", "tracking_error_limit_rad", "approved_misses"}:
        errors.append("资格规则缺字段或包含未支持字段")
    else:
        if not _number(q.get("frequency_readback_tolerance_hz")) or q["frequency_readback_tolerance_hz"] < 0:
            errors.append("频率读回预算须为预先声明的非负有限数")
        limit = q.get("tracking_error_limit_rad")
        if limit is not None and (not _number(limit) or limit < 0):
            errors.append("跟踪误差阈值须有批准数值；无阈值时显式空值，只报告实测")
        if not isinstance(q.get("approved_misses"), dict):
            errors.append("通道例外须显式声明，缺失不能默认豁免")
        elif (not isinstance(q["approved_misses"].get("tgt"), dict)
              or q["approved_misses"] != {"tgt": {"max": 1, "at_steps": [1],
                 "reason": q["approved_misses"]["tgt"].get("reason")}}):
            errors.append("本配方只支持有来源说明的首步目标例外，不允许放宽其他缺失")
        elif not isinstance(q["approved_misses"]["tgt"]["reason"], str) or not q["approved_misses"]["tgt"]["reason"].strip():
            errors.append("首步例外须有明确原因")
    refs = recipe.get("source_refs")
    if not isinstance(refs, list) or not refs or not all(isinstance(x, str) and x.strip() for x in refs):
        errors.append("须列出变量、约束和规则来源")
    return errors


def compile_plan(recipe):
    errors = validate_recipe(recipe)
    if errors:
        raise ValueError("；".join(errors))
    cases = []
    for name, scale in CASE_SCALES.items():
        case = {"schema_version": VERSION, "recipe_id": recipe["recipe_id"],
                "item": recipe["item"], "case": name, "scale": scale,
                "frequency_hz": recipe["variable"]["baseline"] + scale * recipe["variable"]["delta"],
                "fixed": recipe["fixed"], "sampling": recipe["sampling"],
                "recipe_sha256": canonical_hash(recipe)}
        case["case_sha256"] = canonical_hash(case)
        cases.append(case)
    return {"schema_version": VERSION, "recipe": recipe,
            "recipe_sha256": canonical_hash(recipe), "cases": cases,
            "case_order": list(CASE_SCALES), "repeat_order": ["A", "B"]}


def load_runtime_case(path):
    case = read_json(path)
    claimed = case.pop("case_sha256", None)
    if claimed != canonical_hash(case):
        raise ValueError("运行输入内容与已编译配方摘要不一致")
    case["case_sha256"] = claimed
    plan = read_json(Path(path).parent.parent / "plan.json")
    expected = compile_plan(plan["recipe"])
    if expected["recipe_sha256"] != plan.get("recipe_sha256"):
        raise ValueError("配方原文与编译计划摘要不一致")
    if case not in expected["cases"]:
        raise ValueError("运行档位不是已编译配方的完整原输入")
    return case


def parameter_snapshot(cfg):
    """保存公共标量及配置类；不序列化模块、函数、对象 repr 或凭证。"""
    def supported(value):
        if type(value).__module__.startswith("numpy") and hasattr(value, "tolist"):
            return supported(value.tolist())
        if value is None or type(value) in (bool, str, int):
            return value
        if type(value) is float and math.isfinite(value):
            return value
        if type(value) in (list, tuple):
            return [supported(v) for v in value]
        if type(value) is dict and all(type(k) is str for k in value):
            return {k: supported(v) for k, v in sorted(value.items())}
        raise TypeError("非参数类型")
    result = {}
    for key, value in sorted(vars(cfg).items()):
        if key.startswith("_"):
            continue
        if isinstance(value, type) and value.__module__ == cfg.__name__:
            values = {}
            for name, child in sorted(vars(value).items()):
                if not name.startswith("_"):
                    try:
                        values[name] = supported(child)
                    except TypeError:
                        pass
            result[key] = values
        else:
            try:
                result[key] = supported(value)
            except TypeError:
                pass
    return result


def bind_runtime_identity(case_path, case):
    """在每个真实子进程中，用统一入口生成的实际 tag 绑定独立运行身份。"""
    tag = os.environ.get("FLAPPER_TAG", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", tag):
        raise ValueError("实验须从带实际运行标识的统一入口启动")
    archive = Path(case_path).resolve().parent.parent / "runtime" / tag
    archive.mkdir(parents=True, exist_ok=True)
    os.environ.update(I186_ARCHIVE_DIR=str(archive),
                      I186_EXPERIMENT_ID=case["recipe_id"] + ":" + case["recipe_sha256"],
                      I186_RUN_ID=tag)
    return archive, tag


def apply_runtime_case(cfg, case):
    """只在显式配方入口调用；不改增益、力律或既有默认运行。"""
    fixed = case["fixed"]
    cfg.EXPERIMENT_MODE = "recipe"
    cfg.AERO_MODEL = fixed["aero_model"]
    cfg.WIND_ENABLED = False
    cfg.WIND_VECTOR = tuple(fixed["wind_world_m_per_s"])
    cfg.TETHER_ENABLED = False
    cfg.DP_HEIGHT_HOLD = False
    cfg.FLIGHT_CONTROLLER_ENABLED = False
    cfg.BENCH_FREE = ()
    cfg.BENCH_RELEASE = False
    cfg.BENCH_PITCH_DEG = 0.0
    cfg.DP_TARGET_Z = float(fixed["initial_position_world_m"][2])


def prepare_runtime_input(world, mav_view, host):
    """生产环境桥的显式接点；未选择实验配方时不导入配置、不改任何值。"""
    path = os.environ.get(CASE_ENV)
    if not path:
        return None
    import config.sim_config as cfg
    if os.environ.get("FLAPPER_NO_MANUAL") != "1":
        raise ValueError("配方采集须由统一入口隔离人工输入")
    case = load_runtime_case(path)
    archive, tag = bind_runtime_identity(path, case)
    apply_runtime_case(cfg, case)
    return PlannedInput(case, lambda: host.get("_SIM_T"), cfg, mav_view,
                        host, archive, tag, world=world)


class PlannedInput:
    """固定配方的确定性输入与实际条件观察；时钟由宿主显式提供。"""
    def __init__(self, case, clock, cfg, mav_view, host, output_dir, tag, world=None):
        import atexit
        self.case, self.clock, self.cfg = case, clock, cfg
        self.mav_view, self.host = mav_view, host
        self.world = world
        self.source = "experiment:recipe:%s:%s" % (case["recipe_id"], case["case"])
        self.path = Path(output_dir) / (tag + ".conditions.json")
        self.dt = case["sampling"]["physics_dt_s"]
        # Extra guard solve provides the final drive/native-clock observation
        # for the whole measurement window. It is outside the measurement.
        self.expected = round((case["sampling"]["settle_s"] + case["sampling"]["measure_s"]) / self.dt)
        self.summary = {"schema_version": VERSION, "recipe_sha256": case["recipe_sha256"],
                        "case_sha256": case["case_sha256"], "tag": tag,
                        "samples": 0, "expected_samples": self.expected,
                        "errors": [], "first_state": None, "max_body_reset_error": 0.0,
                        "fixed_adopted": None, "complete": False,
                        "sampling_definition": "每步求解后、台架状态重置后、翼指令下发前；不代表步内连续刚性固定"}
        self._last_step = 0
        self._phase = None
        self._parameters = None
        atexit.register(self.close)

    def _observe(self, t):
        from modules.experiment_pipeline.runtime_observer import read_mav_state
        step = round(t / self.dt)
        if step != self._last_step + 1 and len(self.summary["errors"]) < 12:
            self.summary["errors"].append("输入观察步号不连续：%s→%s" % (self._last_step, step))
        self._last_step = step
        self.summary["samples"] += 1
        state, error = read_mav_state(self.mav_view)
        if error:
            if len(self.summary["errors"]) < 12:
                self.summary["errors"].append(error)
        else:
            if self.summary["first_state"] is None:
                self.summary["first_state"] = state
            expected = self.case["fixed"]["initial_position_world_m"] + [1.0, 0.0, 0.0, 0.0] + [0.0] * 6
            actual = state["position_world_m"] + state["orientation_quaternion_wxyz"] + state["linear_velocity"] + state["angular_velocity"]
            self.summary["max_body_reset_error"] = max(self.summary["max_body_reset_error"],
                                                       max(abs(a - b) for a, b in zip(actual, expected)))
        cfg = self.cfg
        parameters = parameter_snapshot(cfg)
        if self._parameters is None:
            self._parameters = parameters
            self.summary["effective_parameters"] = parameters
            self.summary["effective_parameters_sha256"] = canonical_hash(parameters)
            try:
                self.summary["world_physics_dt_s"] = float(self.world.get_physics_dt())
            except Exception as exc:
                self.summary["errors"].append("无法读取实际 World 步长：" + repr(exc))
        elif parameters != self._parameters and len(self.summary["errors"]) < 12:
            self.summary["errors"].append("实际公共参数在采集中发生变化")
        adopted = {"aero_model": cfg.AERO_MODEL, "experiment_mode": cfg.EXPERIMENT_MODE,
                   "wind_enabled": cfg.WIND_ENABLED, "wind_world_m_per_s": list(cfg.WIND_VECTOR),
                   "tether_enabled": cfg.TETHER_ENABLED, "dp_height_hold": cfg.DP_HEIGHT_HOLD,
                   "controller_enabled": cfg.FLIGHT_CONTROLLER_ENABLED,
                   "body_free_axes": list(cfg.BENCH_FREE), "bench_pitch_deg": cfg.BENCH_PITCH_DEG,
                   "dp_target_z_m": cfg.DP_TARGET_Z, "physics_dt_s": cfg.PHYSICS_DT,
                   "side_pulse_enabled": self.host.get("_SIDE_PULSE_ON")}
        if self.summary["fixed_adopted"] is None:
            self.summary["fixed_adopted"] = adopted
        elif adopted != self.summary["fixed_adopted"] and len(self.summary["errors"]) < 12:
            self.summary["errors"].append("运行中固定条件发生变化")
        self.summary["last_observed_sim_t"] = t

    def read(self):
        t = self.clock()
        if not _number(t) or t < 0:
            raise ValueError("配方输入缺少有效仿真时钟，不以墙钟或零值替代")
        self._observe(t)
        phase = "settle" if t <= self.case["sampling"]["settle_s"] else "measure"
        if phase != self._phase:
            # 既有主循环读取此标签判断试验是否结束；阶段不是翼拍相位。
            write_json(Path(self.cfg.LOG_DIR) / "experiment_phase.json", {"phase": phase})
            self._phase = phase
        cfg, fixed = self.cfg, self.case["fixed"]
        freq = self.case["frequency_hz"]
        if not cfg.MANUAL_FREQ_MIN <= freq <= cfg.MANUAL_FREQ_MAX:
            raise ValueError("配方频率超出现行真实消费者输入范围")
        channel = {"throttle": 2.0 * (freq - cfg.MANUAL_FREQ_MIN) / (cfg.MANUAL_FREQ_MAX - cfg.MANUAL_FREQ_MIN) - 1.0,
                   "elevator": fixed["tail_elevator_rad"] / cfg.MANUAL_ELEV_GAIN,
                   "aileron": fixed["tail_aileron_rad"] / cfg.MANUAL_AIL_GAIN,
                   "rudder": fixed["tail_rudder_rad"] / cfg.MANUAL_RUDD_GAIN}
        # 首步和最后有效输入步持久化；退出异常时仍有未完成记录，不能当完整运行。
        if self.summary["samples"] == 1 or self._last_step >= self.expected:
            self.close()
        return channel

    def close(self):
        self.summary["complete"] = (self.summary["samples"] == self.expected
                                     and self._last_step == self.expected)
        try:
            write_json(self.path, self.summary)
        except OSError:
            # 运行器必需此文件；缺失不能得到资格通过。采集不另写物理状态。
            pass


def compare_adopted(expected, actual, allowed_paths=()):
    """完整递归比较，不取共同字段；只允许事前登记的路径发生改变。"""
    differences = []
    def visit(a, b, path):
        if path in allowed_paths:
            return
        if type(a) is not type(b):
            differences.append({"path": path, "expected": a, "actual": b})
        elif isinstance(a, dict):
            for key in sorted(set(a) | set(b)):
                child = path + "." + key if path else key
                if key not in a or key not in b:
                    differences.append({"path": child, "missing": "expected" if key not in a else "actual"})
                else:
                    visit(a[key], b[key], child)
        elif isinstance(a, list):
            if len(a) != len(b):
                differences.append({"path": path, "reason": "列表长度不一致"})
            else:
                for i, (x, y) in enumerate(zip(a, b)):
                    visit(x, y, "%s[%d]" % (path, i))
        elif a != b:
            differences.append({"path": path, "expected": a, "actual": b})
    visit(expected, actual, "")
    return differences
