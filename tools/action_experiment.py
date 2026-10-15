"""可重复使用的单变量动作实验入口。

plan 先冻结配方和实际源码；run 只用统一入口实际双跑；analyze 只读离线
证据并执行同一配方判据。无模拟数据回退，无采集缓存，无后验容差。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.experiment_pipeline.action_recipe import (CASE_ENV, compile_plan,
    compare_adopted, read_json, write_json)
from modules.experiment_pipeline.action_analysis import (ANALYSIS_VERSION,
    file_hash, qualify_run)


def source_files(recipe):
    """保守冻结现有配置/模块闭包和当前构型；不复制整个仓库。"""
    files = {ROOT / "main_sim.py", Path(__file__).resolve(),
             ROOT / "tools/checks/action_experiment_acceptance.py", ROOT / "tools/checks/test_action_experiment.py"}
    for folder in ("modules", "config"):
        files.update(p for p in (ROOT / folder).rglob("*.py") if "__pycache__" not in p.parts)
    files.update(p for p in (ROOT / "assets/flapper").rglob("*") if p.is_file())
    files.update(ROOT / p for p in recipe["source_refs"])
    return sorted(files)


def make_plan(recipe_path, output):
    recipe = read_json(recipe_path)
    plan = compile_plan(recipe)
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("计划目录须为空；冻结计划不能被覆盖或挪作另一轮")
    output.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for file in source_files(recipe):
        file = file.resolve()
        if not file.is_file() or not file.is_relative_to(ROOT):
            raise ValueError("源码或依据缺失/越出项目：%s" % file)
        relative = file.relative_to(ROOT).as_posix()
        hashes[relative] = file_hash(file)
        destination = output / "source" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, destination)
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
    # Raw source copies include uncommitted changes; HEAD alone is not identity.
    plan["source"] = {"head": git.stdout.strip(), "files_sha256": hashes,
                      "snapshot_dir": "source", "includes_uncommitted_source": True}
    import ast
    tree = ast.parse((ROOT / "modules/flight_recorder.py").read_text(encoding="utf-8"))
    headers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "writerow" and len(node.args) == 1:
            try:
                value = ast.literal_eval(node.args[0])
                if isinstance(value, list) and len(value) == 93 and all(type(v) is str for v in value):
                    headers.append(value)
            except (ValueError, TypeError):
                pass
    if len(headers) != 1:
        raise ValueError("无法从当前生产声明唯一固定完整 CSV 字段")
    plan["csv_columns"] = headers[0]
    plan["execution_id"] = recipe["recipe_id"] + "_" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6]
    plan["frozen_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    plan["measurement_contract"] = {
        "version": ANALYSIS_VERSION,
        "capture": "settle+measure+one guard physics step; raw guard step retained",
        "window": "(settle,settle+measure], no post-hoc window selection",
        "time": "actual World dt and unique active native PhysX counter deltas; never wall clock, World accumulated time or aero callback time",
        "tracking": "current measured joints minus previous drive float32 targets, same complete joint-name order",
        "force": "successful PhysX force/torque API float32 submissions, world frame; deferred submissions counted only when actually submitted",
        "impulse": "sum successful submitted load * actual World dt for exactly bound next native solver interval; not force-sensor or momentum measurement",
        "jig": "software six-DOF reset each main step; model input state checked separately; no claim of continuous holonomic constraint",
        "repeat": "whole CSV bytes, all FW channels/body bytes, complete scientific event payloads; only run identity metadata differs",
        "capability": "no tracking performance pass without a frozen numeric threshold",
    }
    from modules.experiment_pipeline.action_recipe import canonical_hash
    plan["plan_sha256"] = canonical_hash(plan)
    for case in plan["cases"]:
        write_json(output / "cases" / (case["case"] + ".json"), case)
    write_json(output / "plan.json", plan)
    print(json.dumps({"plan": str(output / "plan.json"), "plan_sha256": plan["plan_sha256"],
                      "cases_hz": {c["case"]: c["frequency_hz"] for c in plan["cases"]}}, ensure_ascii=False))
    return plan


def load_plan(directory, live_source=False):
    from modules.experiment_pipeline.action_recipe import canonical_hash
    directory = Path(directory).resolve()
    plan = read_json(directory / "plan.json")
    claimed = plan.pop("plan_sha256", None)
    if claimed != canonical_hash(plan):
        raise ValueError("冻结计划摘要不符")
    plan["plan_sha256"] = claimed
    expected = compile_plan(plan["recipe"])
    for key, value in expected.items():
        if plan.get(key) != value:
            raise ValueError("冻结计划与原配方不符：%s" % key)
    for case in plan["cases"]:
        if read_json(directory / "cases" / (case["case"] + ".json")) != case:
            raise ValueError("运行输入文件与冻结计划不符")
    for relative, digest in plan["source"]["files_sha256"].items():
        source = directory / "source" / relative
        if not source.is_file() or file_hash(source) != digest:
            raise ValueError("归档源码不完整或被改动：%s" % relative)
        analysis_dependencies = (relative.startswith("modules/experiment_pipeline/")
                                 or relative in ("modules/fw_iid_ledger.py", "modules/experiment_contract.py", "tools/action_experiment.py"))
        if (live_source or analysis_dependencies) and (not (ROOT / relative).is_file() or file_hash(ROOT / relative) != digest):
            raise ValueError("实际运行源码已偏离事前冻结：%s" % relative)
    return plan


def preflight(directory):
    plan = load_plan(directory, live_source=True)
    import config.sim_config as cfg
    from modules.aero import MODEL_REGISTRY
    from modules.sim_entry import ISAAC_PY, detect_isaac_processes
    errors = []
    if not Path(ISAAC_PY).is_file():
        errors.append("统一入口的真实 Isaac 启动器不存在")
    for key in ("FLAPPER_URDF_USD", "FLAPPER_URDF_PATH"):
        if not Path(getattr(cfg, key)).is_file():
            errors.append("实际构型文件不存在：" + key)
    for case in plan["cases"]:
        if not cfg.MANUAL_FREQ_MIN <= case["frequency_hz"] <= cfg.MANUAL_FREQ_MAX:
            errors.append("配方频率超出实际消费者输入范围")
        if case["fixed"]["aero_model"] not in MODEL_REGISTRY:
            errors.append("气动模型未登记，禁止隐式回退")
        if case["sampling"]["physics_dt_s"] != cfg.PHYSICS_DT:
            errors.append("配方步长与真实入口编译期步长不符")
        if list(cfg.INITIAL_POSITION) != case["fixed"]["initial_position_world_m"]:
            errors.append("配方初始位置与加载器实际采用值不符；不在环境桥事后冒改初态")
    # Check real integration, not just an importable helper declaration.
    bridge = (ROOT / "modules/sim_env_bridge.py").read_text(encoding="utf-8")
    if "prepare_runtime_input(" not in bridge:
        errors.append("生产环境桥尚未接入固定配方输入与实际运行身份")
    if os.environ.get("PYTHONUTF8") != "1":
        errors.append("进程探测须在 PYTHONUTF8=1 下执行，防止 PowerShell 输出解码失败")
    busy, method = detect_isaac_processes()
    if busy:
        errors.append("已有 Isaac 实例；不启动并行实例")
    result = {"ready": not errors, "errors": errors, "instance_probe": method,
              "existing_processes": [{k: p.get(k) for k in ("Name", "ProcessId")} for p in busy],
              "plan_sha256": plan["plan_sha256"]}
    write_json(Path(directory) / "preflight.json", result)
    return result


def find_ledger(tag):
    from modules.fw_iid_ledger import load
    # File names truncate tags. Header identity, not a matching file-name prefix,
    # is the final selector; ambiguous matches are errors.
    prefix = "".join(c if c.isalnum() or c in "_-" else "_" for c in tag)[:48]
    matches = []
    for file in (ROOT / "logs").glob("fw_ledger_" + prefix + "_*.bin"):
        header, _ = load(file)
        if header.get("tag") == tag:
            matches.append(file)
    if len(matches) != 1:
        raise ValueError("当前运行的原值台账缺失或不唯一：%s" % tag)
    return matches[0]


def run_plan(directory, timeout_s=420):
    directory = Path(directory).resolve()
    plan = load_plan(directory, live_source=True)
    check = preflight(directory)
    if not check["ready"]:
        raise ValueError("；".join(check["errors"]))
    if (directory / "acquisition.json").exists():
        raise ValueError("本计划已有采集记录；不覆盖、不用缓存替代双跑；另建完整计划")
    from modules.sim_entry import launch_sim
    acquisition = {"plan_sha256": plan["plan_sha256"], "cases": [], "completed": False}
    write_json(directory / "acquisition.json", acquisition)
    for case in plan["cases"]:
        load_plan(directory, live_source=True)
        sampling = case["sampling"]
        env = {CASE_ENV: str(directory / "cases" / (case["case"] + ".json")),
               "FLAPPER_LEDGER": "1", "FLAPPER_I210_EVIDENCE": "1",
               "FLAPPER_OBSTACLE_COUNT": "0",
               "FLAPPER_MAX_S": repr(sampling["settle_s"] + sampling["measure_s"] + sampling["physics_dt_s"])}
        print("实际采集 %s %.12g Hz，统一入口 A/B" % (case["case"], case["frequency_hz"]), flush=True)
        verdict = launch_sim(env, plan["execution_id"] + "_" + case["case"],
                             "I-187 固定配方全局拍频 " + case["case"],
                             sampling["settle_s"] + sampling["measure_s"] + sampling["physics_dt_s"],
                             str(directory / "run_logs"), timeout_s=timeout_s)
        entry = {"case": case["case"], "unified_entry": verdict, "artifacts": []}
        acquisition["cases"].append(entry)
        for run in verdict["runs"]:
            if not run.get("milestone"):
                continue
            ledger = find_ledger(run["tag"])
            condition = directory / "runtime" / run["tag"] / (run["tag"] + ".conditions.json")
            paths = [ledger, Path(str(ledger) + ".i210.jsonl"), condition,
                     Path(run["csv"]), ROOT / run["log"], Path(str(run["csv"]) + ".meta.json")]
            archive = directory / "raw" / run["tag"]
            archive.mkdir(parents=True, exist_ok=True)
            archived = {}
            for key, path in zip(("ledger", "events", "conditions", "csv", "log", "csv_meta"), paths):
                if not path.is_file():
                    raise ValueError("采集后必需证据缺失：%s" % path)
                target = archive / path.name
                shutil.copy2(path, target)
                archived[key] = str(target)
            entry["artifacts"].append({"tag": run["tag"], **archived})
        write_json(directory / "acquisition.json", acquisition)
        load_plan(directory, live_source=True)
        if not verdict["ok"]:
            print(verdict["reason"], flush=True)
            return acquisition
    acquisition["completed"] = True
    write_json(directory / "acquisition.json", acquisition)
    return acquisition


def analyze_plan(directory):
    directory = Path(directory).resolve()
    plan = load_plan(directory)
    acquisition = read_json(directory / "acquisition.json")
    if acquisition.get("plan_sha256") != plan["plan_sha256"]:
        raise ValueError("采集未绑定当前冻结计划")
    report = {"analysis_version": ANALYSIS_VERSION, "plan_sha256": plan["plan_sha256"],
              "run_completed": acquisition.get("completed") is True, "data_valid": False,
              "experiment_pass": False, "capability_pass": None, "cases": [], "errors": []}
    fixed_reference = None
    motion_reference = None
    entries = acquisition.get("cases", [])
    if [e.get("case") for e in entries] != plan["case_order"]:
        report["errors"].append("四档顺序或完整范围不符")
    for case, entry in zip(plan["cases"], entries):
        if case["case"] != entry.get("case"):
            continue
        result = {"case": case["case"], "frequency_hz": case["frequency_hz"], "runs": [], "repeat_pass": False, "errors": []}
        report["cases"].append(result)
        runs = entry.get("unified_entry", {}).get("runs", [])
        artifacts = entry.get("artifacts", [])
        if len(runs) != 2 or len(artifacts) != 2:
            result["errors"].append("规定的 A/B 真实采集或完整档案缺失")
            continue
        for run, paths in zip(runs, artifacts):
            if paths.get("tag") != run.get("tag"):
                result["errors"].append("原档与运行顺序不符")
                continue
            try:
                normalized = dict(run, csv=paths["csv"], log=paths["log"])
                measured = qualify_run(normalized, case, plan["recipe"]["qualification"], paths["conditions"], paths["ledger"], plan["csv_columns"])
                result["runs"].append(measured)
                params = measured["actual_effective_parameters"]
                if fixed_reference is None:
                    fixed_reference = params
                if compare_adopted(fixed_reference, params):
                    result["errors"].append("完整实际参数与基准不同，变化超出批准输入")
                motion = measured.get("fixed_motion_readback")
                if motion_reference is None:
                    motion_reference = motion
                if compare_adopted(motion_reference, motion):
                    result["errors"].append("实际幅值、扭转滞后或门控与基准不同")
            except (ValueError, KeyError, OSError, TypeError) as exc:
                result["errors"].append(str(exc))
        if len(result["runs"]) == 2:
            a, b = result["runs"]
            result["repeat_pass"] = (file_hash(artifacts[0]["csv"]) == file_hash(artifacts[1]["csv"])
                and a["ledger_body_sha256"] == b["ledger_body_sha256"]
                and a["science_payload_sha256"] == b["science_payload_sha256"]
                and a["effective_parameters_sha256"] == b["effective_parameters_sha256"]
                and a["data_valid"] and b["data_valid"])
    report["data_valid"] = (not report["errors"] and len(report["cases"]) == 4
        and all(not c["errors"] and len(c["runs"]) == 2 and all(r["data_valid"] for r in c["runs"]) for c in report["cases"]))
    report["experiment_pass"] = (report["run_completed"] and report["data_valid"]
                                 and all(c["repeat_pass"] and all(r["tracking"]["status"] != "fail" for r in c["runs"]) for c in report["cases"]))
    if report["experiment_pass"]:
        baseline = report["cases"][0]["runs"][0]["impulse"]["six_components"]
        for c in report["cases"]:
            c["impulse_delta_from_baseline"] = [a - b for a, b in zip(c["runs"][0]["impulse"]["six_components"], baseline)]
    measurements = [r for c in report["cases"] for r in c["runs"]]
    complete = len(measurements) == 8
    def criterion(number, name, passed, value, unit, threshold, source):
        return {"id": "I-187.C%d" % number, "name": name,
                "status": "pass" if passed else ("fail" if measurements else "unknown"),
                "measured_value": value, "unit": unit, "threshold": threshold, "source": source}
    report["criteria"] = [
        criterion(1, "采集前固定配方与定义", True, plan["recipe_sha256"], "sha256", plan["recipe_sha256"], "plan.json; source/; case files"),
        criterion(2, "实际单变量与固定条件", complete and all(not c["errors"] for c in report["cases"]) and all(not r["errors"] for r in measurements),
                  [c["errors"] for c in report["cases"]], "difference list", [], "runtime conditions and actual drive/aero payloads"),
        criterion(3, "四档完整采集与六分量作用量", report["data_valid"], len(measurements), "qualified run count", 8, "raw/; per-run qualifications and impulse"),
        criterion(4, "跟踪误差按预先规则报告", complete and report["data_valid"] and all(r["tracking"]["status"] != "fail" for r in measurements),
                  [r["tracking"]["max_abs_rad"] for r in measurements], "rad", plan["recipe"]["qualification"]["tracking_error_limit_rad"], "plan.recipe.qualification; raw actual and preceding targets"),
        criterion(5, "完整双跑与离线重算", report["data_valid"] and all(c["repeat_pass"] for c in report["cases"]),
                  [c["repeat_pass"] for c in report["cases"]], "four boolean results", [True] * 4, "whole CSV bytes; full FW body; complete scientific event payloads; deterministic result hash"),
        criterion(6, "结果卡和复跑接口", report["experiment_pass"], report["experiment_pass"], "boolean", True, "result.json; result_card.md; tools/action_experiment.py"),
    ]
    from modules.experiment_pipeline.action_recipe import canonical_hash
    report["result_sha256"] = canonical_hash(report)
    return report


def write_report(directory, report):
    directory = Path(directory)
    write_json(directory / "result.json", report)
    lines = ["# 单变量动作实验结果卡", "", "实验方法判据：%s；运行完成：%s；数据有效：%s。" %
             ("通过" if report["experiment_pass"] else "未通过", report["run_completed"], report["data_valid"]),
             "", "| 档位 | 实际请求 Hz | 数据与双跑 | 六分量 API 提交量积分 |", "|---|---:|---|---|"]
    for case in report["cases"]:
        measurement = case["runs"][0] if case["runs"] else {}
        lines.append("| %s | %.12g | %s | %s |" % (case["case"], case["frequency_hz"],
                     "通过" if case["repeat_pass"] else "未通过", measurement.get("impulse", {}).get("six_components")))
    lines += ["", "跟踪误差无批准阈值时只报告实测；方法通过不代表悬停、控制权限或机体动力学能力通过。",
              "力与力矩为世界系模型向 PhysX 的 API 提交量，单位 N·s 和 N·m·s；并非测力计实测。",
              "机体使用逐步软件重置台架，步内运动与气动模型输入分别留证；不视为连续刚性约束。",
              "", "详细逐量定义、资格失败原因、原始文件及内容摘要见 result.json；冻结输入与源码见 plan.json 和 source/。"]
    for c in report["cases"]:
        errors = c["errors"] + [x for r in c["runs"] for x in r["errors"] + r["clock_errors"]]
        if errors:
            lines += ["", c["case"] + "：" + "；".join(dict.fromkeys(errors))]
    (directory / "result_card.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def verify_saved_result(directory):
    directory = Path(directory).resolve()
    saved = read_json(directory / "result.json")
    fresh = analyze_plan(directory)
    if saved != fresh or fresh != analyze_plan(directory):
        raise ValueError("已保存结果与当前原档重算不一致")
    if not (directory / "result_card.md").is_file():
        raise ValueError("短结果卡缺失")
    if not fresh["experiment_pass"] or any(c["status"] != "pass" for c in fresh["criteria"]):
        raise ValueError("原六条判据尚未全部通过")
    return fresh


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("recipe")
    p.add_argument("--output", required=True)
    for command in ("preflight", "run", "analyze"):
        p = sub.add_parser(command)
        p.add_argument("directory")
        if command == "run":
            p.add_argument("--timeout-s", type=int, default=420)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            make_plan(args.recipe, args.output)
        elif args.command == "preflight":
            check = preflight(args.directory)
            print(json.dumps(check, ensure_ascii=False))
            return 0 if check["ready"] else 2
        elif args.command == "run":
            run_plan(args.directory, args.timeout_s)
        else:
            result = analyze_plan(args.directory)
            second = analyze_plan(args.directory)
            if result != second:
                raise ValueError("相同输入离线重算结果不一致")
            write_report(args.directory, result)
            print(json.dumps({k: result[k] for k in ("run_completed", "data_valid", "experiment_pass", "result_sha256")}, ensure_ascii=False))
            return 0 if result["experiment_pass"] else 2
    except (ValueError, OSError, KeyError) as exc:
        print("拒绝：" + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
