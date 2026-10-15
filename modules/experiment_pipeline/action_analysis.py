"""固定动作配方的离线资格与测量。

只读实际事件及原值；API 提交量不是测力计，回调时钟不是物理时钟。
缺少时间/采集资格时保留诊断读数，正式冲量为 null。
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .action_recipe import canonical_hash, compare_adopted, read_json

ANALYSIS_VERSION = "action-experiment/1"


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _finite(value):
    if type(value) in (int, float):
        return math.isfinite(value)
    if type(value) is list:
        return all(_finite(v) for v in value)
    return False


def vector(value, width):
    # PhysX APIs return either (N,) or (1,N). Other shapes are not flattened.
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], list):
        value = value[0]
    if not isinstance(value, list) or len(value) != width or not _finite(value):
        raise ValueError("记录向量缺失、非有限或宽度不符")
    return value


def read_events(path, binary_path, expected_tag):
    """完整文件、序号、记录器绑定、身份、尾部与原二进制摘要联合核验。"""
    raw = Path(path).read_bytes()
    lines = raw.splitlines(keepends=True)
    if not lines or any(not x.endswith(b"\n") or not x.strip() for x in lines):
        raise ValueError("事件文件为空、包含空行或尾行未完成")
    rows = [json.loads(line) for line in lines]
    if len(rows) < 3 or rows[0].get("record_type") != "header" or rows[-1].get("record_type") != "tail":
        raise ValueError("事件文件缺完整头尾")
    header, tail = rows[0], rows[-1]
    if not header.get("binding_id"):
        raise ValueError("事件记录器绑定缺失")
    for index, row in enumerate(rows):
        if row.get("schema") != "i210.actual_events/1" or row.get("seq") != index or type(row.get("seq")) is not int:
            raise ValueError("事件格式或连续序号不符")
        if row.get("binding_id") != header["binding_id"]:
            raise ValueError("事件记录器绑定改变")
    for row in (header, tail):
        if row.get("identity_missing") != [] or row.get("identity_issues") != []:
            raise ValueError("事件身份不完整或冲突")
        if row.get("execution_identity", {}).get("I186_RUN_ID") != expected_tag:
            raise ValueError("事件不是当前实际运行")
    if header.get("meta", {}).get("tag") != expected_tag:
        raise ValueError("事件标签不符")
    if tail.get("prefix_sha256") != hashlib.sha256(b"".join(lines[:-1])).hexdigest():
        raise ValueError("事件原文摘要不符")
    if tail.get("bin_sha256") != file_hash(binary_path) or tail.get("bin_size_bytes") != Path(binary_path).stat().st_size:
        raise ValueError("事件与原值台账不对应")
    if tail.get("event_count") != len(rows) - 2 or tail.get("binary_closed") is not True or tail.get("valid") is not True:
        raise ValueError("事件尾部计数或完整性失败")
    if any(tail.get(k) != 0 for k in ("invalid_event_count", "serialization_issue_count", "io_error_count")):
        raise ValueError("事件含记录错误")
    for row in rows[1:-1]:
        if row.get("record_type") != "event" or row.get("valid") is not True or row.get("serialization_issues") != []:
            raise ValueError("事件记录无效")
        if row.get("kind") not in ("drive", "aero_apply") or not isinstance(row.get("payload"), dict):
            raise ValueError("事件种类或载荷不符")
    from modules.fw_iid_ledger import _anatomy
    _, fw_header, _, _, _, _ = _anatomy(binary_path)
    if fw_header.get("tag") != expected_tag or header.get("joint_names") != fw_header.get("joint_names") or header.get("phys_dt") != fw_header.get("phys_dt"):
        raise ValueError("事件与台账关节名或步长不符")
    # Bind the recorder's original header bytes, not a reserialized dictionary.
    import struct
    with open(binary_path, "rb") as fh:
        prefix = fh.read(12)
        hlen = struct.unpack("<I", prefix[8:12])[0]
        hraw = fh.read(hlen)
    if hashlib.sha256(hraw).hexdigest() != header.get("ledger_header_sha256"):
        raise ValueError("事件未绑定台账原始头")
    return header, rows[1:-1]


def native_solver_sample(event):
    """只接受明确名为 PhysX 的唯一活动模拟器；不拿整数时间戳当秒。"""
    clock = event.get("native_clock", {})
    if clock.get("schema") != "i210.native_clock/1" or clock.get("status") != "observed" or clock.get("query_errors") != []:
        raise ValueError("原生求解计步未取得有效读数")
    core = clock.get("physics_core", {})
    sims = core.get("simulations")
    if not isinstance(sims, list) or core.get("simulation_count") != len(sims):
        raise ValueError("原生模拟器清单不完整")
    active = [s for s in sims if s.get("active") is True]
    if len(active) != 1 or "physx" not in str(active[0].get("name", "")).lower():
        raise ValueError("不能唯一确认 PhysX 求解器归属")
    sim = active[0]
    for key in ("id", "step_count", "timestamp"):
        if type(sim.get(key)) is not int or sim[key] < 0:
            raise ValueError("原生求解器标识、计步或时间戳无效")
    return sim


def expected_conditions(case):
    f = case["fixed"]
    return {"aero_model": f["aero_model"], "experiment_mode": "recipe",
            "wind_enabled": False, "wind_world_m_per_s": f["wind_world_m_per_s"],
            "tether_enabled": False, "dp_height_hold": False, "controller_enabled": False,
            "body_free_axes": [], "bench_pitch_deg": 0.0,
            "dp_target_z_m": float(f["initial_position_world_m"][2]),
            "physics_dt_s": case["sampling"]["physics_dt_s"], "side_pulse_enabled": False}


def analyze_events(events, case, qualification, conditions):
    """按完整实际序列计算，始终区分当前读回和上一帧已提交目标。"""
    errors, clock_errors = [], []
    dt = case["sampling"]["physics_dt_s"]
    total = round((case["sampling"]["settle_s"] + case["sampling"]["measure_s"]) / dt) + 1
    start = round(case["sampling"]["settle_s"] / dt)
    tolerance = qualification["frequency_readback_tolerance_hz"]
    frequency = case["frequency_hz"]
    from modules.aero import MODEL_REGISTRY
    expected_model = MODEL_REGISTRY[case["fixed"]["aero_model"]]
    expected_model_type = expected_model.__module__ + "." + expected_model.__name__
    drives = [r for r in events if r["kind"] == "drive"]
    aero = [r for r in events if r["kind"] == "aero_apply"]
    if [r.get("clock", {}).get("main_step") for r in drives] != list(range(1, total)):
        errors.append("驱动观察不覆盖事前规定的全部输入步")
    if conditions.get("complete") is not True or conditions.get("errors") != []:
        errors.append("实际条件观察未完整结束或含错误")
    if conditions.get("samples") != total - 1 or conditions.get("expected_samples") != total - 1:
        errors.append("条件观察步数与事前计划不符")
    differences = compare_adopted(expected_conditions(case), conditions.get("fixed_adopted"))
    if differences:
        errors.append("实际固定条件与配方不符")
    if conditions.get("max_body_reset_error") != 0.0:
        errors.append("逐步重置后的实际机体状态未保持批准值")
    if conditions.get("world_physics_dt_s") != dt:
        errors.append("实际 World 步长与配方不符")
    parameters = conditions.get("effective_parameters")
    if not isinstance(parameters, dict) or canonical_hash(parameters) != conditions.get("effective_parameters_sha256"):
        errors.append("实际生效参数快照缺失或摘要不符")
    squared, maximum, count, previous, names = None, None, 0, None, None
    drive_solver, drive_times, error_trace = {}, [], []
    fixed_motion = None
    freq_max_error, consumed_max_error = 0.0, 0.0
    for row in drives:
        c, p = row.get("clock", {}), row["payload"]
        step = c.get("main_step")
        try:
            if type(step) is not int or c.get("main_t_s") != step * dt or c.get("phys_dt_s") != dt:
                raise ValueError("驱动观察的主时钟或步长不符")
            if p.get("selection") != "manual" or p.get("target_api_status") != "api_succeeded" or p.get("target_api_error") is not None:
                raise ValueError("批准输入分支未被实际采用或目标 API 失败")
            if "ap_out" not in p or p["ap_out"] is not None:
                raise ValueError("固定台架要求自动驾驶仪实际退出")
            channels = p.get("manual_channel")
            if not isinstance(channels, dict) or set(channels) != {"throttle", "elevator", "aileron", "rudder"}:
                raise ValueError("实际采用输入通道不完整")
            for key, field in (("elevator", "tail_elevator_rad"), ("aileron", "tail_aileron_rad"), ("rudder", "tail_rudder_rad")):
                gain_key = {"elevator": "MANUAL_ELEV_GAIN", "aileron": "MANUAL_AIL_GAIN", "rudder": "MANUAL_RUDD_GAIN"}[key]
                gain = parameters.get(gain_key) if isinstance(parameters, dict) else None
                if not _finite(gain) or not _finite(channels.get(key)) or channels[key] * gain != case["fixed"][field]:
                    raise ValueError("固定尾舵输入与实际参数不符")
            if p.get("query_errors") != {}:
                raise ValueError("实际关节读取失败")
            jnames = p.get("joint_names")
            if not isinstance(jnames, list) or not jnames or len(set(jnames)) != len(jnames) or not all(type(n) is str and n for n in jnames):
                raise ValueError("关节名称缺失或重复")
            if names is None:
                names = jnames
                squared, maximum = [0.0] * len(names), [0.0] * len(names)
            if names != jnames:
                raise ValueError("实际关节顺序发生变化")
            target = vector(p.get("target_api_float32_rad"), len(names))
            actual = vector(p.get("actual_readback", {}).get("joint_position_rad"), len(names))
            vector(p.get("actual_readback", {}).get("joint_velocity_rad_s"), len(names))
            for key in ("frequency_after_set_hz", "frequency_consumed_by_evaluate_hz"):
                if not _finite(p.get(key)):
                    raise ValueError("实际频率读回缺失")
            freq_max_error = max(freq_max_error, abs(p["frequency_after_set_hz"] - frequency))
            if step > start:
                adopted_motion = {}
                for side in ("L", "R"):
                    values = p.get("aero_wing_readback", {}).get(side, {})
                    adopted_motion[side] = {key: values.get(key) for key in ("amplitude", "pitch_lag_s", "stroke_gate")}
                    if not all(_finite(v) for v in adopted_motion[side].values()):
                        raise ValueError("气动运动学固定属性缺失")
                if fixed_motion is None:
                    fixed_motion = adopted_motion
                elif adopted_motion != fixed_motion:
                    raise ValueError("测量中幅值、扭转滞后或门控偷偷变化")
                consumed_max_error = max(consumed_max_error, abs(p["frequency_consumed_by_evaluate_hz"] - frequency))
                if abs(p["frequency_after_set_hz"] - frequency) > tolerance or abs(p["frequency_consumed_by_evaluate_hz"] - frequency) > tolerance:
                    raise ValueError("测量窗实际采用频率与批准输入不符")
                if previous is None or previous[0] != step - 1:
                    raise ValueError("跟踪误差缺少上帧目标，不拿本帧新目标比较当前读回")
                delta = [a - b for a, b in zip(actual, previous[1])]
                for i, value in enumerate(delta):
                    squared[i] += value * value
                    maximum[i] = max(maximum[i], abs(value))
                count += 1
                error_trace.append([step, delta])
            previous = step, target
        except (ValueError, TypeError) as exc:
            if len(errors) < 20:
                errors.append("驱动步 %s：%s" % (step, exc))
        try:
            sim = native_solver_sample(row)
            drive_solver[step] = sim
            drive_times.append((step, sim))
        except ValueError as exc:
            if not clock_errors:
                clock_errors.append(str(exc))
    for (a_step, a), (b_step, b) in zip(drive_times, drive_times[1:]):
        if (b_step != a_step + 1 or b["id"] != a["id"] or b["step_count"] != a["step_count"] + 1
                or b["timestamp"] != a["timestamp"] + 1):
            if not clock_errors:
                clock_errors.append("相邻主循环不对应同一 PhysX 求解器的一次原生推进")
    impulses = [0.0] * 6
    impulse_steps = set()
    callback_indices = []
    model_max = {"position_m": 0.0, "quaternion": 0.0, "air_velocity_m_s": 0.0, "angular_velocity_rad_s": 0.0}
    model_keys = (("position_world_m", case["fixed"]["initial_position_world_m"], "position_m"),
                  ("quaternion_wxyz", [1.0, 0.0, 0.0, 0.0], "quaternion"),
                  ("air_velocity_world_m_s", [0.0] * 3, "air_velocity_m_s"),
                  ("angular_velocity_world_rad_s", [0.0] * 3, "angular_velocity_rad_s"))
    for row in aero:
        c, p = row.get("clock", {}), row["payload"]
        ms = c.get("last_completed_main_step")
        index = c.get("callback_index")
        callback_indices.append(index)
        if type(ms) is not int or not start <= ms < total - 1:
            continue  # measurement interval: commands after steps start .. total-2
        try:
            if p.get("status") not in ("deferred", "api_succeeded"):
                raise ValueError("气动回调状态不适用")
            if p.get("body_read_error") is not None or p.get("tail_status") != "computed" or p.get("tail_error") is not None:
                raise ValueError("模型状态或尾翼求值有错误")
            if p.get("model_type") != expected_model_type:
                raise ValueError("实际气动模型类型与批准构型不符")
            if p.get("clipped") is not False:
                raise ValueError("作用量被限幅，不能形成未饱和单变量结论")
            mi = p.get("model_input", {})
            for key, target, metric in model_keys:
                actual = vector(mi.get(key), len(target))
                model_max[metric] = max(model_max[metric], max(abs(a - b) for a, b in zip(actual, target)))
            for side in ("L", "R"):
                v = p.get("wing_readback", {}).get(side, {}).get("frequency")
                if not _finite(v) or abs(v - frequency) > tolerance:
                    raise ValueError("气动消费者实际频率不符")
            if p.get("status") == "deferred":
                if p.get("force_api_status") != "not_attempted" or p.get("torque_api_status") != "not_attempted":
                    raise ValueError("暂存事件被误记为已施力")
                continue
            if p.get("force_api_status") != "api_succeeded" or p.get("torque_api_status") != "api_succeeded":
                raise ValueError("实际施力 API 未成功")
            force = vector(p.get("submitted_F_float32_N"), 3)
            torque = vector(p.get("submitted_T_float32_Nm"), 3)
            # Every successful API submission before next solver step contributes
            # F * actual World dt. Deferred values already merged by the producer
            # must not be counted twice. Callback dt is never the integration dt.
            sim = native_solver_sample(row)
            current = drive_solver.get(ms)
            following = drive_solver.get(ms + 1)
            if (current is None or following is None or sim["id"] != current["id"]
                    or sim["step_count"] != current["step_count"]
                    or sim["timestamp"] != current["timestamp"]
                    or following["step_count"] != sim["step_count"] + 1):
                raise ValueError("实际施力不能绑定到下一次唯一求解区间")
            for i, value in enumerate(force + torque):
                impulses[i] += value * dt
            impulse_steps.add(ms + 1)
        except (ValueError, TypeError) as exc:
            if len(errors) < 20:
                errors.append("气动回调 %s：%s" % (index, exc))
    if not aero or any(type(x) is not int for x in callback_indices) or any(b != a + 1 for a, b in zip(callback_indices, callback_indices[1:])):
        errors.append("气动回调序列缺失、重复或不连续")
    if any(model_max.values()):
        errors.append("气动模型实际看到的固定状态或来流与配方不符")
    # The extra final guard solve has no drive receipt and is excluded. The
    # interior therefore covers the entire preregistered measurement window.
    required_intervals = set(range(start + 1, total))
    if impulse_steps != required_intervals:
        errors.append("六分量施力未覆盖全部预定内部求解区间")
    if count != total - 1 - start:
        errors.append("跟踪误差未覆盖全部预定内部测量步")
    valid = not errors and not clock_errors
    rms = [math.sqrt(x / count) for x in squared] if count and squared else None
    limit = qualification["tracking_error_limit_rad"]
    tracking = "report_only" if limit is None else ("pass" if valid and maximum and max(maximum) <= limit else "fail")
    return {"data_valid": valid, "errors": errors, "clock_errors": clock_errors,
            "actual_condition_differences": differences,
            "model_fixed_state_max_error": model_max,
            "fixed_motion_readback": fixed_motion,
            "frequency_set_max_error_hz": freq_max_error,
            "frequency_consumed_measurement_max_error_hz": consumed_max_error,
            "tracking": {"status": tracking, "unit": "rad", "threshold": limit,
                         "joint_names": names, "samples": count, "rms_rad": rms,
                         "max_abs_rad": maximum, "error_trace_sha256": canonical_hash(error_trace),
                         "definition": "当前求解后关节位置减上一驱动步实际提交的 float32 目标"},
            "impulse": {"status": "valid" if valid else "invalid", "frame": "world",
                        "force_unit": "N*s", "torque_unit": "N*m*s",
                        "window_s": [start * dt, (total - 1) * dt],
                        "intervals": len(impulse_steps), "six_components": impulses if valid else None,
                        "definition": "完整成功施力 API float32 提交量按下一次唯一 PhysX 推进区间的 World dt 积分；非传感器实测"},
            "science_payload_sha256": canonical_hash([
                {"kind": r["kind"], "clock": r.get("clock"), "payload": r["payload"]} for r in events])}


def qualify_csv(path, case, columns, expected_tag):
    import csv
    meta = read_json(str(path) + ".meta.json")
    if meta.get("tags") != expected_tag or not isinstance(meta.get("description"), str) or not meta["description"].strip():
        raise ValueError("CSV 实际运行身份缺失或不符")
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows or rows[0] != columns or len(set(columns)) != len(columns):
        raise ValueError("CSV 必需完整字段与采集前冻结定义不符")
    dt = case["sampling"]["physics_dt_s"]
    samples = round((case["sampling"]["settle_s"] + case["sampling"]["measure_s"]) / dt)
    expected_steps = list(range(4, samples + 1, 4))
    if len(rows) - 1 != len(expected_steps):
        raise ValueError("CSV 长度不覆盖完整事前窗口")
    for step, row in zip(expected_steps, rows[1:]):
        if len(row) != len(columns):
            raise ValueError("CSV 含空行或缺字段行")
        values = dict(zip(columns, row))
        if float(values["t"]) != round(step * dt, 4):
            raise ValueError("CSV 时刻重复、缺步或与预定采样时基不符")
        phase = "settle" if step * dt <= case["sampling"]["settle_s"] else "measure"
        if values["phase"] != phase:
            raise ValueError("CSV 实验阶段与固定窗口不符")
        for key, value in values.items():
            if key.endswith("_json"):
                json.loads(value)
            elif key != "phase" and not math.isfinite(float(value)):
                raise ValueError("CSV 含非有限数")
    return {"status": "valid", "rows": len(expected_steps), "columns": len(columns),
            "schema_source": "plan.csv_columns from frozen producer declaration"}


def qualify_run(run, case, qualification, conditions_path, ledger_path, csv_columns):
    from modules import fw_iid_ledger as ledger
    dt = case["sampling"]["physics_dt_s"]
    steps = round((case["sampling"]["settle_s"] + case["sampling"]["measure_s"]) / dt) + 1
    manifest = {"expected_steps": steps, "phys_dt": dt, "phys_dt_tol": 0.0,
                "approved_misses": qualification["approved_misses"],
                "run_conditions": {"FLAPPER_TAG": run["tag"], "FLAPPER_LEDGER": "1", "FLAPPER_I210_EVIDENCE": "1"}}
    q = ledger.qualify(ledger_path, manifest)
    header, events = read_events(str(ledger_path) + ".i210.jsonl", ledger_path, run["tag"])
    conditions = read_json(conditions_path)
    if conditions.get("tag") != run["tag"] or conditions.get("case_sha256") != case["case_sha256"] or conditions.get("recipe_sha256") != case["recipe_sha256"]:
        raise ValueError("实际条件不属于当前冻结档位")
    result = analyze_events(events, case, qualification, conditions)
    _, raw = ledger.load(ledger_path)
    slices = ledger._chan_slices()
    raw_steps = {int(row[0]): row for row in raw}
    for event in events:
        if event["kind"] != "drive":
            continue
        step = event.get("clock", {}).get("main_step")
        payload = event["payload"]
        names = payload.get("joint_names")
        if names != header.get("joint_names") or step not in raw_steps:
            result["errors"].append("驱动观察与台账关节名或步号不对应")
            break
        for channel, key in (("jp", "joint_position_rad"), ("jv", "joint_velocity_rad_s")):
            begin, width = slices[channel]
            actual = vector(payload.get("actual_readback", {}).get(key), len(names))
            if actual[:width] != raw_steps[step][begin:begin + width].tolist():
                result["errors"].append("驱动侧实际读回与同一步原值台账不一致：%s/%s" % (step, channel))
                break
        if result["errors"] and result["errors"][-1].startswith("驱动侧实际读回"):
            break
    result["csv_qualification"] = qualify_csv(run["csv"], case, csv_columns, run["tag"])
    result["ledger_qualification"] = {k: v for k, v in q.items() if k != "generated_at"}
    result["data_valid"] = result["data_valid"] and not result["errors"] and q.get("status") == "valid" and run.get("milestone") is True
    if not result["data_valid"]:
        result["impulse"].update(status="invalid", six_components=None)
    result["effective_parameters_sha256"] = conditions.get("effective_parameters_sha256")
    result["actual_effective_parameters"] = conditions.get("effective_parameters")
    result["input_refs"] = {str(Path(p).resolve()): file_hash(p) for p in
                            (ledger_path, str(ledger_path) + ".i210.jsonl", conditions_path, run["csv"], str(run["csv"]) + ".meta.json", run["log"])}
    result["ledger_body_sha256"] = q.get("input_refs", {}).get("sha256_body")
    result["tag"] = run["tag"]
    return result
