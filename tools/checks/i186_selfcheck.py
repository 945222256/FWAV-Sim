# -*- coding: utf-8 -*-
"""i186_selfcheck.py — I-186 证据包自检（L1 返修 v2 补齐版）。

依据 Doro 当前核验结果（2026-10-14 21:42，五组实测差距）在既有全部控制
例之上新增覆盖：实际分析输入分列（测量科目须绑定本次数据，仅列标准阻
断）、实际执行环境一致（python 步隔离如实采集、必要条件未实际应用限制
资格、逐步环境见证）、批准必要清单来源解析（不存在、未批准、版本标记
缺失或片段缺失待审，配置与构包同核）、必需量逐项定义有效性（vphz 一类
列存在但定义与来源未知阻断，不猜语义）、核验独立重查整科齐备性（包卡
同删、改状态、增条目均不能维持通过）。真实生产链 v2：分析步实测取自本
次数据流并登记实际分析输入。既有整科构建保护、身份生成、生产格式读
取、版本读数与全部历史反例保留。

用法：python tools/checks/i186_selfcheck.py   （退出码零为全部通过）
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from modules import evidence_pack as EP
from modules import fw_iid_ledger as L
from modules.experiment_pipeline import scene_witness as SW

APPROVED_MANIFEST = EP.APPROVED_REQUIRED["file"]
APPROVED_AERO = "sane_dickinson"

# 人工接口结构链的实际场景事实（返修 v4：由数据生产侧见证落档，如实
# 声明无物理事件，不冒充飞行；批准场景只作对照不作来源）
_DEFAULT_WITNESS_FACTS = {
    "initial_state": {
        "initial_position_world_m": {
            "value": [0.0, 0.0, 3.0],
            "source": "记录脚本生成起始值（人工接口结构样例）"},
        "initial_orientation": {
            "value": {"quaternion_wxyz": [1.0, 0.0, 0.0, 0.0]},
            "source": "记录脚本生成起始姿态（同上）"},
        "initial_linear_velocity": {
            "value": [0.0, 0.0, 0.0],
            "source": "记录脚本起始线速度（同上）"},
        "initial_angular_velocity": {
            "value": [0.0, 0.0, 0.0],
            "source": "记录脚本起始角速度（同上）"},
        "initialization_and_release_boundary": {
            "event": "pin_then_release",
            "startup_pin_s": 0.9,
            "release_actions": ["清空气动滤波状态", "清空控制器积分状态"],
            "source": "记录脚本按冻结场景钉放口径生成数据（钉住段钉于"
                      "DP_TARGET_Z 与零欧拉角，释放后自起始值继续；"
                      "collection_mode=producer_declared，无物理系统）"},
    },
    "inflow": {
        "ambient_flow_world_m_per_s": {
            "value": [0.0, 0.0, 0.0],
            "source": "记录脚本生成过程未注入来流"},
        "perturbation_policy": {
            "value": "无扰动注入", "injected": False,
            "source": "记录脚本生成过程未注入扰动"},
    },
    "physical_constraints": {
        "body_free_dofs": {
            "value": {"tx": True, "ty": True, "tz": True,
                      "rx": True, "ry": True, "rz": True},
            "source": "生成数据为释放后自由飞行形态（六自由度无锁定；"
                      "collection_mode=producer_declared）"},
        "pin_release_schedule": {
            "event": "pin_then_release",
            "startup_pin_s": 0.9,
            "source": "记录脚本按冻结场景钉放口径生成（同"
                      "initialization_and_release_boundary）"},
        "tether_state": {"present": False, "source": "生成过程无系留对象"},
        "obstacle_state": {"present": False,
                           "source": "生成过程无障碍对象"},
    },
    "controller": {
        "effective_parameters": {
            "value": "未执行控制器（结构数据生成，H1/H2/H3 判定消费记录"
                     "数据不经控制器）",
            "source": "记录脚本生成逻辑（实际生产者自述）",
            "not_applicable_reason": "数据流驱动判定不消费控制器参数，"
                                     "该执行模式无控制器进程",
            "scope": "python 数据流驱动分析链"},
        "mode_and_reset_state": {
            "event": "none",
            "reason": "结构数据生成无控制器启动/复位事件；释放复位动作"
                      "在数据生成口径中等价为自起始值重启",
            "scope": "python 数据流驱动分析链",
            "source": "记录脚本生成逻辑（实际生产者自述）"},
    },
}

# 悬停 CSV 分支必需量全列表（批准清单 hover_csv quantities）
_CSV_COLS = ["t", "x", "y", "z", "vx", "vy", "vz", "wx", "wy", "wz",
             "qw", "qx", "qy", "qz", "freq", "Frz", "ail", "pitch_des",
             "roll", "pitch", "phase"]
_DEFAULT_CSV_LINE = (
    ",".join(_CSV_COLS) + "\n"
    "1.0,0,0,3.0,0,0,0,0,0,0,1.0,0,0,0,1.8,2.1523,0,0,0,0,collect\n")

RESULTS = []


def record(cid, desc, ok, detail=""):
    RESULTS.append({"id": cid, "desc": desc, "pass": bool(ok),
                    "detail": str(detail)[:260]})
    print("%s %s %s | %s" % ("过" if ok else "拒", cid, desc, detail))


def sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def wjson(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def _hover_criteria():
    with open(os.path.join(ROOT, "config", "bench_criteria.json"),
              encoding="utf-8") as f:
        return json.load(f)["subjects"]["hover"]["criteria"]


def make_run(tmp, subject="hover", run_id="run-A", baseline="base-1",
             ledger_phys_dt=None, ledger_run_id=None,
             scores_subject=None, scores_value=1.02, scores_unit="ratio",
             scores_threshold=0.9, scores_comparison=">=",
             scores_version="2026-09-30", scores_verdict="pass",
             scores_identity=True, scores_analyzed=True,
             conditions=True, max_s="0.0666", phys_dt_override=None,
             csv_line=_DEFAULT_CSV_LINE,
             extra_overrides=None, crit_kind="bench_criterion",
             crit_criterion="H2", scores_format="flat",
             producer_criteria=None, necessary=None, item_id="I-186-SELF",
             scene_mode="isaac", witness=True, witness_facts=None,
             aero_approved=True):
    """真实生产函数造隔离运行目录。

    缺省为单判据批准范围加科目层单标量结果（既有保护例共用）；正常整科
    例用 scores_format="producer"（bench_runner 逐条嵌套格式）加
    crit_kind="bench_subject"。producer_criteria 给出嵌套判据条目清单，
    缺省为悬停科三判据全齐的通过样例。scene_mode 缺省 isaac（起始快照
    与 isaac 执行子进程同一组装，参数节覆盖实际注入）；python 模式的
    隔离行为由 V 系列真实运行器例核验。necessary 缺省绑定 HQ 批准必要
    清单（返修 v3）；csv_line 缺省为悬停 CSV 分支必需量全列。
    """
    os.makedirs(tmp, exist_ok=True)
    if necessary is None and subject == "hover":
        necessary = {"source": APPROVED_MANIFEST}
    ov = {"FLAPPER_MAX_S": max_s, "FLAPPER_HEADLESS": "1"}
    if aero_approved:
        # 选用批准悬停场景的气动模型（结构样例的配置选择，非改采集真值；
        # 默认 sane_dickinson_3d 未获本场景批准，aero_approved=False 可
        # 复现默认不符反例）
        ov["FLAPPER_AERO_MODEL"] = APPROVED_AERO
    ov.update(extra_overrides or {})
    if phys_dt_override is not None:
        ov["FLAPPER_PHYS_DT"] = phys_dt_override
        ledger_phys_dt = ledger_phys_dt or float(phys_dt_override)
    ledger_phys_dt = ledger_phys_dt or (1.0 / 120.0)
    crit = {"kind": crit_kind, "subject": subject, "note": "I-186 自检"}
    if crit_kind == "bench_criterion":
        crit["criterion"] = crit_criterion
    config = {
        "scene": {"mode": scene_mode, "duration_s": float(max_s),
                  "argv": ["mock"]},
        "overrides": ov, "tags": {"name": run_id, "desc": "I-186 自检（人"
                               "工接口结构样例，不作真实飞行证据）"},
        "criteria": crit,
        "locks": {"standard": True},
    }
    if necessary is not None:
        config["necessary_conditions"] = necessary
    if conditions:
        EP.capture_conditions(tmp, config)
    steps = int(round(float(max_s) / (1.0 / 120.0)))
    ledger_path = os.path.join(tmp, "fw_ledger_%s.bin" % run_id)
    rec = L.LedgerRecorder(ledger_path, [f"j{i}" for i in range(9)],
                           ledger_phys_dt,
                           meta={"tag": "i186", "run_id":
                                 ledger_run_id or run_id, "env": ov})
    for s in range(1, steps + 1):
        rec.record(s, {n: [float(s)] * c for n, c in L.CHAN_LAYOUT})
    rec.close()
    if csv_line is not None:
        with open(os.path.join(tmp, "ident_stream_self.csv"), "w",
                  encoding="utf-8") as f:
            f.write(csv_line)
    if witness:
        SW.write_witness(tmp, "exp-self-1", run_id,
                         witness_facts if witness_facts is not None
                         else _DEFAULT_WITNESS_FACTS)
    wjson(os.path.join(tmp, "run_record.json"), {
        "item": item_id, "experiment_id": "exp-self-1",
        "run_id": run_id, "baseline_id": baseline, "config": config,
        "mode": scene_mode,
        "config_file_sha256": hashlib.sha256(
            json.dumps(config, sort_keys=True).encode("utf-8")).hexdigest(),
        "exit_code": 0, "timed_out": False,
        "completeness": {"complete": True, "rule": "自检收尾规则"},
        "steps": [{"name": "main", "mode": scene_mode, "complete": True,
                   "env_witness": {
                       "mode": scene_mode,
                       "interpreter": sys.executable,
                       "env_final": (
                           dict(ov, FLAPPER_TAG=run_id)
                           if scene_mode == "isaac"
                           else {"PYTHONUTF8": "1"})}}]})
    if scores_format == "producer":
        hc = _hover_criteria()
        crit_ids = {c["id"] for c in hc}
        nested = []
        for c in (producer_criteria if producer_criteria is not None
                  else [c["id"] for c in hc]):
            if isinstance(c, str):
                base = next(b for b in hc if b["id"] == c)
                meas = {"H1": {"survive_s": 60.0},
                        "H2": {"lift_ratio": 1.02},
                        "H3": {"roll_max_rad": 0.1,
                               "pitch_max_rad": 0.1}}[c]
                nested.append({"id": c, "status": "pass",
                               "measured": meas,
                               "threshold": base["threshold"]})
            else:
                nested.append(c)
        scores = {"schema_version": 1, "item": item_id,
                  "criteria_file": "config/bench_criteria.json",
                  "criteria_frozen_at": "2026-09-30",
                  "subjects": [{"key": scores_subject or subject,
                                "verdict": "pass", "criteria": nested}]}
    else:
        scores = {"schema_version": 1, "item": item_id,
                  "criteria_file": "config/bench_criteria.json",
                  "subjects": [{"key": scores_subject or subject,
                                "verdict": scores_verdict,
                                "value": scores_value, "unit": scores_unit,
                                "threshold": scores_threshold,
                                "comparison": scores_comparison,
                                "criterion_version": scores_version,
                                "standard_source":
                                    "config/bench_criteria.json#hover/H2",
                                "standard_threshold_key":
                                    "lift_ratio_min"}]}
    if scores_identity:
        scores["experiment_id"] = "exp-self-1"
        scores["run_id"] = run_id
    if scores_analyzed:
        inputs = [{"file": "config/bench_criteria.json",
                   "sha256": sha(os.path.join(ROOT, "config",
                                             "bench_criteria.json")),
                   "role": "standard_definition"}]
        inputs.append({"file": os.path.basename(ledger_path),
                       "sha256": sha(ledger_path)})
        csv_path = os.path.join(tmp, "ident_stream_self.csv")
        if os.path.exists(csv_path):
            inputs.append({"file": "ident_stream_self.csv",
                           "sha256": sha(csv_path)})
        scores["analyzed_inputs"] = inputs
    wjson(os.path.join(tmp, "self_scores.json"), scores)
    return tmp


def check_fact_contract():
    """直接改变原始事实，防止空外层与采集异常被误判为完整。"""
    import copy

    for cid, section, key, replacement in (
        ("R1", "initial_state", "initial_position_world_m", {}),
        ("R2", "controller", "effective_parameters", {}),
        ("R3", "controller", "effective_parameters",
         {"value": {"query_error": "autopilot absent"}, "source": "实际查询"}),
        ("R4", "controller", "effective_parameters",
         {"value": {"gain": 1.0, "filter": {"query_error": "read failed"}},
          "source": "实际查询"}),
        ("R5", "controller", "effective_parameters",
         {"value": {"filter": None}, "source": "实际查询"}),
        ("R6", "initial_state", "initialization_and_release_boundary",
         {"event": "pin_then_release", "source": "实际分支",
          "actions": {"pin": {"status": "failed", "query_error": "write failed"}}}),
        ("R7", "initial_state", "initialization_and_release_boundary",
         {"event": "pin_then_release", "source": "实际分支",
          "actions": {"reset": {"status": "not_executed"}}}),
        ("R8", "initial_state", "initial_orientation",
         {"value": {"quaternion_wxyz": [0.0] * 4}, "source": "实际读数"}),
    ):
        facts = copy.deepcopy(_DEFAULT_WITNESS_FACTS)
        facts[section][key] = replacement
        errors = EP._fact_field_problems(facts)
        record(cid, "原始事实无效必须点名：%s.%s" % (section, key),
               any(section + "." + key in err for err in errors), errors[:2])
    facts = copy.deepcopy(_DEFAULT_WITNESS_FACTS)
    facts["controller"]["effective_parameters"] = {
        "value": {"gain": 0.0, "filter": None}, "source": "实际查询"}
    record("R9", "实际零参数与未启动的可空滤波状态保留合法",
           not EP._fact_field_problems(facts))


def check_runtime_contract(base):
    """复用真实采集与构包入口；桩仅供读数，不执行物理仿真。"""
    import copy
    from types import SimpleNamespace as NS
    from unittest.mock import patch
    import numpy as np
    from modules.experiment_pipeline import runtime_observer as RO

    class Body:
        position = [0.0, 0.0, 3.0]
        orientation = [1.0, 0.0, 0.0, 0.0]
        velocity = [0.0] * 6

        def get_world_poses(self):
            return self.position, self.orientation

        def get_velocities(self):
            return self.velocity

    body = Body()
    ctl = NS(_ei=0.0, _freq_last=1.55, _pv_filt=np.zeros(3), f0=1.55)
    cfg = NS(WIND_ENABLED=False, WIND_VECTOR=(-5.0, 0.0, 0.0),
             TETHER_ENABLED=False, EXPERIMENT_MODE="",
             FlightControllerConfig=NS(FREQ_HOVER=1.55, PITCH_KP=0.0))
    manager = NS(_buoys=[], _dynamic_obstacles=[])
    context = {"_SIDE_PULSE_ON": False, "_SIDE_PULSE_T": 5.0,
               "_SIDE_PULSE_DVY": 1.5,
               "_I186_TETHER_RESULT": {"selected": False, "status": "succeeded",
                                        "observed_at_sim_t": 0.9}}
    pin = {k: {"status": "succeeded"} for k in ("body_pin", "wing_pin")}
    actions = {k: {"status": "succeeded"} for k in RO._RELEASE_ACTIONS}
    for key in ("perturb_qw", "perturb_roll"):
        actions[key] = {"status": "not_applicable", "scope": "release",
                        "reason": "实际请求为零", "requested": 0.0}
    serial = 0

    def observe(*, controller=ctl, config=cfg, pc=context, obstacles=manager,
                pin_receipt=pin, release_receipt=actions, release_body=body):
        nonlocal serial
        serial += 1
        archive = os.path.join(base, "runtime_%02d" % serial)
        with patch.dict(os.environ, {RO.ENV_ARCHIVE: archive,
                                    RO.ENV_EXPERIMENT: "exp-self-1", RO.ENV_RUN: "run-A"}):
            RO.observe_initialization(body, controller, config, 0.008333,
                                     pin_result=pin_receipt, obstacle_manager=obstacles,
                                     runtime_context=pc)
            RO.observe_release(release_body, controller, config, 0.9, release_receipt,
                               obstacle_manager=obstacles, runtime_context=pc)
        with open(os.path.join(archive, SW.WITNESS_NAME), encoding="utf-8") as fh:
            return json.load(fh)

    class Released(Body):
        orientation = [0.9950041652780258, 0.0, 0.09983341664682815, 0.0]
        velocity = [3.95, 0.0, 0.1, 0.0, 0.2, 0.0]

    doc = observe(release_body=Released())
    facts = doc["facts"]
    record("R10", "风关闭保留配置负五米每秒，实际采用零风",
           facts["inflow"]["ambient_flow_world_m_per_s"]["value"] == [0.0] * 3
           and facts["inflow"]["ambient_flow_world_m_per_s"][
               "configured_flow_world_m_per_s"] == [-5.0, 0.0, 0.0])
    record("R11", "钉住和释放完整状态同时保留，数值及时间各自可追溯",
           facts["initial_state"]["initial_linear_velocity"]["value"] == [0.0] * 3
           and facts["initial_state"]["post_release_linear_velocity"]["value"] == [3.95, 0.0, 0.1]
           and facts["initial_state"]["post_release_angular_velocity"]["value"] == [0.0, 0.2, 0.0]
           and facts["initial_state"]["post_release_orientation"]["value"][
               "quaternion_wxyz"] == Released.orientation
           and len(doc["observations"]) == 2
           and doc["observations"][0]["facts"]["initial_state"][
               "initial_linear_velocity"]["value"] == [0.0] * 3
           and facts["initial_state"]["initialization_and_release_boundary"][
               "pin_started_at_sim_t"] == 0.008333)
    record("R12", "完整采集正控制：参数和状态分开、numpy可序列化、必要事实合法",
           not EP._fact_field_problems(facts, runtime_observed=True)
           and facts["controller"]["effective_parameters"]["value"]["parameters"]["PITCH_KP"] == 0.0
           and facts["controller"]["effective_parameters"]["value"]["state"]["_pv_filt"] == [0.0] * 3)
    record("R39", "位姿与速度逐量保存单位、坐标与采样时点",
           facts["initial_state"]["initial_orientation"]["frame"] == "body_to_world"
           and facts["initial_state"]["post_release_linear_velocity"]["unit"] == "m/s"
           and facts["initial_state"]["post_release_angular_velocity"]["unit"] == "rad/s"
           and facts["initial_state"]["post_release_orientation"]["observed_at_sim_t"] == 0.9)

    def rejected(cid, desc, **kw):
        d = observe(**kw)
        errors = EP._fact_field_problems(d["facts"], runtime_observed=True)
        record(cid, desc, bool(errors), errors[:2])
        return d

    rejected("R13", "控制器不存在：真实读失败阻断", controller=None)
    failed_pin = copy.deepcopy(pin)
    failed_pin["body_pin"] = {"status": "failed", "query_error": "pin write failed"}
    bad_pin_doc = rejected("R14", "钉住写失败不得生成已成功锁定事实", pin_receipt=failed_pin)
    record("R15", "钉住失败后释放成功仍保留失败，不伪造自由度",
           bad_pin_doc["facts"]["initial_state"]["initialization_and_release_boundary"]["status"] == "failed"
           and bad_pin_doc["facts"]["physical_constraints"]["body_free_dofs"]["value"] is None)
    failed_release = copy.deepcopy(actions)
    failed_release["wing_snapshot"] = {"status": "failed", "query_error": "joint write failed"}
    rejected("R16", "释放关节快照失败仍须阻断", release_receipt=failed_release)
    rejected("R17", "旧版固定动作名称列表不能作为成功回执", release_receipt=["积分清零"])
    rejected("R18", "缺系留执行回执不得以配置默认值补造", pc={"_SIDE_PULSE_ON": False})
    rejected("R19", "缺场景障碍注册表不得写成无障碍", obstacles=None)
    pulse = copy.deepcopy(context)
    pulse["_SIDE_PULSE_ON"] = True
    pulse_doc = rejected("R20", "已启用侧滑扰动但无执行回执保持未知", pc=pulse)
    record("R21", "未知扰动不写成明确未注入",
           pulse_doc["facts"]["inflow"]["perturbation_policy"]["injected"] is None)
    broken_cfg = copy.copy(cfg)
    broken_cfg.WIND_VECTOR = [None, 0.0, 0.0]
    rejected("R22", "来流读数非法仍保存缺口而不吞掉整份观察", config=broken_cfg)

    class Unreadable(Body):
        def get_world_poses(self):
            raise RuntimeError("release state read failed")

    rejected("R23", "释放状态读失败必须进入字段资格门禁", release_body=Unreadable())
    cfg_on = copy.copy(cfg)
    cfg_on.WIND_ENABLED = True
    nonzero = observe(config=cfg_on)
    record("R24", "风开启保存消费者采用的非零来流",
           nonzero["facts"]["inflow"]["ambient_flow_world_m_per_s"]["value"] == [-5.0, 0.0, 0.0])
    missing = copy.deepcopy(facts)
    for key in list(missing["initial_state"]):
        if key.startswith("post_release_"):
            del missing["initial_state"][key]
    record("R25", "运行期见证缺完整释放状态不能只用钉住状态过关",
           len(EP._fact_field_problems(missing, runtime_observed=True)) == 4)
    bad_dir = os.path.join(base, "write_error")
    with patch.dict(os.environ, {RO.ENV_ARCHIVE: bad_dir,
                                RO.ENV_EXPERIMENT: "exp-self-1", RO.ENV_RUN: "run-A"}), \
            patch.object(SW, "record_observation", side_effect=OSError("archive write failed")):
        RO.observe_initialization(body, ctl, cfg, 0.0)
    with open(os.path.join(bad_dir, "scene_witness_errors.jsonl"), encoding="utf-8") as fh:
        err = json.loads(fh.readline())
    record("R26", "写失败日志为合法JSON并保留故障", "archive write failed" in err["error"])

    # 一份隔离数据目录复用真实构包与核验，不为每次输入变化重抄源码快照。
    run_dir = make_run(os.path.join(base, "runtime_pack"), crit_kind="bench_subject",
                       scores_format="producer")
    path = os.path.join(run_dir, SW.WITNESS_NAME)
    wjson(path, doc)
    good = EP.build_pack(run_dir)
    verify = EP.verify_pack(os.path.join(run_dir, EP.PACK_NAME))
    record("R27", "运行期完整字段经构包核验可用（隔离桩，非飞行）",
           good["necessary_conditions"]["status"] == "complete"
           and good["experiment_verdict"]["status"] == "pass" and verify["pass"],
           good["necessary_conditions"].get("reason", ""))
    for cid, changed in (("R28", bad_pin_doc), ("R29", pulse_doc)):
        wjson(path, changed)
        pk = EP.build_pack(run_dir)
        vv = EP.verify_pack(os.path.join(run_dir, EP.PACK_NAME))
        record(cid, "真实采集缺口贯通构包与核验，不得实验通过",
               pk["necessary_conditions"]["status"] == "deficient"
               and pk["experiment_verdict"]["status"] != "pass"
               and vv.get("verdict") != "pass")
    for cid, section, key in (("R30", "initial_state", "initial_position_world_m"),
                              ("R31", "controller", "effective_parameters")):
        broken = copy.deepcopy(doc)
        broken["facts"][section][key] = {}
        wjson(path, broken)
        pk = EP.build_pack(run_dir)
        vv = EP.verify_pack(os.path.join(run_dir, EP.PACK_NAME))
        record(cid, "原始外层空对象贯通构包与核验仍阻断",
               pk["experiment_verdict"]["status"] != "pass" and vv.get("verdict") != "pass")


def check_production_receipts(base):
    """执行主程序原分支的AST；比较修改前后写调用，不装载Isaac或推进物理。"""
    import ast
    import contextlib
    import copy
    import io
    import math
    import subprocess
    from types import SimpleNamespace as NS
    from unittest.mock import patch
    import numpy as np
    import config
    from modules import experiment_pipeline
    from modules.experiment_pipeline import runtime_observer as RO
    from pathlib import Path

    before = subprocess.check_output(
        ["git", "show", "8fee64c:main_sim.py"], cwd=ROOT).decode("utf-8")
    after = Path(ROOT, "main_sim.py").read_text(encoding="utf-8")

    def compiled(source, target):
        tree = ast.parse(source)
        if target == "launch":
            node = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                        and ast.unparse(n.test) == "_SIM_T < _cs.STARTUP_PIN_S")
        else:
            node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == target)
        return compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                       "main_sim.py:isolated_" + target, "exec")

    class Body:
        def __init__(self, force_failure=False):
            self.pose = [0.0, 0.0, 3.0, 1.0, 0.0, 0.0, 0.0]
            self.velocity = [0.0] * 6
            self.writes = []
            self.force_failure = force_failure

        def get_world_poses(self):
            return self.pose[:3], self.pose[3:]

        def get_velocities(self):
            return self.velocity

        def set_velocities(self, value):
            self.writes.append(["velocity", np.asarray(value).tolist()])
            self.velocity = np.asarray(value).reshape(-1).tolist()

        def set_world_poses(self, positions, orientations):
            self.writes.append(["pose", np.asarray(positions).tolist(), np.asarray(orientations).tolist()])
            self.pose = np.asarray(positions).reshape(-1).tolist() + np.asarray(orientations).reshape(-1).tolist()

        def apply_forces_and_torques_at_pos(self, forces, is_global):
            self.writes.append(["force", np.asarray(forces).tolist(), is_global])
            if self.force_failure:
                raise RuntimeError("actual force call failed")

    cfg = NS(STARTUP_PIN_S=0.9, LAUNCH_VX=3.95, LAUNCH_PITCH=0.1,
             FlightControllerConfig=NS(FREQ_HOVER=1.55), WIND_ENABLED=False,
             WIND_VECTOR=(-5.0, 0.0, 0.0), TETHER_ENABLED=False,
             EXPERIMENT_MODE="", DP_HEIGHT_HOLD=False,
             TETHER_ANCHOR=(0.0, 0.0, 0.0), TETHER_LENGTH=1.0,
             TETHER_K=2.0, TETHER_C=0.0)
    manager = NS(_buoys=[], _dynamic_obstacles=[])
    no_observer = NS(observe_initialization=lambda *a, **k: None,
                     observe_release=lambda *a, **k: None)

    def launch(source, name, observe):
        body, ap, aero = Body(), NS(), NS()
        ns = {"_SIM_T": 0.008333, "_cs": cfg, "np": np, "math": math, "os": os,
              "mav_view": body, "autopilot": ap, "aero_callback": aero,
              "step_count": 1, "art": None, "_wk": None,
              "obstacle_manager": NS(get_manager=lambda: manager),
              "_SIDE_PULSE_ON": False,
              "_I186_TETHER_RESULT": {"status": "succeeded", "selected": False},
              "flapper_loader": NS(get_loader=lambda: NS(get_pose=lambda: body.pose)),
              "_euler_to_quat": lambda r, p, y: [math.cos(p / 2), 0.0, math.sin(p / 2), 0.0]}
        calls = {"pin": 0}

        def pin_body(view):
            calls["pin"] += 1
            body.writes.append(["body_pin"])
            if name == "pin_failed" or (name == "late_pin_failed" and calls["pin"] == 2):
                raise RuntimeError("pin setter failed")

        def pin_wing(*args):
            body.writes.append(["wing_pin", ns["_SIM_T"]])
            if name == "release_failed" and ns["_SIM_T"] >= 0.9:
                raise RuntimeError("release joint setter failed")

        ns.update(_bench_pin=pin_body, _pin_wing_joints=pin_wing)
        archive = os.path.join(base, "production_" + name + ("_new" if observe else "_old"))
        with patch.dict(os.environ, {RO.ENV_ARCHIVE: archive,
                                    RO.ENV_EXPERIMENT: "exp-self-1", RO.ENV_RUN: "run-A",
                                    "FLAPPER_PERTURB_QW": "0.001", "FLAPPER_PERTURB_ROLL": "0.002"}), \
                patch.object(experiment_pipeline, "runtime_observer", RO if observe else no_observer), \
                contextlib.redirect_stdout(io.StringIO()):
            code = compiled(source, "launch")
            exec(code, ns)
            if name == "late_pin_failed":
                ns["_SIM_T"] = 0.016666
                exec(code, ns)
            ns["_SIM_T"] = 0.9
            exec(code, ns)
        doc = None
        if observe:
            with open(os.path.join(archive, SW.WITNESS_NAME), encoding="utf-8") as fh:
                doc = json.load(fh)
        return [body.writes, vars(ap), vars(aero)], doc

    for number, name in enumerate(("normal", "pin_failed", "late_pin_failed", "release_failed"), 32):
        old_writes, _ = launch(before, name, False)
        new_writes, doc = launch(after, name, True)
        event = doc["facts"]["initial_state"]["initialization_and_release_boundary"]
        expected = "succeeded" if name == "normal" else "failed"
        record("R%d" % number, "主程序实际钉放分支：%s，原写调用保持且回执符合结果" % name,
               old_writes == new_writes and event["status"] == expected,
               "动作状态=%s 原写调用一致=%s" % (event["status"], old_writes == new_writes))

    for number, enabled, fail in ((36, False, False), (37, True, False), (38, True, True)):
        results = []
        for source in (before, after):
            body = Body(force_failure=fail)
            ns = {"np": np, "os": os, "LOG_DIR": base, "_SIM_T": 0.9,
                  "_DP_STATE": {}, "logger": NS(debug=lambda *args: None)}
            selected_cfg = copy.copy(cfg)
            selected_cfg.TETHER_ENABLED = enabled
            with patch.object(config, "sim_config", selected_cfg):
                exec(compiled(source, "_apply_tether_and_dp"), ns)
                ns["_apply_tether_and_dp"](body, NS(get_pose=lambda: body.pose,
                                                   get_velocity=lambda: body.velocity))
            results.append((body.writes, ns["_DP_STATE"], ns.get("_I186_TETHER_RESULT")))
        receipt = results[1][2]
        record("R%d" % number, "实际系留消费者：启用=%s，施力失败=%s" % (enabled, fail),
               results[0][:2] == results[1][:2] and receipt["selected"] is enabled
               and receipt["status"] == ("failed" if fail else "succeeded"), receipt)


def main():
    check_fact_contract()
    workroot = os.path.join(ROOT, "logs", "i186_l1_typed_wiring_20261015",
                            "tmp")
    os.makedirs(workroot, exist_ok=True)
    base = tempfile.mkdtemp(prefix="i186_v2_", dir=workroot)
    try:
        check_runtime_contract(base)
        check_production_receipts(base)
        # N1 正常例（生产格式整科）：逐条嵌套结果三判据齐、绑定齐、条件
        # 子进程生效 → pass
        d1 = make_run(os.path.join(base, "n1"), crit_kind="bench_subject",
                      scores_format="producer")
        p1 = EP.build_pack(d1)
        v1 = EP.verify_pack(os.path.join(d1, EP.PACK_NAME))
        c1 = next(c for c in p1["criteria"]
                  if c.get("criterion_id") == "H2")
        ok1 = (p1["experiment_verdict"]["status"] == "pass"
               and v1["pass"] and p1["conditions_status"] == "captured"
               and p1["run_conditions"]["snapshot_mode"] == "child_effective"
               and c1["value"] == 1.02 and c1["unit"] == "dimensionless"
               and c1["threshold"] == 0.9 and c1["comparison"] == ">="
               and c1["criterion_version"] == "2026-09-30"
               and c1["input_source"]["sha256"] == sha(
                   os.path.join(d1, "self_scores.json")))
        record("N1", "正常例（生产嵌套格式整科）：三判据齐绑定齐条件生"
                "效，pass", ok1, "判定=%s 条件=%s" % (
                    p1["experiment_verdict"]["status"],
                    p1["conditions_status"]))

        # N2 身份链保留
        idn = p1["identity"]
        record("N2", "身份链保留：实验、运行、基准标识在包内",
               idn["experiment_id"]["value"] == "exp-self-1"
               and idn["run_id"]["value"] == "run-A"
               and idn["baseline_id"]["value"] == "base-1",
               "exp=%s run=%s" % (idn["experiment_id"]["value"],
                                  idn["run_id"]["value"]))

        # N16 同科目外来结果（身份与分析输入哈希全部来自其他运行）
        d16 = make_run(os.path.join(base, "n16"))
        sp = os.path.join(d16, "self_scores.json")
        doc = json.load(open(sp, encoding="utf-8"))
        doc["experiment_id"] = "exp-OTHER"
        doc["run_id"] = "run-OTHER"
        doc["analyzed_inputs"] = [{"file": "x.bin",
                                   "sha256": "0" * 64}]
        wjson(sp, doc)
        p16 = EP.build_pack(d16)
        ok16 = (p16["criteria"][0]["status"] != "pass"
                and "绑定失败" in p16["criteria"][0]["reason"]
                and p16["experiment_verdict"]["status"] != "pass")
        record("N16", "同科目外来结果：身份与分析输入哈希错配阻断",
               ok16, p16["criteria"][0]["reason"][:90])

        # N17 科学字段缺失（删单位与判据版本）
        d17 = make_run(os.path.join(base, "n17"), scores_unit=None,
                       scores_version=None)
        p17 = EP.build_pack(d17)
        ok17 = (p17["criteria"][0]["status"] != "pass"
                and "科学字段缺失" in p17["criteria"][0]["reason"])
        record("N17", "缺单位与判据版本：必要字段缺失不得通过", ok17,
               p17["criteria"][0]["reason"][:80])

        # N18 值阈值矛盾（0.9 对 0.5 小于等于却写通过）
        d18 = make_run(os.path.join(base, "n18"), scores_value=0.5)
        p18 = EP.build_pack(d18)
        ok18 = (p18["criteria"][0]["status"] == "fail"
                and "矛盾" in p18["criteria"][0]["reason"]
                and p18["experiment_verdict"]["status"] != "pass")
        record("N18", "值 0.9 阈值 0.5 规则小于等于却写通过：判 fail",
               ok18, p18["criteria"][0]["reason"][:90])

        # N19 非有限值（NaN）
        d19 = make_run(os.path.join(base, "n19"),
                       scores_value=float("nan"))
        p19 = EP.build_pack(d19)
        ok19 = (p19["criteria"][0]["status"] == "unknown"
                and p19["criteria"][0]["value"] is None
                and "非有限" in p19["criteria"][0]["reason"])
        record("N19", "非有限值：记 null 判未知不得正式通过", ok19,
               p19["criteria"][0]["reason"][:80])

        # N20 实验阶段不冒充拍相位（CSV phase=collect）
        qm = p1["quantity_meanings"]["actual_mapping"]
        ok20 = (qm["experiment_phase_status"] == "present"
                and qm["flap_phase_status"] == "missing"
                and "实验阶段" in qm["flap_phase_note"])
        record("N20", "phase 列为实验阶段：拍相位缺失如实限制", ok20,
               "实验阶段=%s 拍相位=%s" % (qm["experiment_phase_status"],
                                           qm["flap_phase_status"]))

        # N21 子进程生效条件（FLAPPER_PHYS_DT=0.01）
        d21 = make_run(os.path.join(base, "n21"),
                       phys_dt_override="0.01")
        p21 = EP.build_pack(d21)
        snap_dt = p21["run_conditions"]["snapshot_mode"]
        ok21 = (p21["run_conditions"]["snapshot_mode"] ==
                "child_effective"
                and json.load(open(os.path.join(
                    d21, EP.CONDITIONS_NAME), encoding="utf-8"))
                ["effective_config"]["phys_dt"] == 0.01)
        record("N21", "注入步长 0.01：子进程快照读到 0.01 非父默认",
               ok21, "mode=%s" % snap_dt)
        # 父进程默认快照不得冒充（伪造 parent_default 快照）
        d21b = make_run(os.path.join(base, "n21b"))
        cp = os.path.join(d21b, EP.CONDITIONS_NAME)
        cond = json.load(open(cp, encoding="utf-8"))
        cond["effective_config"]["snapshot_mode"] = "parent_default"
        wjson(cp, cond)
        p21b = EP.build_pack(d21b)
        ok21b = (p21b["conditions_status"] == "parent_default"
                 and p21b["experiment_verdict"]["status"] != "pass")
        record("N21b", "父进程默认快照冒充：条件非生效值阻断 pass",
               ok21b, "条件=%s 判定=%s"
                      % (p21b["conditions_status"],
                         p21b["experiment_verdict"]["status"]))

        # 返修二保留控制例（回归）
        d3 = make_run(os.path.join(base, "n3"), subject="hover",
                      scores_subject="turn", crit_kind="bench_subject")
        p3 = EP.build_pack(d3)
        record("N3", "外来科目：不作本次证据", p3["criteria"][0][
            "status"] == "not_executed"
            and p3["experiment_verdict"]["status"] != "pass",
            p3["criteria"][0]["reason"][:60])

        d4 = make_run(os.path.join(base, "n4"), ledger_run_id="run-OTHER")
        p4 = EP.build_pack(d4)
        record("N4", "外来台账：不绑定资格非 valid",
               p4["data_qualification"]["status"] != "valid",
               p4["data_qualification"]["status"])

        d5 = make_run(os.path.join(base, "n5"), ledger_phys_dt=0.01)
        p5 = EP.build_pack(d5)
        record("N5", "台账步长 0.01 对条件清单：invalid",
               p5["data_qualification"]["status"] == "invalid",
               p5["data_qualification"]["status"])

        d6 = make_run(os.path.join(base, "n6"), conditions=False)
        p6 = EP.build_pack(d6)
        record("N6", "无起始采集：非 pass",
               p6["conditions_status"] == "missing"
               and p6["experiment_verdict"]["status"] != "pass",
               p6["conditions_status"])

        d8 = make_run(os.path.join(base, "n8"))
        EP.build_pack(d8)
        sp8 = os.path.join(d8, "self_scores.json")
        doc8 = json.load(open(sp8, encoding="utf-8"))
        doc8["subjects"][0]["verdict"] = "fail"
        wjson(sp8, doc8)
        v8 = EP.verify_pack(os.path.join(d8, EP.PACK_NAME))
        record("N8", "结果篡改：核验查出", not v8["pass"],
               "；".join(v8["problems"])[:80])

        d9 = make_run(os.path.join(base, "n9"))
        EP.build_pack(d9)
        lp9 = os.path.join(d9, "fw_ledger_run-A.bin")
        raw = open(lp9, "rb").read()
        with open(lp9, "r+b") as f:
            f.seek(len(raw) - 8)
            f.write(b"\x01\x00\x00\x00\x00\x00\x00\x00")
        v9 = EP.verify_pack(os.path.join(d9, EP.PACK_NAME))
        record("N9", "台账篡改：核验查出", not v9["pass"],
               "；".join(v9["problems"])[:80])

        d10 = make_run(os.path.join(base, "n10"))
        EP.build_pack(d10)
        cardp = os.path.join(d10, EP.CARD_NAME)
        card = json.load(open(cardp, encoding="utf-8"))
        card["experiment_id"] = {"value": "exp-OTHER", "reason": None}
        wjson(cardp, card)
        v10 = EP.verify_pack(os.path.join(d10, EP.PACK_NAME))
        record("N10", "卡片身份更换：核验查出", not v10["pass"],
               "；".join(v10["problems"])[:80])

        d11 = make_run(os.path.join(base, "n11"))
        EP.build_pack(d11)
        EP.build_pack(d11)
        with open(os.path.join(d11, EP.CARD_NAME + ".prev"), "rb") as f:
            old_card = f.read()
        with open(os.path.join(d11, EP.CARD_NAME), "wb") as f:
            f.write(old_card)
        v11 = EP.verify_pack(os.path.join(d11, EP.PACK_NAME))
        record("N11", "混代：发布代号查出",
               not v11["pass"] and any("发布代号" in q
                                       for q in v11["problems"]),
               "；".join(v11["problems"])[:80])

        d12 = make_run(os.path.join(base, "n12"))
        EP.build_pack(d12)
        pack_before = open(os.path.join(d12, EP.PACK_NAME), "rb").read()
        card_before = open(os.path.join(d12, EP.CARD_NAME), "rb").read()
        real_replace = os.replace
        calls = {"n": 0}

        def flaky_replace(a, b):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("模拟第二输出写失败")
            return real_replace(a, b)
        EP.os.replace = flaky_replace
        raised = False
        try:
            EP.build_pack(d12)
        except Exception:
            raised = True
        finally:
            EP.os.replace = real_replace
        consistent = (open(os.path.join(d12, EP.PACK_NAME), "rb").read()
                      == pack_before
                      and open(os.path.join(d12, EP.CARD_NAME),
                               "rb").read() == card_before)
        v12 = EP.verify_pack(os.path.join(d12, EP.PACK_NAME))
        record("N12", "成对发布失败回滚一致", raised and consistent
               and v12["pass"], "raised=%s 核验=%s" % (raised, v12["pass"]))

        d13 = os.path.join(base, "n13")
        os.makedirs(d13)
        shutil.copy("logs/experiments/817d550f1d5d_20261006_084653/"
                    "run_record.json", d13)
        p13 = EP.build_pack(d13)
        v13 = EP.verify_pack(os.path.join(d13, EP.PACK_NAME))
        record("N13", "旧档接入：条件缺失待核非 pass",
               p13["conditions_status"] == "missing"
               and p13["experiment_verdict"]["status"] != "pass"
               and v13["pass"], p13["experiment_verdict"]["status"])

        d14 = os.path.join(base, "n14")
        os.makedirs(d14)
        p14 = EP.build_pack(d14)
        record("N14", "空目录旧配置兼容：待核非 pass",
               p14["identity"]["legacy_mode"]
               and p14["experiment_verdict"]["status"] != "pass",
               p14["experiment_verdict"]["status"])

        d15 = make_run(os.path.join(base, "n15"),
                       csv_line="t,freq,dA,Fx\n1,1.8,0,0\n")
        p15 = EP.build_pack(d15)
        qm15 = p15["quantity_meanings"]["actual_mapping"]
        record("N15", "无 phase 列：实验阶段与拍相位均缺失如实",
               qm15["experiment_phase_status"] == "missing"
               and qm15["flap_phase_status"] == "missing",
               "实验=%s 拍=%s" % (qm15["experiment_phase_status"],
                                  qm15["flap_phase_status"]))
        # ── 返修七：方向与单位绑定批准测量语义 ──
        # T1 Doro 原输入：值 0.5 改用小于等于 0.9 自报通过 → 方向错配阻断
        dT1 = make_run(os.path.join(base, "t1"), scores_value=0.5,
                       scores_comparison="<=")
        pT1 = EP.build_pack(dT1)
        okT1 = (pT1["criteria"][0]["status"] != "pass"
                and "比较方向" in pT1["criteria"][0]["reason"])
        record("T1", "值 0.5 小于等于 0.9 反转方向：与批准不低于定义不"
                "符阻断", okT1, pT1["criteria"][0]["reason"][:90])

        # T2 单位错填 kg → 与无量纲比值定义不符阻断
        dT2 = make_run(os.path.join(base, "t2"), scores_unit="kg")
        pT2 = EP.build_pack(dT2)
        okT2 = (pT2["criteria"][0]["status"] != "pass"
                and "单位" in pT2["criteria"][0]["reason"])
        record("T2", "升力比单位错填 kg：与无量纲比值定义不符阻断", okT2,
               pT2["criteria"][0]["reason"][:90])

        # T3 批准等价单位表示（无量纲）→ 正常通过
        dT3 = make_run(os.path.join(base, "t3"), scores_unit="无量纲")
        pT3 = EP.build_pack(dT3)
        okT3 = (pT3["criteria"][0]["status"] == "pass"
                and pT3["experiment_verdict"]["status"] == "pass")
        record("T3", "批准等价单位 无量纲：正常通过", okT3,
               "判据=%s 判定=%s" % (pT3["criteria"][0]["status"],
                                      pT3["experiment_verdict"]["status"]))

        # T4 多被测量条目（H3 已转写为 bindings）：单判据声明加科目层单
        # 标量结果无法逐被测量对应 → 待审阻断
        dT4 = make_run(os.path.join(base, "t4"), crit_kind="bench_criterion",
                       crit_criterion="H3")
        spT4 = os.path.join(dT4, "self_scores.json")
        docT4 = json.load(open(spT4, encoding="utf-8"))
        docT4["subjects"][0]["standard_source"] = \
            "config/bench_criteria.json#hover/H3"
        docT4["subjects"][0]["standard_threshold_key"] = "roll_abs_max_rad"
        docT4["subjects"][0]["threshold"] = 0.55
        docT4["subjects"][0]["unit"] = "rad"
        docT4["subjects"][0]["comparison"] = "<="
        docT4["subjects"][0]["value"] = 0.1
        docT4.pop("criteria_file", None)
        wjson(spT4, docT4)
        pT4 = EP.build_pack(dT4)
        okT4 = (pT4["criteria"][0]["status"] == "unknown"
                and "个被测量" in pT4["criteria"][0]["reason"])
        record("T4", "H3 已转写两被测量：单标量结果无法逐被测量对应阻断",
               okT4, pT4["criteria"][0]["reason"][:80])

        # T4b 未转写条目（cruise/C1 无结构化定义）→ 待审不得通过
        dT4b = make_run(os.path.join(base, "t4b"), subject="cruise",
                        crit_kind="bench_criterion", crit_criterion="C1",
                        scores_subject="cruise")
        spT4b = os.path.join(dT4b, "self_scores.json")
        docT4b = json.load(open(spT4b, encoding="utf-8"))
        docT4b["subjects"][0]["standard_source"] = \
            "config/bench_criteria.json#cruise/C1"
        docT4b["subjects"][0]["standard_threshold_key"] = "survive_min_s"
        docT4b["subjects"][0]["threshold"] = 60.0
        docT4b["subjects"][0]["unit"] = "s"
        docT4b["subjects"][0]["comparison"] = ">="
        docT4b["subjects"][0]["value"] = 60.0
        docT4b.pop("criteria_file", None)
        wjson(spT4b, docT4b)
        pT4b = EP.build_pack(dT4b)
        okT4b = (pT4b["criteria"][0]["status"] == "unknown"
                 and "缺结构化测量定义" in pT4b["criteria"][0]["reason"])
        record("T4b", "未转写条目 cruise/C1：缺结构化定义待审不得通过",
               okT4b, pT4b["criteria"][0]["reason"][:80])

        # ── 返修六：标准来源真绑定反例 ──
        # S1 不存在的标准文件路径（Doro 原输入形态）→ 阻断
        dS1 = make_run(os.path.join(base, "s1"))
        spS1 = os.path.join(dS1, "self_scores.json")
        docS1 = json.load(open(spS1, encoding="utf-8"))
        docS1["subjects"][0]["standard_source"] =             "config/NO_SUCH_APPROVED_STANDARD_20261014.json#hover"
        docS1.pop("criteria_file", None)
        wjson(spS1, docS1)
        pS1 = EP.build_pack(dS1)
        okS1 = (pS1["criteria"][0]["status"] != "pass"
                and "标准文件不存在" in pS1["criteria"][0]["reason"])
        record("S1", "不存在的标准文件路径：阻断不得正式通过", okS1,
               pS1["criteria"][0]["reason"][:90])

        # S2 文件存在但科目无效 → 阻断
        dS2 = make_run(os.path.join(base, "s2"))
        spS2 = os.path.join(dS2, "self_scores.json")
        docS2 = json.load(open(spS2, encoding="utf-8"))
        docS2["subjects"][0]["standard_source"] =             "config/bench_criteria.json#no_such_subject/H9"
        docS2.pop("criteria_file", None)
        wjson(spS2, docS2)
        pS2 = EP.build_pack(dS2)
        okS2 = (pS2["criteria"][0]["status"] != "pass"
                and ("无科目" in pS2["criteria"][0]["reason"]
                     or "无判据条目" in pS2["criteria"][0]["reason"]))
        record("S2", "文件存在但科目无效：阻断", okS2,
               pS2["criteria"][0]["reason"][:90])

        # S3 阈值与标准不符（不另选阈值）→ 阻断
        dS3 = make_run(os.path.join(base, "s3"), scores_threshold=0.7)
        pS3 = EP.build_pack(dS3)
        okS3 = (pS3["criteria"][0]["status"] != "pass"
                and "与标准" in pS3["criteria"][0]["reason"])
        record("S3", "阈值与标准 lift_ratio_min 不符：阻断不另选", okS3,
               pS3["criteria"][0]["reason"][:90])

        # S4 版本不符 → 阻断
        dS4 = make_run(os.path.join(base, "s4"), scores_version="1999-01-01")
        pS4 = EP.build_pack(dS4)
        okS4 = (pS4["criteria"][0]["status"] != "pass"
                and "标准版本" in pS4["criteria"][0]["reason"])
        record("S4", "判据版本与冻结版本不符：阻断", okS4,
               pS4["criteria"][0]["reason"][:80])

        # S5 未声明阈值键（标准文本非空但无数字绑定）→ 阻断
        dS5 = make_run(os.path.join(base, "s5"))
        spS5 = os.path.join(dS5, "self_scores.json")
        docS5 = json.load(open(spS5, encoding="utf-8"))
        docS5["subjects"][0].pop("standard_threshold_key", None)
        docS5.pop("criteria_file", None)
        wjson(spS5, docS5)
        pS5 = EP.build_pack(dS5)
        okS5 = (pS5["criteria"][0]["status"] != "pass"
                and "阈值键" in pS5["criteria"][0]["reason"])
        record("S5", "未声明标准阈值键：数字判据未绑定阻断", okS5,
               pS5["criteria"][0]["reason"][:80])

        # S6 成包后标准文件内容变动 → 核验查出（隔离副本模拟）
        dS6 = make_run(os.path.join(base, "s6"))
        pS6 = EP.build_pack(dS6)
        # 用临时根无法直接改共享文件——改包内记录的标准摘要模拟变动
        packS6 = os.path.join(dS6, EP.PACK_NAME)
        docS6 = json.load(open(packS6, encoding="utf-8"))
        docS6["criteria"][0]["standard"]["sha256"] = "0" * 64
        wjson(packS6, docS6)
        vS6 = EP.verify_pack(packS6)
        okS6 = not vS6["pass"]
        record("S6", "引用标准源内容变动：核验重查摘要与阈值键", okS6,
               "；".join(vS6["problems"])[:90] or "未查出")

        # ── 返修五：本轮原输入反例 ──
        # Q1 面积为空且不预写缺项清单（Doro 原输入：只改 AREA 不写
        # required_missing）→ 构包必须从字段本身检出
        dQ1 = make_run(os.path.join(base, "q1"))
        cpQ1 = os.path.join(dQ1, EP.CONDITIONS_NAME)
        condQ1 = json.load(open(cpQ1, encoding="utf-8"))
        condQ1["effective_config"]["classes"]["TailAeroConfig"]["AREA"] = None
        wjson(cpQ1, condQ1)
        pQ1 = EP.build_pack(dQ1)
        okQ1 = (pQ1["data_qualification"]["status"] != "valid"
                and pQ1["experiment_verdict"]["status"] != "pass"
                and "AREA" in pQ1["data_qualification"]["reason"])
        record("Q1", "面积为空不预写清单：构包从字段本身检出阻断", okQ1,
               pQ1["data_qualification"]["reason"][:90])

        # Q2 全有效参数删除仅留 mode 与空清单 → 阻断
        dQ2 = make_run(os.path.join(base, "q2"))
        cpQ2 = os.path.join(dQ2, EP.CONDITIONS_NAME)
        condQ2 = json.load(open(cpQ2, encoding="utf-8"))
        condQ2["effective_config"] = {"snapshot_mode": "child_effective",
                                       "classes": {}, "errors": [],
                                       "required_missing": []}
        wjson(cpQ2, condQ2)
        pQ2 = EP.build_pack(dQ2)
        okQ2 = (pQ2["data_qualification"]["status"] != "valid"
                and "整表缺失" in pQ2["data_qualification"]["reason"])
        record("Q2", "有效参数整表删除仅留空清单：阻断", okQ2,
               pQ2["data_qualification"]["reason"][:80])

        # Q3 删除 CSV 后映射缺失阻断（无台账单用定义时不 pass）
        dQ3 = make_run(os.path.join(base, "q3"), csv_line=None)
        pQ3 = EP.build_pack(dQ3)
        okQ3 = (pQ3["quantity_meanings"]["actual_mapping"]["status"]
                == "present")
        # q3 仍有绑定台账 → 台账单用定义生效 present；再造一例无台账
        dQ3b = make_run(os.path.join(base, "q3b"), csv_line=None)
        for f in os.listdir(dQ3b):
            if f.startswith("fw_ledger_"):
                os.remove(os.path.join(dQ3b, f))
        pQ3b = EP.build_pack(dQ3b)
        okQ3b = (pQ3b["quantity_meanings"]["actual_mapping"]["status"]
                 == "missing"
                 and pQ3b["experiment_verdict"]["status"] != "pass")
        record("Q3", "台账单用有完整定义 present；无 CSV 无台账缺失阻断",
               okQ3 and okQ3b,
               "台账单用=%s 全缺=%s 判定=%s" % (
                   pQ3["quantity_meanings"]["actual_mapping"]["status"],
                   pQ3b["quantity_meanings"]["actual_mapping"]["status"],
                   pQ3b["experiment_verdict"]["status"]))

        # Q4 无标准来源 → 阻断；有来源分列记录
        dQ4 = make_run(os.path.join(base, "q4"))
        spQ4 = os.path.join(dQ4, "self_scores.json")
        docQ4 = json.load(open(spQ4, encoding="utf-8"))
        docQ4.pop("criteria_file", None)
        docQ4["subjects"][0].pop("standard_source", None)
        wjson(spQ4, docQ4)
        pQ4 = EP.build_pack(dQ4)
        okQ4 = (pQ4["criteria"][0]["status"] != "pass"
                and "未绑定" in pQ4["criteria"][0]["reason"])
        cN1 = next(c for c in p1["criteria"]
                   if c.get("criterion_id") == "H2")
        okQ4b = cN1.get("standard_source") == \
            "config/bench_criteria.json#hover/H2"
        record("Q4", "无批准标准来源阻断；正常例来源分列记录", okQ4 and okQ4b,
               "缺源=%s 正常源=%s" % (pQ4["criteria"][0]["status"],
                                     cN1.get("standard_source")))

        # Q5 条件篡改成包后核验重查（面积改空不写清单 → verify 查出）
        dQ5 = make_run(os.path.join(base, "q5"))
        EP.build_pack(dQ5)
        cpQ5 = os.path.join(dQ5, EP.CONDITIONS_NAME)
        condQ5 = json.load(open(cpQ5, encoding="utf-8"))
        condQ5["effective_config"]["classes"]["FlapKinematicsConfig"]["AMPLITUDE"] = None
        wjson(cpQ5, condQ5)
        vQ5 = EP.verify_pack(os.path.join(dQ5, EP.PACK_NAME))
        okQ5 = (not vQ5["pass"])
        record("Q5", "成包后条件参数被改空：核验重查字段本身", okQ5,
               "；".join(vQ5["problems"])[:90] or "未查出")

        # ── 返修四组：本轮七反例 ──
        # P1 结果缺实验与运行标识 → 绑定阻断非 pass
        dP1 = make_run(os.path.join(base, "p1"))
        spP1 = os.path.join(dP1, "self_scores.json")
        docP1 = json.load(open(spP1, encoding="utf-8"))
        del docP1["experiment_id"]
        del docP1["run_id"]
        wjson(spP1, docP1)
        pP1 = EP.build_pack(dP1)
        okP1 = (pP1["criteria"][0]["status"] != "pass"
                and "缺" in pP1["criteria"][0]["reason"])
        record("P1", "结果缺实验与运行标识：必需标识缺失阻断", okP1,
               pP1["criteria"][0]["reason"][:80])

        # P2 结果事项错配阻断；登记别名放行
        dP2 = make_run(os.path.join(base, "p2"))
        spP2 = os.path.join(dP2, "self_scores.json")
        docP2 = json.load(open(spP2, encoding="utf-8"))
        docP2["item"] = "I-999"
        wjson(spP2, docP2)
        pP2 = EP.build_pack(dP2)
        okP2a = (pP2["criteria"][0]["status"] != "pass"
                 and "事项标识" in pP2["criteria"][0]["reason"])
        rp2 = os.path.join(dP2, "run_record.json")
        recP2 = json.load(open(rp2, encoding="utf-8"))
        recP2["item_aliases"] = ["I-186-BENCH"]
        wjson(rp2, recP2)
        docP2["item"] = "I-186-BENCH"
        wjson(spP2, docP2)
        pP2b = EP.build_pack(dP2)
        okP2 = okP2a and pP2b["criteria"][0]["status"] == "pass"
        record("P2", "事项错配 I-999 阻断；登记别名 I-186-BENCH 放行",
               okP2, "错配=%s 别名=%s"
               % (pP2["criteria"][0]["status"],
                  pP2b["criteria"][0]["status"]))

        # P3 必要参数缺失（尾翼面积空）→ 条件缺陷资格非有效
        dP3 = make_run(os.path.join(base, "p3"))
        cpP3 = os.path.join(dP3, EP.CONDITIONS_NAME)
        condP3 = json.load(open(cpP3, encoding="utf-8"))
        condP3["effective_config"]["classes"]["TailAeroConfig"]["AREA"] = None
        condP3["effective_config"]["required_missing"] = \
            ["TailAeroConfig.AREA=None"]
        wjson(cpP3, condP3)
        pP3 = EP.build_pack(dP3)
        okP3 = (pP3["data_qualification"]["status"] != "valid"
                and pP3["experiment_verdict"]["status"] != "pass")
        record("P3", "必要参数面积为空：条件缺陷资格非有效", okP3,
               pP3["data_qualification"]["status"])

        # P4 实际运行器六锁注入不丢
        dP4 = make_run(os.path.join(base, "p4"))
        condP4 = json.load(open(os.path.join(dP4, EP.CONDITIONS_NAME),
                                encoding="utf-8"))
        from modules.backend.bench_runner import STANDARD_LOCK
        lock_keys = set(STANDARD_LOCK.keys())
        applied = set((condP4.get("applied_overrides") or {}).keys())
        okP4 = lock_keys <= applied
        record("P4", "真实运行器六锁全部在注入采集（%d 键）"
               % len(lock_keys), okP4,
               "缺：%s" % "、".join(sorted(lock_keys - applied))
               if not okP4 else "齐")

        # P5 生产桥最终生效（同输入下 30/1.25/attached_flow）
        dP5 = make_run(os.path.join(base, "p5"),
                       phys_dt_override=None,
                       extra_overrides={"FLAPPER_WAVE_AMP": "30",
                                        "FLAPPER_WAVE_FREQ": "1.25",
                                        "FLAPPER_AERO_MODEL":
                                        "attached_flow"})
        condP5 = json.load(open(os.path.join(dP5, EP.CONDITIONS_NAME),
                                encoding="utf-8"))
        ecP5 = condP5["effective_config"]["classes"]["FlapKinematicsConfig"]
        okP5 = (ecP5["AMPLITUDE"] == 30.0
                and ecP5["FREQUENCY"] == 1.25
                and condP5["effective_config"]["aero_model"]
                == "attached_flow")
        record("P5", "生产桥生效：拍幅 30 频率 1.25 模型 attached_flow",
               okP5, "%s/%s/%s" % (ecP5["AMPLITUDE"], ecP5["FREQUENCY"],
                                   condP5["effective_config"]["aero_model"]))

        # P6 科学元数据类型非法（unit=[] 版本={}）
        dP6 = make_run(os.path.join(base, "p6"), scores_unit=[],
                       scores_version={})
        pP6 = EP.build_pack(dP6)
        okP6 = (pP6["criteria"][0]["status"] != "pass"
                and "类型非法" in pP6["criteria"][0]["reason"])
        record("P6", "单位空列表版本空字典：类型非法阻断", okP6,
               pP6["criteria"][0]["reason"][:80])

        # P7 源码恢复补丁篡改 → 核验查出
        dP7 = make_run(os.path.join(base, "p7"))
        pP7 = EP.build_pack(dP7)
        pp7 = os.path.join(dP7, EP.PATCH_NAME)
        if os.path.exists(pp7):
            with open(pp7, "a", encoding="utf-8") as f:
                f.write("被篡改的补丁内容")
            vP7 = EP.verify_pack(os.path.join(dP7, EP.PACK_NAME))
            okP7 = (not vP7["pass"]
                    and any("补丁" in q for q in vP7["problems"]))
            detail = "；".join(vP7["problems"])[:90]
        else:
            okP7 = False
            detail = "工作区干净未产补丁（样例失效）"
        record("P7", "源码恢复补丁被改：核验查出哈希错配", okP7, detail)

        # ── L1 接续补齐：整科完整性（Doro 反例重跑与出口核对） ──
        # W1 整科反例重跑（真实原输入形态）：声明悬停整科，结果文件仅一
        # 条升力比单标量条目——第七版据此判整科通过，本轮必须阻断。
        dW1 = make_run(os.path.join(base, "w1"), crit_kind="bench_subject",
                       scores_format="flat")
        pW1 = EP.build_pack(dW1)
        miss_ids = sorted(c.get("criterion_id") for c in pW1["criteria"]
                          if c["status"] == "missing")
        okW1 = (pW1["experiment_verdict"]["status"] != "pass"
                and miss_ids == ["H1", "H3"]
                and all("必需判据条目" in c["reason"]
                        for c in pW1["criteria"]
                        if c["status"] == "missing"))
        record("W1", "整科反例重跑：仅 H2 结果不再整科通过，H1/H3 补缺"
                "失条目", okW1, "判定=%s 缺=%s" % (
                    pW1["experiment_verdict"]["status"], miss_ids))

        # W2 完整整科正常例的核验与卡片：三判据四条目（H3 两被测量）逐
        # 条对应，范围声明整科
        card1 = json.load(open(os.path.join(d1, EP.CARD_NAME),
                               encoding="utf-8"))
        okW2 = (v1["pass"] and len(card1["criteria"]) == 4
                and card1["criteria_scope"]["kind"] == "bench_subject"
                and {c["criterion_id"] for c in card1["criteria"]}
                == {"H1", "H2", "H3"}
                and {(c["criterion_id"], c["quantity"])
                     for c in p1["criteria"]
                     if c["criterion_id"] == "H3"}
                == {("H3", "roll_max_rad"), ("H3", "pitch_max_rad")})
        record("W2", "完整整科：核验过，卡片四条目逐条对应含 H3 两被测"
                "量", okW2, "卡片判据数=%d" % len(card1["criteria"]))

        # W3 缺一项：生产格式缺 H1 → 阻断
        dW3 = make_run(os.path.join(base, "w3"), crit_kind="bench_subject",
                       scores_format="producer",
                       producer_criteria=["H2", "H3"])
        pW3 = EP.build_pack(dW3)
        okW3 = (pW3["experiment_verdict"]["status"] != "pass"
                and any(c["criterion_id"] == "H1"
                        and c["status"] == "missing"
                        for c in pW3["criteria"]))
        record("W3", "生产格式缺 H1：整科阻断", okW3,
               pW3["experiment_verdict"]["reason"][:90])

        # W4 重复条目：H2 出现两次 → 证据歧义改判未知
        dW4 = make_run(os.path.join(base, "w4"), crit_kind="bench_subject",
                       scores_format="producer",
                       producer_criteria=["H1", "H2", "H2", "H3"])
        pW4 = EP.build_pack(dW4)
        h2sts = [c["status"] for c in pW4["criteria"]
                 if c["criterion_id"] == "H2"]
        okW4 = (len(h2sts) == 2 and all(s == "unknown" for s in h2sts)
                and all("重复出现" in c["reason"]
                        for c in pW4["criteria"]
                        if c["criterion_id"] == "H2")
                and pW4["experiment_verdict"]["status"] != "pass")
        record("W4", "重复条目 H2 两次：证据歧义不得通过", okW4,
               "H2 状态=%s" % h2sts)

        # W5 错条目：科目不存在的假条目 H9 → 标准无该条目阻断
        dW5 = make_run(os.path.join(base, "w5"), crit_kind="bench_subject",
                       scores_format="producer",
                       producer_criteria=["H2", {"id": "H9",
                                                 "status": "pass",
                                                 "measured": {"x": 1.0},
                                                 "threshold": {"x": 1.0}}])
        pW5 = EP.build_pack(dW5)
        h9 = next(c for c in pW5["criteria"] if c["criterion_id"] == "H9")
        okW5 = (h9["status"] == "unknown"
                and "无判据条目" in h9["reason"]
                and pW5["experiment_verdict"]["status"] != "pass")
        record("W5", "错条目 H9：科目无该判据条目阻断", okW5,
               h9["reason"][:90])

        # W6 单判据批准范围：声明 bench_criterion H2 而结果含三判据 →
        # H2 记自身结论，H1/H3 如实记不适用，不代称整科通过
        dW6 = make_run(os.path.join(base, "w6"), crit_kind="bench_criterion",
                       crit_criterion="H2", scores_format="producer")
        pW6 = EP.build_pack(dW6)
        v6 = EP.verify_pack(os.path.join(dW6, EP.PACK_NAME))
        st_map = {(c["criterion_id"], c["quantity"]): c["status"]
                  for c in pW6["criteria"]}
        okW6 = (pW6["experiment_verdict"]["status"] == "pass"
                and st_map.get(("H2", "lift_to_weight_ratio")) == "pass"
                and st_map.get(("H1", "survive_duration")) == "not_applicable"
                and st_map.get(("H3", "roll_max_rad")) == "not_applicable"
                and pW6["criteria_scope"]["kind"] == "bench_criterion"
                and v6["pass"])
        record("W6", "单判据声明 H2：自身结论 pass，范围外如实不适用，范"
                "围分列不代称整科", okW6, "判定=%s 范围=%s" % (
                    pW6["experiment_verdict"]["status"],
                    pW6["criteria_scope"]["kind"]))

        # ── L1 接续补齐：必要条件与版本读数 ──
        # W7 必要条件节贯通：正式清单绑定、环境键注入核对、必需量对照
        # 记录量映射、空清单不豁免
        nec_ok = {"source": APPROVED_MANIFEST,
                  "conditions": ["FLAPPER_MAX_S", "FLAPPER_HEADLESS"],
                  "quantities": ["t", "roll"], "note": "自检（清单只加不"
                                                       "减的附加项）"}
        dW7 = make_run(os.path.join(base, "w7"), crit_kind="bench_subject",
                       scores_format="producer", necessary=nec_ok)
        pW7 = EP.build_pack(dW7)
        from modules.experiment_pipeline import config_schema as CS
        probW7 = CS.validate_config(json.load(open(
            os.path.join(dW7, "run_record.json"), encoding="utf-8"))
            ["config"])
        condW7 = json.load(open(os.path.join(dW7, EP.CONDITIONS_NAME),
                                encoding="utf-8"))
        okW7 = (not probW7
                and pW7["necessary_conditions"]["status"] == "complete"
                and condW7["declared"]["necessary_conditions"]
                == nec_ok
                and pW7["experiment_verdict"]["status"] == "pass")
        record("W7", "必要条件节：模式接受、采集留档、注入与必需量核对"
                "齐备", okW7, "核对=%s 模式问题=%s" % (
                    pW7["necessary_conditions"]["status"], probW7[:1]))

        # W7b 条件键未在参数节声明 → 配置校验拒绝运行
        cfg7b = json.load(open(os.path.join(dW7, "run_record.json"),
                               encoding="utf-8"))["config"]
        cfg7b["necessary_conditions"] = dict(
            nec_ok, conditions=["FLAPPER_MAX_S", "FLAPPER_WAVE_AMP"])
        prob7b = CS.validate_config(cfg7b)
        okW7b = bool(prob7b) and any("未在 overrides" in p for p in prob7b)
        record("W7b", "条件键未在参数节声明：配置校验拒绝运行", okW7b,
               (prob7b[:1] or ["无问题"])[0][:90])

        # W7c 声明的条件键未见注入：先构建（齐备），再改条件文件加未注
        # 入键，核验须查出（哈希与内容重查），重建后清单缺口阻断
        dW7c = make_run(os.path.join(base, "w7c"),
                        crit_kind="bench_subject", scores_format="producer",
                        necessary=nec_ok)
        EP.build_pack(dW7c)
        cp7c = os.path.join(dW7c, EP.CONDITIONS_NAME)
        cond7c = json.load(open(cp7c, encoding="utf-8"))
        cond7c["declared"]["necessary_conditions"]["conditions"] = \
            ["FLAPPER_MAX_S", "FLAPPER_WAVE_AMP"]
        wjson(cp7c, cond7c)
        vW7c = EP.verify_pack(os.path.join(dW7c, EP.PACK_NAME))
        pW7c = EP.build_pack(dW7c)
        okW7c = (not vW7c["pass"]
                 and pW7c["necessary_conditions"]["status"] == "deficient"
                 and pW7c["necessary_conditions"]["missing_conditions"]
                 == ["FLAPPER_WAVE_AMP"]
                 and pW7c["experiment_verdict"]["status"] != "pass")
        record("W7c", "声明条件键未注入：核验查出条件变动，重建后清单缺"
                "口阻断", okW7c, "缺=%s" % pW7c["necessary_conditions"]
                ["missing_conditions"])

        # W7d 声明的必需量不在记录量映射 → 构包阻断
        dW7d = make_run(os.path.join(base, "w7d"),
                        crit_kind="bench_subject", scores_format="producer",
                        csv_line="t,phase,freq,dA,Fx,roll\n"
                                 "1,collect,1.8,0,0,0.01\n",
                        necessary=dict(nec_ok, quantities=["t", "z"]))
        pW7d = EP.build_pack(dW7d)
        okW7d = (pW7d["necessary_conditions"]["status"] == "deficient"
                 and "z" in pW7d["necessary_conditions"]["missing_quantities"]
                 and pW7d["experiment_verdict"]["status"] != "pass")
        record("W7d", "必需量 z 不在实际记录量：清单缺口阻断", okW7d,
               "缺=%s" % pW7d["necessary_conditions"]["missing_quantities"])

        # W7e 空清单不豁免：无条件必要项仍由缺项检查把守（面积为空仍阻断）
        dW7e = make_run(os.path.join(base, "w7e"),
                        crit_kind="bench_subject", scores_format="producer",
                        necessary={"source": APPROVED_MANIFEST,
                                   "conditions": [], "quantities": []})
        cp7e = os.path.join(dW7e, EP.CONDITIONS_NAME)
        cond7e = json.load(open(cp7e, encoding="utf-8"))
        cond7e["effective_config"]["classes"]["TailAeroConfig"]["AREA"] = \
            None
        wjson(cp7e, cond7e)
        pW7e = EP.build_pack(dW7e)
        okW7e = (pW7e["necessary_conditions"]["status"] == "not_declared"
                 or pW7e["necessary_conditions"]["status"] == "complete")
        okW7e = (okW7e and pW7e["data_qualification"]["status"] != "valid"
                 and pW7e["experiment_verdict"]["status"] != "pass"
                 and "AREA" in pW7e["data_qualification"]["reason"])
        record("W7e", "空清单不豁免：无条件必要项（面积为空）仍阻断",
               okW7e, "资格=%s" % pW7e["data_qualification"]["status"])

        # W8 运行环境实际版本读数：解释器与仿真环境包，启动命令不当版本
        condW8 = json.load(open(os.path.join(d1, EP.CONDITIONS_NAME),
                                encoding="utf-8"))
        rv8 = condW8["effective_config"].get("runtime_versions") or {}
        pkgs = {e.get("package"): e for e in
                (rv8.get("sim_runtimes") or [])}
        okW8 = (str(rv8.get("interpreter", {}).get("python_version")
                    or "").startswith("3.")
                and bool(condW8["declared"]["scene"].get("argv"))
                and pkgs.get("numpy", {}).get("version")
                and ("isaacsim" in pkgs))
        record("W8", "版本读数：解释器 3.x 与仿真包读数在档，argv 只作声"
                "明", okW8, "py=%s numpy=%s isaacsim=%s" % (
                    rv8.get("interpreter", {}).get("python_version"),
                    pkgs.get("numpy", {}).get("version"),
                    pkgs.get("isaacsim", {}).get("version")
                    or pkgs.get("isaacsim", {}).get("reason")))

        # W8b 删除版本读数 → 缺项阻断（启动命令补不上版本）
        dW8b = make_run(os.path.join(base, "w8b"),
                        crit_kind="bench_subject", scores_format="producer")
        cp8b = os.path.join(dW8b, EP.CONDITIONS_NAME)
        cond8b = json.load(open(cp8b, encoding="utf-8"))
        del cond8b["effective_config"]["runtime_versions"]
        wjson(cp8b, cond8b)
        pW8b = EP.build_pack(dW8b)
        okW8b = (pW8b["conditions_status"] == "captured_deficient"
                 and any("运行环境版本缺失" in d for d in
                         EP._conditions_deficiencies(cond8b))
                 and pW8b["experiment_verdict"]["status"] != "pass")
        record("W8b", "无版本读数：缺项阻断，启动命令不当版本", okW8b,
               "条件=%s" % pW8b["conditions_status"])

        # ── L1 接续补齐：真实生产链贯通（返修 v2：数据流驱动分析步） ──
        # W9 链两步均为纯 Python：数据记录步（台账加数据流 CSV，占位符
        # 绑定本次运行身份）加台架分析步（--data-file 实测取自本次数据
        # 流，--archive-dir 读 identity.json 绑定身份写分数，实际分析输
        # 入登记标准定义与本次数据两类）。
        from modules.experiment_pipeline import runner as R
        # 归档根须与仓库同盘（运行记录用相对路径登记，跨盘会失败）；
        # 放本轮检查目录下，finally 一并清理。
        w9root = tempfile.mkdtemp(
            prefix="i186_w9_", dir=os.path.join(ROOT, "logs"))
        old_archive_root = R.ARCHIVE_ROOT
        recorder_script = (
            "import csv, os, sys\n"
            "sys.path.insert(0, os.getcwd())\n"
            "from modules import fw_iid_ledger as L\n"
            "import config.sim_config as c\n"
            "archive_dir, run_id, tag, max_s = sys.argv[1:5]\n"
            "dt = float(getattr(c, 'PHYSICS_DT', 1.0 / 120.0))\n"
            "steps = int(round(float(max_s) / dt))\n"
            "rec = L.LedgerRecorder(\n"
            "    os.path.join(archive_dir, 'fw_ledger_' + run_id + '.bin'),\n"
            "    ['j' + str(i) for i in range(9)], dt,\n"
            "    meta={'tag': tag, 'run_id': run_id,\n"
            "          'env': {}})\n"
            "for s in range(1, steps + 1):\n"
            "    rec.record(s, {n: [float(s) for _ in range(cn)]\n"
            "                  for n, cn in L.CHAN_LAYOUT})\n"
            "rec.close()\n"
            "cols = ['t', 'x', 'y', 'z', 'vx', 'vy', 'vz', 'wx', 'wy',\n"
            "        'wz', 'qw', 'qx', 'qy', 'qz', 'freq', 'Frz', 'ail',\n"
            "        'pitch_des']\n"
            "from modules.aero_solver import quat_to_euler\n"
            "import numpy as _np\n"
            "csv_path = os.path.join(archive_dir, 'ident_stream_chain.csv')\n"
            "n = int(round(61.0 / dt))\n"
            "with open(csv_path, 'w', newline='', encoding='utf-8') as fh:\n"
            "    w = csv.writer(fh)\n"
            "    w.writerow(cols + ['roll', 'pitch'])\n"
            "    t = 0.0\n"
            "    for _ in range(n + 1):\n"
            "        quat = _np.asarray([1.0, 0.0, 0.0, 0.0])\n"
            "        roll, pitch, _yaw = quat_to_euler(quat)\n"
            "        row = [t] + [0.0 for _ in range(13)]\n"
            "        row = row + [1.8, 2.1523, 0.0, 0.0]\n"
            "        row[3] = 3.0\n"
            "        row[10] = 1.0\n"
            "        w.writerow(['%.6f' % v for v in row]\n"
            "                   + ['%.6f' % roll, '%.6f' % pitch])\n"
            "        t = t + dt\n"
            "print('[W9-REC] run_id=' + run_id + ' steps=' + str(steps)\n"
            "      + ' csv_rows=' + str(n + 1))\n")
        w9_necessary = {
            "source": APPROVED_MANIFEST,
            "note": "返修 v3：必要量由批准清单 hover_csv 分支决定"}
        w9cfg = {
            "scene": {"mode": "python", "duration_s": 0.0666,
                      "argv": [sys.executable, "-c", "pass"]},
            "overrides": {"FLAPPER_MAX_S": "0.0666",
                          "FLAPPER_HEADLESS": "1",
                          "FLAPPER_AERO_MODEL": APPROVED_AERO},
            "tags": {"name": "i186_w9_chain",
                     "desc": "I-186 生产贯通自检链 v2"},
            "criteria": {"kind": "bench_subject", "subject": "hover"},
            "locks": {"standard": True},
            "item": "I-186",
            "necessary_conditions": w9_necessary,
            "chain": [
                {"name": "witness", "mode": "python",
                 "argv": [sys.executable, "-m",
                          "modules.experiment_pipeline.scene_witness",
                          "{archive_dir}", "{experiment_id}", "{run_id}",
                          "json:" + json.dumps(_DEFAULT_WITNESS_FACTS,
                                               ensure_ascii=False,
                                               separators=(",", ":"))]},
                {"name": "record", "mode": "python",
                 "argv": [sys.executable, "-c", recorder_script,
                          "{archive_dir}", "{run_id}", "{tag}", "0.0666"]},
                {"name": "judge", "mode": "python",
                 "argv": [sys.executable, "-m",
                          "modules.backend.bench_runner",
                          "--subject", "hover",
                          "--data-file",
                          "{archive_dir}/ident_stream_chain.csv",
                          "--archive-dir", "{archive_dir}"]},
            ],
        }
        w9cfg_path = os.path.join(w9root, "w9_config.json")
        wjson(w9cfg_path, w9cfg)
        R.ARCHIVE_ROOT = os.path.join(w9root, "experiments")
        import io as _io
        from contextlib import redirect_stdout
        buf9 = _io.StringIO()
        try:
            with redirect_stdout(buf9):
                rc9 = R.main([w9cfg_path])
        finally:
            R.ARCHIVE_ROOT = old_archive_root
        out9 = buf9.getvalue()
        exp_root9 = os.path.join(w9root, "experiments")
        arch9 = None
        if os.path.isdir(exp_root9):
            cands = [os.path.join(exp_root9, n) for n in
                     sorted(os.listdir(exp_root9))
                     if os.path.isdir(os.path.join(exp_root9, n))]
            arch9 = cands[-1] if cands else None
        okW9pre = (rc9 == 0 and arch9 is not None
                   and "EXPERIMENT DONE" in out9
                   and "证据包已生成" in out9)
        if okW9pre:
            rec9 = json.load(open(os.path.join(arch9, "run_record.json"),
                                  encoding="utf-8"))
            ident9 = json.load(open(os.path.join(arch9, "identity.json"),
                                    encoding="utf-8"))
            scores_files = [f for f in os.listdir(arch9)
                            if f.endswith("_scores.json")]
            sc9 = json.load(open(os.path.join(arch9, scores_files[0]),
                                 encoding="utf-8")) if scores_files else {}
            csv9_path = os.path.join(arch9, "ident_stream_chain.csv")
            run_data_refs = [e for e in (sc9.get("analyzed_inputs") or [])
                             if isinstance(e, dict)
                             and e.get("role") == "run_data"]
            pack9 = json.load(open(os.path.join(arch9, EP.PACK_NAME),
                                   encoding="utf-8"))
            v9 = EP.verify_pack(os.path.join(arch9, EP.PACK_NAME))
            card9 = json.load(open(os.path.join(arch9, EP.CARD_NAME),
                                   encoding="utf-8"))
            crit_ids9 = {(c.get("criterion_id"), c.get("quantity"))
                         for c in pack9["criteria"]}
            wit9 = (rec9["steps"] or [{}])[0].get("env_witness") or {}
            okW9 = (rec9["schema_version"] == 2
                    and str(rec9["experiment_id"]).startswith("exp-")
                    and rec9["run_id"] == os.path.basename(arch9)
                    and rec9["item"] == "I-186"
                    and ident9["experiment_id"] == rec9["experiment_id"]
                    and ident9["run_id"] == rec9["run_id"]
                    and sc9.get("experiment_id") == rec9["experiment_id"]
                    and sc9.get("run_id") == rec9["run_id"]
                    and sc9.get("item") == "I-186"
                    and bool(run_data_refs)
                    and run_data_refs[0].get("sha256") == sha(csv9_path)
                    and pack9["execution"]["status"] == "completed"
                    and pack9["data_qualification"]["status"] == "valid"
                    and pack9["necessary_conditions"]["status"] == "complete"
                    and pack9["criteria_scope"]["kind"] == "bench_subject"
                    and (pack9.get("approved_scenario_comparison")
                         or {}).get("verdict") == "divergent"
                    and pack9["applicability"]["status"]                         == "not_applicable"
                    and all(c["status"] == "pass"
                            for c in pack9["criteria"])
                    and pack9["experiment_verdict"]["status"] != "pass"
                    and os.path.isfile(os.path.join(arch9,
                                                    "scene_witness.json"))
                    and crit_ids9 == {
                        ("H1", "survive_duration"),
                        ("H2", "lift_to_weight_ratio"),
                        ("H3", "roll_max_rad"), ("H3", "pitch_max_rad")}
                    and v9["pass"]
                    and len(card9["criteria"]) == 4
                    and wit9.get("interpreter") == sys.executable
                    and "FLAPPER_" not in "".join(
                        (wit9.get("env_final") or {}).keys()))
            detail9 = ("判定=%s 资格=%s 必要=%s 缺事实=%s 判据数=%d 核验=%s 数据绑定=%s "
                       "场景=%s 适用=%s 见证=%s"
                       % (pack9["experiment_verdict"]["status"],
                          pack9["data_qualification"]["status"],
                          pack9["necessary_conditions"]["status"],
                          pack9["necessary_conditions"].get("missing_facts"),
                          len(pack9["criteria"]), v9["pass"],
                          bool(run_data_refs),
                          (pack9.get("approved_scenario_comparison")
                           or {}).get("verdict"),
                          pack9["applicability"]["status"],
                          os.path.isfile(os.path.join(
                              arch9, "scene_witness.json"))))
        else:
            okW9 = False
            detail9 = "rc=%s 归档=%s" % (rc9, arch9)
        # 返修 v4 语义：python 链隔离环境下生效模型为仓库默认
        # sane_dickinson_3d，未获本场景批准也未实际选用——生产链如实判
        # 与批准场景不符（不冒充、不改真值），全链机械（身份、数据绑
        # 定、见证、核验一致性）贯通；批准模型的正控制由 X7 的 isaac
        # 采集路径（真实子进程实际应用）承担。
        record("W9", "真实生产链：见证与场景比较贯通，默认模型未获批如"
                "实阻断", okW9, detail9)
        shutil.rmtree(w9root, ignore_errors=True)

        # ── 返修 v2：本轮五组真实反例与正常控制 ──
        # V1 分析只列标准未列本次数据（Doro 原输入形态）→ 测量科目阻断
        dV1 = make_run(os.path.join(base, "v1"), crit_kind="bench_subject",
                       scores_format="producer")
        spV1 = os.path.join(dV1, "self_scores.json")
        docV1 = json.load(open(spV1, encoding="utf-8"))
        docV1["analyzed_inputs"] = [{
            "file": "config/bench_criteria.json",
            "sha256": sha(os.path.join(ROOT, "config",
                                      "bench_criteria.json")),
            "role": "standard_definition"}]
        wjson(spV1, docV1)
        pV1 = EP.build_pack(dV1)
        okV1 = (pV1["experiment_verdict"]["status"] != "pass"
                and any("未绑定本次实际分析数据" in c["reason"]
                        for c in pV1["criteria"]))
        record("V1", "分析只列标准未列本次数据：阻断不正式通过", okV1,
               pV1["criteria"][0]["reason"][:90])

        # V2 必要清单引用不存在来源（Doro 原输入形态）→ 配置拒绝且构包
        # 待审阻断
        cfgV2 = {
            "scene": {"mode": "isaac", "duration_s": 0.0666,
                      "argv": ["mock"]},
            "overrides": {"FLAPPER_MAX_S": "0.0666"},
            "tags": {"name": "run-A", "desc": "I-186 自检"},
            "criteria": {"kind": "bench_subject", "subject": "hover"},
            "locks": {"standard": True},
            "necessary_conditions": {
                "source": "config/I186_APPROVED_RECIPE_DOES_NOT_EXIST.json"
                          "#hover",
                "conditions": [], "quantities": []},
        }
        probV2 = CS.validate_config(cfgV2)
        dV2 = make_run(os.path.join(base, "v2"), crit_kind="bench_subject",
                       scores_format="producer",
                       necessary=cfgV2["necessary_conditions"])
        pV2 = EP.build_pack(dV2)
        okV2 = (any("来源文件不存在" in p for p in probV2)
                and pV2["necessary_conditions"]["status"]
                == "pending_source"
                and pV2["experiment_verdict"]["status"] != "pass")
        record("V2", "必要清单引用不存在来源：配置拒绝运行，构包待审阻"
                "断", okV2,
                "模式=%s 包=%s" % (probV2[:1] and "拒" or "过",
                                   pV2["necessary_conditions"]["status"]))

        # V2b 未带版本标记的文件作来源（未批准定义文件）→ 待审阻断
        unversioned = os.path.join(
            ROOT, "logs", "i186_l1_rework_v2_20261014", "tmp",
            "unversioned_recipe.json")
        os.makedirs(os.path.dirname(unversioned), exist_ok=True)
        wjson(unversioned, {"subjects": {"hover": {"note": "无版本标记"}}})
        necV2b = {
            "source": "logs/i186_l1_rework_v2_20261014/tmp/"
                      "unversioned_recipe.json",
            "conditions": [], "quantities": []}
        dV2b = make_run(os.path.join(base, "v2b"),
                        crit_kind="bench_subject", scores_format="producer",
                        necessary=necV2b)
        pV2b = EP.build_pack(dV2b)
        probV2b = CS.validate_config({
            "scene": {"mode": "isaac", "duration_s": 1, "argv": ["m"]},
            "overrides": {}, "tags": {"name": "t", "desc": "t"},
            "criteria": {"kind": "bench_subject", "subject": "hover"},
            "locks": {"standard": True},
            "necessary_conditions": necV2b})
        okV2b = (pV2b["necessary_conditions"]["status"] == "pending_source"
                 and any("未带版本" in p for p in probV2b))
        record("V2b", "未带版本标记文件作来源：未批准定义待审阻断", okV2b,
               pV2b["necessary_conditions"].get("reason", "")[:90])

        # V3 必需量存在但意义未知（vphz，Doro 原输入形态）→ 定义与来源
        # 未知阻断，不猜成拍相位
        dV3 = make_run(os.path.join(base, "v3"), crit_kind="bench_subject",
                       scores_format="producer",
                       csv_line="t,phase,vphz,freq,dA,Fx,roll\n"
                                "1,collect,3.6,1.8,0,0,0.01\n",
                       necessary={"source": APPROVED_MANIFEST,
                                  "conditions": [],
                                  "quantities": ["vphz"]})
        pV3 = EP.build_pack(dV3)
        necV3 = pV3["necessary_conditions"]
        okV3 = (necV3["status"] == "deficient"
                and necV3["undefined_quantities"] == ["vphz"]
                and pV3["experiment_verdict"]["status"] != "pass")
        record("V3", "必需量 vphz 存在但定义与来源未知：阻断不猜语义",
               okV3, "缺定义=%s" % necV3.get("undefined_quantities"))

        # V4 实际执行环境与起始快照一致（Doro 原输入形态：合法 python
        # 配置声明步长 0.02，实际子进程无该覆盖）
        w4root = tempfile.mkdtemp(
            prefix="i186_v4_", dir=os.path.join(ROOT, "logs"))
        v4cfg = {
            "scene": {"mode": "python", "duration_s": 1.0,
                      "argv": [sys.executable, "-c", "pass"]},
            "overrides": {"FLAPPER_PHYS_DT": "0.02",
                          "FLAPPER_HEADLESS": "1"},
            "tags": {"name": "i186_v4_env",
                     "desc": "I-186 执行环境见证自检"},
            "criteria": {"kind": "bench_subject", "subject": "hover"},
            "locks": {"standard": True},
            "item": "I-186",
        }
        v4cfg_path = os.path.join(w4root, "v4_config.json")
        wjson(v4cfg_path, v4cfg)
        R.ARCHIVE_ROOT = os.path.join(w4root, "experiments")
        buf4 = _io.StringIO()
        try:
            with redirect_stdout(buf4):
                rc4 = R.main([v4cfg_path])
        finally:
            R.ARCHIVE_ROOT = old_archive_root
        exp4 = os.path.join(w4root, "experiments")
        arch4 = None
        if os.path.isdir(exp4):
            cands4 = [os.path.join(exp4, n) for n in sorted(os.listdir(exp4))
                      if os.path.isdir(os.path.join(exp4, n))]
            arch4 = cands4[-1] if cands4 else None
        okV4 = False
        detail4 = "rc=%s 归档=%s" % (rc4, arch4)
        if arch4:
            cond4 = json.load(open(os.path.join(arch4, EP.CONDITIONS_NAME),
                                   encoding="utf-8"))
            rec4 = json.load(open(os.path.join(arch4, "run_record.json"),
                                  encoding="utf-8"))
            wit4 = (rec4.get("steps") or [{}])[0].get("env_witness") or {}
            applied4 = cond4.get("applied_overrides") or {}
            okV4 = (rc4 == 0
                    and "FLAPPER_PHYS_DT" not in applied4
                    and "FLAPPER_PHYS_DT" in (
                        cond4.get("declared_overrides_not_applied") or [])
                    and abs(float(cond4["effective_config"]["phys_dt"])
                            - 1.0 / 120.0) < 1e-9
                    and "FLAPPER_PHYS_DT" not in (wit4.get("env_final")
                                                  or {})
                    and wit4.get("interpreter") == sys.executable)
            detail4 = ("applied无dt=%s 快照dt=%s 见证dt=%s"
                       % ("FLAPPER_PHYS_DT" not in applied4,
                          cond4["effective_config"]["phys_dt"],
                          "FLAPPER_PHYS_DT" not in (
                              wit4.get("env_final") or {})))
        record("V4", "实际执行环境一致：python 步隔离如实采集，快照不称"
                "未应用的覆盖", okV4, detail4)
        shutil.rmtree(w4root, ignore_errors=True)

        # V4b python 模式声明必要条件未实际应用 → 如实限制资格不通过
        dV4b = make_run(os.path.join(base, "v4b"),
                        crit_kind="bench_subject", scores_format="producer",
                        scene_mode="python",
                        necessary={"source": APPROVED_MANIFEST,
                                   "conditions": ["FLAPPER_PHYS_DT"],
                                   "quantities": []})
        pV4b = EP.build_pack(dV4b)
        necV4b = pV4b["necessary_conditions"]
        okV4b = (necV4b["status"] == "deficient"
                 and "FLAPPER_PHYS_DT" in (necV4b.get("missing_conditions")
                                           or [])
                 and pV4b["experiment_verdict"]["status"] != "pass")
        record("V4b", "必要条件未实际应用：如实限制资格不通过", okV4b,
               "必要=%s 缺=%s" % (necV4b.get("status"),
                                  necV4b.get("missing_conditions")))

        # V5 包与卡同时删去必需判据（Doro 原输入形态：原结果未变）→ 核
        # 验独立重查拒绝
        dV5 = make_run(os.path.join(base, "v5"), crit_kind="bench_subject",
                       scores_format="producer")
        EP.build_pack(dV5)
        packV5p = os.path.join(dV5, EP.PACK_NAME)
        cardV5p = os.path.join(dV5, EP.CARD_NAME)
        docP = json.load(open(packV5p, encoding="utf-8"))
        docC = json.load(open(cardV5p, encoding="utf-8"))
        docP["criteria"] = [c for c in docP["criteria"]
                            if c.get("criterion_id") != "H1"]
        docC["criteria"] = [c for c in docC["criteria"]
                            if c.get("criterion_id") != "H1"]
        wjson(packV5p, docP)
        wjson(cardV5p, docC)
        vV5 = EP.verify_pack(packV5p)
        okV5 = (not vV5["pass"]
                and any("整科齐备性" in q or "重算" in q
                        for q in vV5["problems"]))
        record("V5", "包与卡同时删存活判据：核验按运行声明与原结果重查"
                "拒绝", okV5, "；".join(vV5["problems"])[:90] or "未查出")

        # V5b 修改必要条目状态（包判 fail 改 pass）→ 核验重查拒绝
        dV5b = make_run(os.path.join(base, "v5b"), crit_kind="bench_subject",
                        scores_format="producer",
                        producer_criteria=[
                            {"id": "H1", "status": "fail",
                             "measured": {"survive_s": 10.0},
                             "threshold": _hover_criteria()[0]["threshold"]},
                            "H2", "H3"])
        EP.build_pack(dV5b)
        packV5b = os.path.join(dV5b, EP.PACK_NAME)
        docPb = json.load(open(packV5b, encoding="utf-8"))
        for c in docPb["criteria"]:
            if c.get("criterion_id") == "H1":
                c["status"] = "pass"
                c["reason"] = "被篡改为通过"
        wjson(packV5b, docPb)
        vV5b = EP.verify_pack(packV5b)
        okV5b = (not vV5b["pass"]
                 and any("重算" in q for q in vV5b["problems"]))
        record("V5b", "包内修改必要条目状态：核验重算不符拒绝", okV5b,
               "；".join(vV5b["problems"])[:90] or "未查出")

        # V5c 包与卡同时增加不存在条目 → 核验重查拒绝
        dV5c = make_run(os.path.join(base, "v5c"), crit_kind="bench_subject",
                        scores_format="producer")
        EP.build_pack(dV5c)
        packV5c = os.path.join(dV5c, EP.PACK_NAME)
        cardV5c = os.path.join(dV5c, EP.CARD_NAME)
        docPc = json.load(open(packV5c, encoding="utf-8"))
        docCc = json.load(open(cardV5c, encoding="utf-8"))
        fake = {"subject_declared": "hover", "subject_actual": "hover",
                "criterion_id": "H4", "quantity": None, "status": "pass"}
        docPc["criteria"].append(dict(fake, reason="伪造条目"))
        docCc["criteria"].append(dict(fake))
        wjson(packV5c, docPc)
        wjson(cardV5c, docCc)
        vV5c = EP.verify_pack(packV5c)
        okV5c = (not vV5c["pass"]
                 and any("重算" in q for q in vV5c["problems"]))
        record("V5c", "包与卡同时增加伪造条目：核验重算不符拒绝", okV5c,
               "；".join(vV5c["problems"])[:90] or "未查出")

        # V6 包内科学值篡改（Doro 原输入形态：原值 1.02 改 0.5，阈值与
        # 状态不动）→ 核验与原结果逐值核对拒绝
        dV6 = make_run(os.path.join(base, "v6"), crit_kind="bench_subject",
                       scores_format="producer")
        EP.build_pack(dV6)
        packV6 = os.path.join(dV6, EP.PACK_NAME)
        docV6 = json.load(open(packV6, encoding="utf-8"))
        for c in docV6["criteria"]:
            if c.get("criterion_id") == "H2":
                c["value"] = 0.5
        wjson(packV6, docV6)
        vV6 = EP.verify_pack(packV6)
        okV6 = (not vV6["pass"]
                and any("重算" in q for q in vV6["problems"]))
        record("V6", "包内升力比值改 0.5：核验按原结果逐值核对拒绝",
               okV6, "；".join(vV6["problems"])[:90] or "未查出")

        # V7 自造版本标记文件被当批准配方（Doro 原输入形态）→ 未获批准
        # 待审阻断
        selfmade = os.path.join(ROOT, "logs", "i186_l1_rework_v3_20261014",
                                "tmp", "selfmade_recipe.json")
        os.makedirs(os.path.dirname(selfmade), exist_ok=True)
        wjson(selfmade, {
            "version": "SELFMADE-v1", "record_id": "SELFMADE-RECORD",
            "subjects": {"hover": {
                "necessary_conditions": {"conditions": [],
                                         "quantities": []}}}})
        selfmade_rel = "logs/i186_l1_rework_v3_20261014/tmp/" \
                       "selfmade_recipe.json"
        dV7 = make_run(os.path.join(base, "v7"), crit_kind="bench_subject",
                       scores_format="producer",
                       necessary={"source": selfmade_rel})
        pV7 = EP.build_pack(dV7)
        okV7 = (pV7["necessary_conditions"]["status"] == "pending_source"
                and "不是当前批准记录登记" in pV7["necessary_conditions"]
                .get("reason", "")
                and pV7["experiment_verdict"]["status"] != "pass")
        record("V7", "自造版本标记文件：未获批准待审阻断", okV7,
               pV7["necessary_conditions"].get("reason", "")[:90])

        # V8 未携带必要基线的批准文件加空清单（Doro 原输入形态）→ 待审
        # 阻断，不借旧口径放行新实验
        dV8 = make_run(os.path.join(base, "v8"), crit_kind="bench_subject",
                       scores_format="producer",
                       necessary={"source": "config/bench_criteria.json"
                                            "#hover",
                                  "conditions": [], "quantities": []})
        pV8 = EP.build_pack(dV8)
        okV8 = (pV8["necessary_conditions"]["status"] == "pending_source"
                and pV8["experiment_verdict"]["status"] != "pass")
        record("V8", "判据文件无必要基线加空清单：待审阻断", okV8,
               pV8["necessary_conditions"].get("reason", "")[:90])

        # V9 错分支：有 CSV 记录却声明台账分支（不得豁免 CSV 必需量）；
        # 仅台账却声明 CSV 分支
        dV9 = make_run(os.path.join(base, "v9"), crit_kind="bench_subject",
                       scores_format="producer",
                       necessary={"source": APPROVED_MANIFEST
                                  + "#hover_ledger"})
        pV9 = EP.build_pack(dV9)
        okV9 = (pV9["necessary_conditions"]["status"] == "pending_source"
                and "错分支" in pV9["necessary_conditions"].get("reason", ""))
        record("V9", "有 CSV 记录声明台账分支：错分支阻断", okV9,
               pV9["necessary_conditions"].get("reason", "")[:90])
        dV9b = make_run(os.path.join(base, "v9b"), crit_kind="bench_subject",
                        scores_format="producer", csv_line=None,
                        necessary={"source": APPROVED_MANIFEST
                                   + "#hover_csv"})
        pV9b = EP.build_pack(dV9b)
        okV9b = (pV9b["necessary_conditions"]["status"] == "pending_source"
                 and "不符" in pV9b["necessary_conditions"].get("reason", ""))
        record("V9b", "仅台账记录声明 CSV 分支：错分支阻断", okV9b,
               pV9b["necessary_conditions"].get("reason", "")[:90])

        # ── 返修 v3：冻结存活计时同输入新旧入口对照 ──
        from modules.backend import bench_runner as BR

        hover_crits = _hover_criteria()
        h1c = next(c for c in hover_crits if c["id"] == "H1")

        def _mk_rows(t_end, dt=1.0 / 120.0, z=3.0, crash_from_t=None,
                     crash_z=0.1, dip_exact_t=None):
            rows = []
            n = int(round(t_end / dt))
            for i in range(n + 1):
                t = round(i * dt, 9)
                zz = z
                if crash_from_t is not None and t >= crash_from_t:
                    zz = crash_z
                if dip_exact_t is not None and t == dip_exact_t:
                    zz = crash_z
                rows.append({"t": t, "z": zz, "x": 0.0, "y": 0.0,
                             "vx": 0.0, "vy": 0.0, "vz": 0.0,
                             "wx": 0.0, "wy": 0.0, "wz": 0.0,
                             "roll": 0.0, "pitch": 0.0, "yaw": 0.0,
                             "freq": 1.8, "Frz": 2.1523})
            return rows

        def _h1of(judged):
            return next(c for c in judged if c["id"] == "H1")

        # U1 完整 60 s：老批准入口（run_hover_bench 逐语义）报 60，新数据
        # 流入口同输入语义报 60，均通过
        text_old, _code_old = BR.run_hover_mock()
        measured_old = BR.parse_hover_output(text_old)
        judged_old, _cons = BR.judge_hover(hover_crits, measured_old)
        h1_old = _h1of(judged_old)
        h1_new = _h1of(BR.judge_hover_from_stream(
            _mk_rows(60.0), hover_crits, True, "u1"))
        okU1 = (h1_old["measured"]["survive_s"] == 60.0
                and h1_new["measured"]["survive_s"] == 60.0
                and h1_old["status"] == "pass"
                and h1_new["status"] == "pass")
        record("U1", "完整 60 s 无坠机：新旧入口一致报 60 秒均通过", okU1,
               "老=%s 新=%s" % (h1_old["measured"]["survive_s"],
                                h1_new["measured"]["survive_s"]))

        # U2 t=1 飞行窗边界：t=1.0 与 t=0.5 的出带行均在窗外，不判坠机
        h1u2a = _h1of(BR.judge_hover_from_stream(
            _mk_rows(60.0, dip_exact_t=1.0), hover_crits, True, "u2a"))
        h1u2b = _h1of(BR.judge_hover_from_stream(
            _mk_rows(60.0, dip_exact_t=0.5), hover_crits, True, "u2b"))
        okU2 = (h1u2a["status"] == "pass"
                and h1u2a["measured"]["survive_s"] == 60.0
                and h1u2b["status"] == "pass"
                and h1u2b["measured"]["survive_s"] == 60.0)
        record("U2", "t=1 边界：窗外出带行不判坠机，存活为实际覆盖", okU2,
               "t=1.0 报 %s，t=0.5 报 %s"
               % (h1u2a["measured"]["survive_s"],
                  h1u2b["measured"]["survive_s"]))

        # U3 首个 t>1 坠机：坠机时刻用原仿真时刻（不减窗起点）
        h1u3 = _h1of(BR.judge_hover_from_stream(
            _mk_rows(60.0, crash_from_t=round(121 * (1.0 / 120.0), 9)),
            hover_crits, True, "u3"))
        okU3 = (h1u3["status"] == "fail"
                and h1u3["measured"]["survive_s"]
                == round(round(121 * (1.0 / 120.0), 9), 4))
        record("U3", "首个 t>1 坠机：存活取原仿真时刻", okU3,
               "报 %s" % h1u3["measured"]["survive_s"])

        # U4 已知中途坠机：存活为坠机原仿真时刻
        h1u4 = _h1of(BR.judge_hover_from_stream(
            _mk_rows(60.0, crash_from_t=35.0), hover_crits, True, "u4"))
        okU4 = (h1u4["status"] == "fail"
                and abs(h1u4["measured"]["survive_s"] - 35.0) < 1e-6)
        record("U4", "中途坠机：存活 35 秒（原仿真时刻）", okU4,
               "报 %s" % h1u4["measured"]["survive_s"])

        # U5 实际未完成覆盖：只盖 50 s 不按预期 60 补成通过
        h1u5 = _h1of(BR.judge_hover_from_stream(
            _mk_rows(50.0), hover_crits, True, "u5"))
        h1u5b = _h1of(BR.judge_hover_from_stream(
            _mk_rows(50.0), hover_crits, False, "u5b"))
        okU5 = (h1u5["status"] == "fail"
                and abs(h1u5["measured"]["survive_s"] - 50.0) < 1e-6
                and h1u5b["status"] == "fail"
                and abs(h1u5b["measured"]["survive_s"] - 50.0) < 1e-6)
        record("U5", "未完成覆盖：存活按实际 50 秒，不按预期补成通过",
               okU5, "完成态报 %s，未完成态报 %s"
               % (h1u5["measured"]["survive_s"],
                  h1u5b["measured"]["survive_s"]))

        # ── 返修 v4：实际条件采集与批准场景适用性 ──
        # X1 未采集实际初始化事件（Doro 原输入形态：无场景见证）→ 事实
        # 缺失点名阻断，不得以批准场景代填
        dX1 = make_run(os.path.join(base, "x1"), crit_kind="bench_subject",
                       scores_format="producer", witness=False)
        pX1 = EP.build_pack(dX1)
        necX1 = pX1["necessary_conditions"]
        missX1 = necX1.get("missing_facts") or []
        okX1 = (necX1["status"] == "deficient"
                and any(m.startswith("initial_state.") for m in missX1)
                and any(m.startswith("inflow.") for m in missX1)
                and pX1["experiment_verdict"]["status"] != "pass"
                and necX1.get("scene_witness", {}).get("problem"))
        record("X1", "无场景见证：实际事实缺失点名阻断，不以场景代填",
               okX1, "缺 %d 项，首 %s" % (len(missX1), missX1[:2]))

        # X2 换用未获本悬停批准的模型（Doro 原输入形态：实际应用
        # attached_flow）→ 与批准场景不符阻断
        dX2 = make_run(os.path.join(base, "x2"), crit_kind="bench_subject",
                       scores_format="producer",
                       extra_overrides={"FLAPPER_AERO_MODEL":
                                        "attached_flow"})
        pX2 = EP.build_pack(dX2)
        cmpX2 = pX2.get("approved_scenario_comparison") or {}
        okX2 = (cmpX2.get("verdict") == "divergent"
                and any(i["item"] == "aero_model" and i["verdict"]
                        == "divergent" and i["actual"] == "attached_flow"
                        for i in cmpX2.get("items") or [])
                and pX2["applicability"]["status"] == "not_applicable"
                and pX2["experiment_verdict"]["status"] != "pass")
        vcardX2 = json.load(open(os.path.join(dX2, EP.CARD_NAME),
                                 encoding="utf-8"))
        okX2 = okX2 and vcardX2.get("approved_scenario", {}).get(
            "verdict") == "divergent"
        vX2 = EP.verify_pack(os.path.join(dX2, EP.PACK_NAME))
        okX2 = okX2 and vX2["pass"]
        record("X2", "实际模型 attached_flow 未获本场景批准：适用性阻断",
               okX2, "比较=%s 适用=%s 核验一致=%s"
               % (cmpX2.get("verdict"), pX2["applicability"]["status"],
                  vX2["pass"]))

        # X2b 默认模型 sane_dickinson_3d 亦不自动获批 → 不符阻断
        dX2b = make_run(os.path.join(base, "x2b"), crit_kind="bench_subject",
                        scores_format="producer", aero_approved=False)
        pX2b = EP.build_pack(dX2b)
        cmpX2b = pX2b.get("approved_scenario_comparison") or {}
        aero_item_x2b = next((i for i in cmpX2b.get("items") or []
                              if i["item"] == "aero_model"), {})
        okX2b = (cmpX2b.get("verdict") == "divergent"
                 and aero_item_x2b.get("actual") == "sane_dickinson_3d"
                 and pX2b["experiment_verdict"]["status"] != "pass")
        record("X2b", "默认 sane_dickinson_3d 不自动获批：不符阻断", okX2b,
               "实际=%s" % aero_item_x2b.get("actual"))

        # X3 见证缺来流节 → 缺来源点名（inflow 事实缺失）
        factsX3 = json.loads(json.dumps(_DEFAULT_WITNESS_FACTS,
                                        ensure_ascii=False))
        factsX3.pop("inflow")
        dX3 = make_run(os.path.join(base, "x3"), crit_kind="bench_subject",
                       scores_format="producer", witness_facts=factsX3)
        pX3 = EP.build_pack(dX3)
        missX3 = pX3["necessary_conditions"].get("missing_facts") or []
        okX3 = (pX3["necessary_conditions"]["status"] == "deficient"
                and any(m.startswith("inflow.") for m in missX3)
                and pX3["experiment_verdict"]["status"] != "pass")
        record("X3", "见证缺来流节：实际来源缺失点名阻断", okX3,
               "缺=%s" % [m for m in missX3 if m.startswith("inflow.")])

        # X4 真实差异：见证如实声明实际风 1 m/s → 与批准无风不符，采集
        # 真值保留不改
        factsX4 = json.loads(json.dumps(_DEFAULT_WITNESS_FACTS,
                                        ensure_ascii=False))
        factsX4["inflow"]["ambient_flow_world_m_per_s"] = {
            "value": [1.0, 0.0, 0.0],
            "source": "见证如实声明：生成了恒定前向来流（结构样例注入）"}
        dX4 = make_run(os.path.join(base, "x4"), crit_kind="bench_subject",
                       scores_format="producer", witness_facts=factsX4)
        pX4 = EP.build_pack(dX4)
        cmpX4 = pX4.get("approved_scenario_comparison") or {}
        inflow_item = next((i for i in cmpX4.get("items") or []
                            if i["item"] == "inflow"), {})
        okX4 = (cmpX4.get("verdict") == "divergent"
                and inflow_item.get("actual", {}).get(
                    "ambient_flow_world_m_per_s") == [1.0, 0.0, 0.0]
                and pX4["applicability"]["status"] == "not_applicable"
                and pX4["experiment_verdict"]["status"] != "pass")
        record("X4", "实际风 1 m/s 真值保留：与批准无风不符阻断", okX4,
               "适用=%s" % pX4["applicability"]["status"])

        # X5 初始化/释放时点事件在档（钉住后释放见证）→ 事实完整，比较
        # 不误判
        factsX5 = json.loads(json.dumps(_DEFAULT_WITNESS_FACTS,
                                        ensure_ascii=False))
        factsX5["initial_state"]["initialization_and_release_boundary"] = {
            "event": "pin_then_release",
            "startup_pin_s": 0.9,
            "release_actions": ["清空气动滤波状态", "清空控制器积分状态"],
            "source": "记录脚本按冻结场景口径执行的钉放序列（结构样例）"}
        factsX5["physical_constraints"]["pin_release_schedule"] = {
            "event": "pin_then_release", "startup_pin_s": 0.9,
            "source": "同上"}
        dX5 = make_run(os.path.join(base, "x5"), crit_kind="bench_subject",
                       scores_format="producer", witness_facts=factsX5)
        pX5 = EP.build_pack(dX5)
        okX5 = (pX5["necessary_conditions"]["status"] == "complete"
                and (pX5.get("approved_scenario_comparison")
                     or {}).get("verdict") == "match"
                and pX5["experiment_verdict"]["status"] == "pass")
        record("X5", "初始化与释放时点事件在档：事实完整通过", okX5,
               "判定=%s" % pX5["experiment_verdict"]["status"])

        # X6 无环境覆盖仍有参数：无控制器覆盖键而控制器事实由见证提供
        dX6 = make_run(os.path.join(base, "x6"), crit_kind="bench_subject",
                       scores_format="producer")
        pX6 = EP.build_pack(dX6)
        missX6 = pX6["necessary_conditions"].get("missing_facts") or []
        appliedX6 = pX6["necessary_conditions"].get("applied_union") or []
        okX6 = (not any(m.startswith("controller.") for m in missX6)
                and not any("CONTROLLER" in k or "GAIN" in k
                            for k in appliedX6)
                and (pX6["necessary_conditions"]["facts"].get("controller")
                     or {}).get("effective_parameters") is not None)
        record("X6", "无控制器覆盖键而事实由见证提供：参数不因无覆盖缺"
                "失", okX6, "控制器缺项=%s"
               % [m for m in missX6 if m.startswith("controller.")])

        # X7 合格完整见证正控制：批准模型加完整见证 → 通过
        dX7 = make_run(os.path.join(base, "x7"), crit_kind="bench_subject",
                       scores_format="producer")
        pX7 = EP.build_pack(dX7)
        okX7 = (pX7["necessary_conditions"]["status"] == "complete"
                and pX7["necessary_conditions"].get("scene_witness",
                                                    {}).get("file")
                == "scene_witness.json"
                and (pX7.get("approved_scenario_comparison")
                     or {}).get("verdict") == "match"
                and pX7["applicability"]["status"] == "applicable"
                and pX7["experiment_verdict"]["status"] == "pass")
        record("X7", "完整见证加批准模型：事实与适用齐备通过", okX7,
               "判定=%s" % pX7["experiment_verdict"]["status"])

        # X8 人工样例边界：数据来源与结论边界如实，不作飞行成功证明
        facts8 = pX7["necessary_conditions"]["facts"]
        card8 = json.load(open(os.path.join(dX7, EP.CARD_NAME),
                               encoding="utf-8"))
        okX8 = ("人工接口" in str(facts8.get("run", {}).get("data_origin"))
                and pX7["control_use"]["status"] == "diagnostic_only"
                and card8.get("approved_scenario") is not None
                and "结构样例" in str(facts8["initial_state"].get(
                    "initial_position_world_m", {}).get("source")))
        record("X8", "人工样例边界：来源与结论边界如实标明", okX8,
               "data_origin=%s" % str(facts8.get("run", {}).get(
                   "data_origin"))[:40])

        # ── 返修 v5：逐字段事实有效性与完整批准范围比较 ──
        # 六个 Doro 原输入反例逐例重跑：直接改变原始见证事实，检查生产
        # 逻辑（构包与核验）是否发现；不预填 required_missing。
        def _wcopy():
            return json.loads(json.dumps(_DEFAULT_WITNESS_FACTS,
                                         ensure_ascii=False))

        def _blocked(d, tag):
            pk = EP.build_pack(d)
            vv = EP.verify_pack(os.path.join(d, EP.PACK_NAME))
            ok = (pk["experiment_verdict"]["status"] != "pass"
                  and vv.get("verdict") != "pass")
            detail = "%s/%s" % (pk["experiment_verdict"]["status"],
                                vv.get("verdict"))
            return ok, detail

        # Y1 初始位置为空对象（Doro 原输入形态）
        f = _wcopy()
        f["initial_state"]["initial_position_world_m"] = {
            "value": {}, "source": "x"}
        d = make_run(os.path.join(base, "y1"), crit_kind="bench_subject",
                     scores_format="producer", witness_facts=f)
        okY1, dtY1 = _blocked(d, "Y1")
        necY1 = EP.build_pack(d)["necessary_conditions"]
        okY1 = okY1 and any("initial_position_world_m" in m
                            for m in necY1.get("invalid_facts") or [])
        record("Y1", "初始位置空对象：字段无效点名，构包核验均不通过",
               okY1, dtY1)

        # Y2 控制器生效参数为空对象且无不适用声明（Doro 原输入形态）
        f = _wcopy()
        f["controller"]["effective_parameters"] = {"value": {},
                                                   "source": "x"}
        d = make_run(os.path.join(base, "y2"), crit_kind="bench_subject",
                     scores_format="producer", witness_facts=f)
        okY2, dtY2 = _blocked(d, "Y2")
        pY2 = EP.build_pack(d)
        okY2 = okY2 and any("effective_parameters" in m
                            for m in pY2["necessary_conditions"].get(
                                "invalid_facts") or [])
        record("Y2", "控制器生效参数空对象：不得靠外层存在放行", okY2, dtY2)

        # Y3 初始化/释放/复位事件只有 none 且无范围与理由（Doro 原输入
        # 形态）
        f = _wcopy()
        f["initial_state"]["initialization_and_release_boundary"] = {
            "event": "none"}
        f["physical_constraints"]["pin_release_schedule"] = {"event": "none"}
        f["controller"]["mode_and_reset_state"] = {"event": "none"}
        d = make_run(os.path.join(base, "y3"), crit_kind="bench_subject",
                     scores_format="producer", witness_facts=f)
        okY3, dtY3 = _blocked(d, "Y3")
        pY3 = EP.build_pack(d)
        okY3 = okY3 and any("无理由不适用" in m for m in
                            pY3["necessary_conditions"].get(
                                "invalid_facts") or [])
        record("Y3", "无理由不适用事件：不算已恢复事实，阻断", okY3, dtY3)

        # Y4 初始位置三个空值且来源为空（Doro 原输入形态）
        f = _wcopy()
        f["initial_state"]["initial_position_world_m"] = {
            "value": [None, None, None], "source": ""}
        d = make_run(os.path.join(base, "y4"), crit_kind="bench_subject",
                     scores_format="producer", witness_facts=f)
        okY4, dtY4 = _blocked(d, "Y4")
        pY4 = EP.build_pack(d)
        n_inv = len(pY4["necessary_conditions"].get("invalid_facts") or [])
        okY4 = okY4 and n_inv >= 2
        record("Y4", "null 位置加空来源：数值与来源同时无效阻断", okY4,
               "%s 无效项=%d" % (dtY4, n_inv))

        # Y5 扰动注入标记为字符串 true（Doro 原输入形态）
        f = _wcopy()
        f["inflow"]["perturbation_policy"] = {
            "value": "有扰动", "injected": "true", "source": "x"}
        d = make_run(os.path.join(base, "y5"), crit_kind="bench_subject",
                     scores_format="producer", witness_facts=f)
        okY5, dtY5 = _blocked(d, "Y5")
        pY5 = EP.build_pack(d)
        okY5 = okY5 and any("严格布尔" in m for m in
                            pY5["necessary_conditions"].get(
                                "invalid_facts") or [])
        record("Y5", "扰动真值为字符串 true：类型非法阻断，不被默认吞掉",
               okY5, dtY5)

        # Y6 三平移三转动均锁定（Doro 原输入形态）：不属已批准自由飞行
        f = _wcopy()
        f["physical_constraints"]["body_free_dofs"] = {
            "value": {"tx": False, "ty": False, "tz": False,
                      "rx": False, "ry": False, "rz": False},
            "source": "结构样例：全锁定生成"}
        d = make_run(os.path.join(base, "y6"), crit_kind="bench_subject",
                     scores_format="producer", witness_facts=f)
        okY6, dtY6 = _blocked(d, "Y6")
        pY6 = EP.build_pack(d)
        dof_item = next((i for i in (pY6.get("approved_scenario_comparison")
                                     or {}).get("items") or []
                         if i["item"] == "body_free_dofs"), {})
        okY6 = okY6 and dof_item.get("verdict") == "divergent" \
            and pY6["applicability"]["status"] == "not_applicable"
        record("Y6", "机体六自由度全锁：不属已批准自由飞行，真值保留阻"
                "断", okY6, "%s 适用=%s" % (dtY6,
                                            pY6["applicability"]["status"]))

        # Y7 每个自由度单独锁定均不属已批准自由飞行
        okY7 = True
        dtY7 = []
        for dk in ("tx", "ty", "tz", "rx", "ry", "rz"):
            f = _wcopy()
            vals = {"tx": True, "ty": True, "tz": True,
                    "rx": True, "ry": True, "rz": True}
            vals[dk] = False
            f["physical_constraints"]["body_free_dofs"] = {
                "value": vals, "source": "结构样例：单自由度锁定"}
            d = make_run(os.path.join(base, "y7_" + dk),
                         crit_kind="bench_subject", scores_format="producer",
                         witness_facts=f)
            pk = EP.build_pack(d)
            vv = EP.verify_pack(os.path.join(d, EP.PACK_NAME))
            item = next((i for i in (pk.get("approved_scenario_comparison")
                                     or {}).get("items") or []
                         if i["item"] == "body_free_dofs"), {})
            if not (pk["experiment_verdict"]["status"] != "pass"
                    and vv.get("verdict") != "pass"
                    and item.get("verdict") == "divergent"):
                okY7 = False
                dtY7.append(dk)
        record("Y7", "六个自由度逐一锁定：逐项比较均阻断", okY7,
               "失败自由度=%s" % (dtY7 or "无"))

        # Y8 合法非空正控制：具名钉放事件加全量有效字段 → 通过
        d = make_run(os.path.join(base, "y8"), crit_kind="bench_subject",
                     scores_format="producer")
        pY8 = EP.build_pack(d)
        dof_item8 = next((i for i in (pY8.get("approved_scenario_comparison")
                                      or {}).get("items") or []
                          if i["item"] == "body_free_dofs"), {})
        okY8 = (pY8["necessary_conditions"]["status"] == "complete"
                and not (pY8["necessary_conditions"].get("invalid_facts")
                         or [])
                and dof_item8.get("verdict") == "match"
                and pY8["experiment_verdict"]["status"] == "pass")
        record("Y8", "合法非空正控制：事件与六自由度齐备通过", okY8,
               "判定=%s" % pY8["experiment_verdict"]["status"])

        # Y9 假不适用：控制器不适用声明缺范围与来源 → 无效；合法有界不
        # 适用（带范围理由来源且符合执行模式）→ 事实有效
        f = _wcopy()
        f["controller"]["effective_parameters"] = {
            "value": {}, "not_applicable_reason": "没跑控制器"}
        d = make_run(os.path.join(base, "y9"), crit_kind="bench_subject",
                     scores_format="producer", witness_facts=f)
        okY9a, _ = _blocked(d, "Y9")
        d9b = make_run(os.path.join(base, "y9b"), crit_kind="bench_subject",
                       scores_format="producer")
        p9b = EP.build_pack(d9b)
        okY9b = not any("effective_parameters" in m for m in
                        p9b["necessary_conditions"].get("invalid_facts")
                        or [])
        record("Y9", "假不适用（缺范围来源）无效；合法有界不适用有效",
               okY9a and okY9b, "假=%s 真=%s" % (okY9a, okY9b))

        # Y10 事件 none 带完整理由（结构诊断样例）：事实有效但缺实际初
        # 始化/释放来源——不得作为悬停运行通过
        f = _wcopy()
        f["initial_state"]["initialization_and_release_boundary"] = {
            "event": "none",
            "reason": "人工接口结构链无物理仿真进程，无初始化/释放事件",
            "scope": "结构诊断样例",
            "source": "记录脚本自述"}
        f["physical_constraints"]["pin_release_schedule"] = {
            "event": "none",
            "reason": "结构数据生成无钉住/释放事件",
            "scope": "结构诊断样例",
            "source": "记录脚本自述"}
        d = make_run(os.path.join(base, "y10"), crit_kind="bench_subject",
                     scores_format="producer", witness_facts=f)
        pY10 = EP.build_pack(d)
        vY10 = EP.verify_pack(os.path.join(d, EP.PACK_NAME))
        okY10 = (not any("initialization_and_release_boundary" in m
                         or "pin_release_schedule" in m
                         for m in pY10["necessary_conditions"].get(
                             "invalid_facts") or [])
                 and pY10["applicability"]["status"] == "unknown"
                 and pY10["experiment_verdict"]["status"] != "pass"
                 and vY10.get("verdict") != "pass")
        record("Y10", "结构样例缺物理事件：事实有效但不作悬停运行通过",
               okY10, "适用=%s 判定=%s"
               % (pY10["applicability"]["status"],
                  pY10["experiment_verdict"]["status"]))

        # ── 返修 v6：完整字段契约与真实生产观察接线 ──
        # Z1-Z5 Doro 五例在原始事实上直接重跑；Z6-Z8 同类外层非法与缺
        # 分量对照。均不预填 required_missing。
        def _zcase(name, mut, desc):
            f = json.loads(json.dumps(_DEFAULT_WITNESS_FACTS,
                                      ensure_ascii=False))
            mut(f)
            d = make_run(os.path.join(base, name),
                         crit_kind="bench_subject", scores_format="producer",
                         witness_facts=f)
            pk = EP.build_pack(d)
            vv = EP.verify_pack(os.path.join(d, EP.PACK_NAME))
            inv = pk["necessary_conditions"].get("invalid_facts") or []
            ok = (pk["experiment_verdict"]["status"] != "pass"
                  and vv.get("verdict") != "pass" and inv)
            record(name.upper(), desc, ok,
                   "%s 无效=%s" % (pk["experiment_verdict"]["status"],
                                   inv[:1] or "无"))

        _zcase("z1", lambda f: f["initial_state"].update(
            initial_position_world_m=True),
               "位置字段为裸布尔：外层结构非法阻断")
        _zcase("z2", lambda f: f["initial_state"].update(
            initial_orientation={"value": {}, "source": "s"}),
               "姿态为空对象：表示缺失阻断（all 空集不真）")
        _zcase("z3", lambda f: f["initial_state"].update(
            initial_orientation={"value": {"roll_rad": 0.1},
                                 "source": "s"}),
               "部分欧拉角：缺分量阻断（不过滤后查剩余）")
        _zcase("z4", lambda f: f["controller"].update(
            effective_parameters={"value": "unknown", "source": "s"}),
               "控制参数 unknown 字样：不算实际有效事实")
        _zcase("z5", lambda f: f["initial_state"].update(
            initial_linear_velocity=[0.0, 0.0, 0.0]),
               "裸速度列表无来源：外层非法阻断")
        _zcase("z6", lambda f: f["initial_state"].update(
            initial_orientation={"value": {"quaternion_wxyz": [1.0, 0.0, 0.0]},
                                 "source": "s"}),
               "部分四元数：缺分量阻断")
        _zcase("z7", lambda f: f["physical_constraints"].update(
            body_free_dofs={"value": {"tx": True}, "source": "s"}),
               "自由度只给一分量：缺分量阻断")
        _zcase("z8", lambda f: f["physical_constraints"].update(
            tether_state={"present": "false", "source": "s"}),
               "系留标记为字符串：严格布尔阻断")

        # Z9 如实 unknown 与伪 complete 的区分：前者包一致性核验合法通
        # 过；后者（无效事实写 complete 且实验 pass）必须拒绝。
        f = json.loads(json.dumps(_DEFAULT_WITNESS_FACTS,
                                  ensure_ascii=False))
        f["controller"]["effective_parameters"] = {"value": "unknown",
                                                   "source": "s"}
        d = make_run(os.path.join(base, "z9"), crit_kind="bench_subject",
                     scores_format="producer", witness_facts=f)
        pZ9 = EP.build_pack(d)
        vZ9 = EP.verify_pack(os.path.join(d, EP.PACK_NAME))
        okZ9a = (pZ9["necessary_conditions"]["status"] == "deficient"
                 and pZ9["experiment_verdict"]["status"] == "unknown"
                 and vZ9["pass"])
        docZ9 = json.load(open(os.path.join(d, EP.PACK_NAME),
                               encoding="utf-8"))
        docZ9["necessary_conditions"]["status"] = "complete"
        docZ9["necessary_conditions"]["invalid_facts"] = []
        for c in docZ9.get("criteria", []):
            c["status"] = "pass"
        docZ9["experiment_verdict"] = {"status": "pass",
                                       "reason": "伪 complete"}
        wjson(os.path.join(d, EP.PACK_NAME), docZ9)
        vZ9b = EP.verify_pack(os.path.join(d, EP.PACK_NAME))
        okZ9b = not vZ9b["pass"]
        record("Z9", "如实 unknown 核验合法；伪 complete 加 pass 必拒",
               okZ9a and okZ9b, "如实过=%s 伪拒=%s" % (okZ9a, okZ9b))

        # Z10 真实生产函数隔离调用：挂点字段流、运行身份、时点、缺身份
        # /查询失败/写失败出口（桩对象只供属性读数，证明接口与字段流，
        # 不是实测飞行；不复制物理逻辑）。
        from modules.experiment_pipeline import runtime_observer as RO

        class _StubMav:
            def get_world_poses(self):
                return ([0.0, 0.0, 3.0], [1.0, 0.0, 0.0, 0.0])

            def get_velocities(self):
                return [0.0] * 6

        class _StubAutopilot:
            _ei = 0.0
            _freq_last = 1.55
            _pitch_tgt_filt = None

        class _StubCfg:
            WIND_ENABLED = False
            WIND_VECTOR = (0.0, 0.0, 0.0)
            TETHER_ENABLED = False
            EXPERIMENT_MODE = ""
            class FlightControllerConfig:
                FREQ_HOVER = 1.55

        zdir = os.path.join(base, "z10w")
        os.makedirs(zdir, exist_ok=True)
        keep = {k: os.environ.pop(k, None) for k in
                ("I186_ARCHIVE_DIR", "I186_EXPERIMENT_ID", "I186_RUN_ID")}
        try:
            ok_no_id = RO.observe_initialization(
                _StubMav(), _StubAutopilot(), _StubCfg(), 0.0) is None
            os.environ["I186_ARCHIVE_DIR"] = zdir
            os.environ["I186_EXPERIMENT_ID"] = "exp-self-1"
            os.environ["I186_RUN_ID"] = "run-A"
            RO.observe_initialization(_StubMav(), _StubAutopilot(),
                                      _StubCfg(), 0.008333,
                                      pin_result={k: {"status": "succeeded"}
                                                  for k in ("body_pin", "wing_pin")})
            RO.observe_release(_StubMav(), _StubAutopilot(), _StubCfg(),
                               0.9, {k: {"status": "succeeded"}
                                     for k in RO._RELEASE_ACTIONS},
                               runtime_context={"_SIDE_PULSE_ON": False,
                                                "_I186_TETHER_RESULT": {
                                                    "selected": False,
                                                    "status": "succeeded"}})

            class _Boom:
                def get_world_poses(self):
                    raise RuntimeError("boom")

            zdir2 = os.path.join(base, "z10err")
            os.makedirs(zdir2, exist_ok=True)
            os.environ["I186_ARCHIVE_DIR"] = zdir2
            RO.observe_initialization(_Boom(), _StubAutopilot(),
                                      _StubCfg(), 1.0)
            doc10e = json.load(open(os.path.join(zdir2,
                                                 "scene_witness.json"),
                                    encoding="utf-8"))
            ok_err = "boom" in str(doc10e["facts"]["initial_state"][
                "initial_position_world_m"].get("query_error"))
            os.environ["I186_ARCHIVE_DIR"] = zdir
            doc10 = json.load(open(os.path.join(zdir, "scene_witness.json"),
                                   encoding="utf-8"))
            f10 = doc10["facts"]
            okZ10 = (ok_no_id
                     and doc10["collection_mode"] == "runtime_object"
                     and doc10["experiment_id"] == "exp-self-1"
                     and doc10["run_id"] == "run-A"
                     and f10["initial_state"]["initial_position_world_m"][
                         "value"] == [0.0, 0.0, 3.0]
                     and f10["inflow"]["ambient_flow_world_m_per_s"][
                         "value"] == [0.0, 0.0, 0.0]
                     and f10["physical_constraints"]["body_free_dofs"][
                         "value"]["tx"] is True
                     and f10["controller"]["effective_parameters"][
                         "value"]["state"]["_freq_last"] == 1.55
                     and f10["initial_state"][
                         "initialization_and_release_boundary"]["event"]
                     == "pin_then_release"
                     and doc10["observations"][0]["site"]
                     == "main_sim.py:pin_branch"
                     and doc10["observations"][1]["site"]
                     == "main_sim.py:release_branch"
                     and ok_err)
            bad = os.path.join(base, "z10bad")
            with open(bad, "w"):
                pass
            os.environ["I186_ARCHIVE_DIR"] = bad
            try:
                RO.observe_release(_StubMav(), _StubAutopilot(),
                                   _StubCfg(), 2.0, [])
                okZ10w = True
            except Exception:
                okZ10w = False
            okZ10 = okZ10 and okZ10w
        finally:
            for k, v in keep.items():
                if v is not None:
                    os.environ[k] = v
                else:
                    os.environ.pop(k, None)
        record("Z10", "运行期观察器隔离调用：字段流、身份、时点与缺身份"
                "/查询失败/写失败出口（桩读数不称实测飞行）", okZ10,
               "mode=runtime_object")

        # Z11 源级接线证明：真实生产点存在采集调用并由运行器传递同
        # run 身份（读已提交源码文本核对，非独立见证步或固定 JSON）。
        ms = open(os.path.join(ROOT, "main_sim.py"), encoding="utf-8").read()
        rn = open(os.path.join(ROOT, "modules", "experiment_pipeline",
                               "runner.py"), encoding="utf-8").read()
        okZ11 = ("from modules.experiment_pipeline import" in ms
                 and "runtime_observer as _i186_ro" in ms
                 and "_i186_ro.observe_initialization(" in ms
                 and "_i186_ro.observe_release(" in ms
                 and "I186_ARCHIVE_DIR" in rn
                 and "I186_EXPERIMENT_ID" in rn
                 and "I186_RUN_ID" in rn)
        record("Z11", "生产点接线在源：main_sim 两挂点加运行器身份传递",
               okZ11, "挂点与身份注入均在源码")



    finally:
        shutil.rmtree(base, ignore_errors=True)

    n = sum(1 for r in RESULTS if r["pass"])
    print("汇总：%d/%d" % (n, len(RESULTS)))
    return 0 if n == len(RESULTS) else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--facts-only"]:
        check_fact_contract()
        sys.exit(0 if all(r["pass"] for r in RESULTS) else 1)
    if sys.argv[1:] == ["--runtime-only"]:
        check_fact_contract()
        workroot = os.path.join(ROOT, "logs", "i186_l1_typed_wiring_20261015", "tmp")
        os.makedirs(workroot, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="runtime_", dir=workroot) as base:
            check_runtime_contract(base)
            check_production_receipts(base)
        print("汇总：%d/%d" % (sum(r["pass"] for r in RESULTS), len(RESULTS)))
        sys.exit(0 if all(r["pass"] for r in RESULTS) else 1)
    sys.exit(main())
