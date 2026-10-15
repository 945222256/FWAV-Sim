# -*- coding: utf-8 -*-
"""scene_witness.py — 实际场景事实最小采集入口（事项 I-186 返修 v4）。

用途：把本次运行实际发生的初始化/释放、来流、物理约束与控制器事实在
数据生产侧落档，供证据包按 HQ 批准必要清单（record_id
HQ-I186-HOVER-REQUIRED-20261014-V1）的 condition_definitions 核对。本模
块只登记事实，不与批准场景比较（比较在证据包做），不改真值。

事实由实际执行进程经链步传入（inline JSON），写入归档目录
scene_witness.json 并绑定本次实验与运行身份；构包按身份核对采纳，外来
见证不作本次证据。人工接口结构链如实声明"无物理事件"，不冒充飞行。

用法（链步）：
  python -m modules.experiment_pipeline.scene_witness \
      <归档目录> <experiment_id> <run_id> <事实JSON字符串或文件路径>

仅使用 Python 标准库。
"""
import json
import os
import sys
import time
import copy

WITNESS_NAME = "scene_witness.json"
SCHEMA_VERSION = 1
TOOL = "modules/experiment_pipeline/scene_witness.py"

# 事实节允许的键：与批准清单 condition_definitions 的条件名对应
FACT_SECTIONS = ("initial_state", "inflow", "physical_constraints",
                 "controller")


def write_witness(archive_dir, experiment_id, run_id, facts,
                  collection_mode="producer_declared"):
    """校验并原子写入场景见证，返回写入路径。

    facts 为条件名到字段字典的映射；每节须为对象（字段值可为基础值或
    含 value 与 source 的对象），字段缺失由证据包按清单点名，不在本侧
    预填或代填。
    """
    if not isinstance(facts, dict):
        raise ValueError("facts 必须为 JSON 对象")
    if collection_mode not in ("producer_declared", "runtime_object"):
        raise ValueError("collection_mode 须为 producer_declared 或 "
                         "runtime_object（生产进程自述或实际运行对象采"
                         "集；自述不冒充实测）")
    unknown = sorted(set(facts) - set(FACT_SECTIONS))
    if unknown:
        raise ValueError("facts 含未登记节：%s；只许 %s"
                         % ("、".join(unknown), "、".join(FACT_SECTIONS)))
    for name in facts:
        if not isinstance(facts[name], dict):
            raise ValueError("facts 节 %s 必须为对象" % name)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "written_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "producer": TOOL,
        "collection_mode": collection_mode,
        "experiment_id": experiment_id,
        "run_id": run_id,
        "facts": facts,
    }
    os.makedirs(archive_dir, exist_ok=True)
    path = os.path.join(archive_dir, WITNESS_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


def record_observation(archive_dir, experiment_id, run_id, stage, sections,
                       site="", observed_at_sim_t=None,
                       collection_mode="runtime_object"):
    """运行期观察合并写入（事项 I-186 返修 v6，采集方 runtime_observer）。

    把一个生产时点的实际对象读数合并进归档 scene_witness.json：文档不
    存在则以该身份初始化；sections 为条件名到字段字典的映射，字段级合
    并（后写覆盖同名字段的值，来源随之携带生产位置 site 与仿真时点）。
    collection_mode 固定 runtime_object（与 producer_declared 分列，改
    标签不升级资格）。写失败由调用方记录采集缺失。
    """
    if collection_mode != "runtime_object":
        raise ValueError("record_observation 仅用于 runtime_object 采集")
    path = os.path.join(archive_dir, WITNESS_NAME)
    doc = None
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            doc = None
    if not isinstance(doc, dict)             or str(doc.get("experiment_id")) != str(experiment_id)             or str(doc.get("run_id")) != str(run_id):
        doc = {
            "schema_version": SCHEMA_VERSION,
            "written_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "producer": TOOL,
            "collection_mode": collection_mode,
            "experiment_id": experiment_id,
            "run_id": run_id,
            "facts": {},
            "observations": [],
        }
    doc.setdefault("facts", {})
    doc.setdefault("observations", [])
    if doc.get("collection_mode") not in (None, "runtime_object"):
        doc["collection_mode"] = "%s+runtime_object" % doc["collection_mode"]
    for name, fields in (sections or {}).items():
        if name not in FACT_SECTIONS or not isinstance(fields, dict):
            continue
        doc["facts"].setdefault(name, {})
        doc["facts"][name].update(fields)
    doc["observations"].append({
        "stage": stage, "site": site,
        "observed_at_sim_t": observed_at_sim_t,
        "wall_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sections": sorted((sections or {}).keys()),
        # Keep the actual value at each observation, not only the latest merged
        # field. A release observation must not erase the pinned-state evidence.
        "facts": copy.deepcopy(sections or {}),
    })
    os.makedirs(archive_dir, exist_ok=True)
    tmp = path + ".obs.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 4:
        print(__doc__)
        return 2
    archive_dir, experiment_id, run_id, facts_arg = argv
    if facts_arg.startswith("json:"):
        facts = json.loads(facts_arg[len("json:"):])
    elif os.path.isfile(facts_arg):
        with open(facts_arg, encoding="utf-8") as fh:
            facts = json.load(fh)
    else:
        facts = json.loads(facts_arg)
    path = write_witness(archive_dir, experiment_id, run_id, facts)
    print("[WITNESS] 场景见证已写入 %s（实验 %s 运行 %s）"
          % (path, experiment_id, run_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
