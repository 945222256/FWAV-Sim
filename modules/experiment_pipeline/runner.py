# -*- coding: utf-8 -*-
"""runner.py 声明式实验管线统一运行器，事项 I-015。

用法：PYTHONUTF8=1 python -m modules.experiment_pipeline.runner 配置路径

目标形态：实验等于一份配置文件，统一运行器执行，同配置同摘要，改配置
即换摘要。

流程共八步。

第一步 读配置文件并按 config_schema 校验，校验不通过即拒绝运行，退出
码为二。

第二步 计算配置摘要，打印完整六十四位十六进制串与十二位短标识。

第三步 建归档目录，目录名为 logs/experiments/摘要前十二位_时间戳。

第四步 组装子进程环境。先清空继承环境里全部 FLAPPER_ 前缀变量，防止
外层环境的非声明配置混入；再依次注入 PYTHONUTF8、标准锁五变量、标注
两变量、参数节全部键值。锁五变量取值以
modules/backend/bench_runner.py 的 STANDARD_LOCK 为唯一来源，启动前与
模式文件的变量名清单核对一致，不一致即拒绝运行。

第五步 按场景节命令启动子进程，工作目录为仓库根，标准输出与标准错
误合流写入归档目录内 run.log。

第六步 判定完成性。isaac 模式以运行日志内的收尾里程碑文本为准，里程
碑为同时出现 [RUNCAP] 与 正常收尾退出 两段文本，退出码仅作参考登记；
此规则继承事项 I-025 登记的解释器启动脚本退出码不可靠缺陷。python 模
式的纯 Python 工具退出码可靠，以退出码零判完成，若日志同时含收尾里
程碑文本则一并登记为证据。运行日志含主循环异常标记 [LOOP-CRASH] 时
登记为失守证据。

第七步 扫描 logs 目录，把本次运行期间新出现且归属本次运行的产物复制
进归档目录，逐件登记原路径、归档名、类别、字节数与文件 sha256。数据
流的归属核对以 logs/ident_latest.txt 指针及其旁挂元数据的标注为准，
标注与本次实验名不符的数据流不归档并登记原样证据。

第八步 在归档目录写 run_record.json，含配置全文、配置文件摘要、配置
摘要、命令、注入环境、产物清单、完成性结论与时间信息。

isaac 模式启动前检测既有 Isaac 进程，检测到即拒绝启动并退出，绝不并
行第二个实例，也绝不结束任何进程。两种模式都有运行墙钟上限，超时不
结束进程，只记录超时并按未完成归档。退出码约定：零为完成，一为未完
成或超时，二为拒绝启动。

链节为可选节，事项 I-078。含链节时按步骤顺序执行：isaac 步骤为采集
步，python 步骤为离线处理步，命令词支持三个占位符并在运行时替换，占
位符为采集数据流路径、实验名、归档目录。任一步骤未完成即停止链。每
次执行结束向 logs/experiments/registry.jsonl 追加一行登记。

星标规则，事项 I-104，机主纪律：成功或有重要意义的采样文件名带全角
星。判据通过加两颗全角星，判据信号取拟合报告的留出验证或步骤日志的
基准通过行；配置可选节 importance 为真且判据未过时加一颗。星标在归
档采样类产物时自动加于归档名前缀，原始产物不重命名，既有证据路径不
受影响。半角星号为系统禁用字符，配置校验全库禁用，星标一律全角。

本运行器不搬移不修改 logs 下的原始产物，归档为复制；指针文件
ident_latest.txt 保持原位原义。仅使用 Python 标准库与同仓库的
modules.backend.bench_runner。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from modules.backend.bench_runner import (  # noqa: E402
    LOOP_CRASH_MARK,
    MILESTONE_CAP_DONE,
    MILESTONE_RUN_CAP,
    STANDARD_LOCK,
    detect_isaac_processes,
)
from modules.experiment_pipeline import config_schema, fit_diag, hashing  # noqa: E402

LOGS_DIR = os.path.join(_REPO_ROOT, "logs")
ARCHIVE_ROOT = os.path.join(LOGS_DIR, "experiments")
RUN_LOG_NAME = "run.log"
RECORD_NAME = "run_record.json"
IDENTITY_NAME = "identity.json"
IDENT_POINTER_NAME = "ident_latest.txt"
REGISTRY_NAME = "registry.jsonl"

# 运行墙钟上限：下限沿用三维在环确认文档登记的二十五分钟外部上限惯
# 例；时长较长的实验按每秒仿真二十五秒墙钟放宽。
WALL_CAP_MIN_S = 1500.0
WALL_CAP_PER_DURATION_S = 25.0

# 收尾里程碑出现后等待进程自行退出的宽限期，超过即按里程碑判定完成，
# 进程留给操作系统自行收尾。
MILESTONE_EXIT_GRACE_S = 120.0

POLL_INTERVAL_S = 2.0


def _now_stamp():
    """返回归档目录名使用的时间戳文本。"""
    return time.strftime("%Y%m%d_%H%M%S")


def _scan_log_files():
    """扫描 logs 目录下全部文件，返回路径到二元组的字典。

    二元组为字节数与修改时间。归档根目录 logs/experiments 及其下层不
    计入，避免运行器自身的归档物被当作新产物。
    """
    archive_abs = os.path.abspath(ARCHIVE_ROOT)
    found = {}
    for dirpath, dirnames, filenames in os.walk(LOGS_DIR):
        here = os.path.abspath(dirpath)
        if here == archive_abs or here.startswith(archive_abs + os.sep):
            dirnames[:] = []
            continue
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                stat = os.stat(path)
                found[path] = (stat.st_size, stat.st_mtime)
            except OSError:
                found[path] = (None, None)
    return found


def _python_child_env():
    """python 步骤的子进程环境：清空全部 FLAPPER 前缀变量防止串扰，只
    注入编码变量。拟合类工具对运行环境的要求即到此为止。"""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(config_schema.ENV_PREFIX)}
    env["PYTHONUTF8"] = "1"
    return env


def _find_collect_csv(tag, not_before):
    """按标注定位采集步骤产出的数据流，返回绝对路径或空值。

    以旁挂元数据的标注核对，取修改时间不早于本次运行起点的最新一份；
    不读指针文件，指针在并行施工下有竞态，教训登记于事项 I-038。
    """
    best_path = None
    best_mtime = -1.0
    try:
        names = os.listdir(LOGS_DIR)
    except OSError:
        return None
    for name in names:
        if not name.startswith("ident_stream_") \
                or not name.endswith(".csv.meta.json"):
            continue
        meta_path = os.path.join(LOGS_DIR, name)
        try:
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            continue
        if str(meta.get("tags") or "").strip() != tag:
            continue
        csv_path = meta_path[:-len(".meta.json")]
        try:
            mtime = os.path.getmtime(csv_path)
        except OSError:
            continue
        if mtime < not_before - 1.0 or mtime <= best_mtime:
            continue
        best_path = csv_path
        best_mtime = mtime
    return best_path


def _substitute_argv(argv, collect_csv, tag, archive_dir,
                     experiment_id=None, run_id=None):
    """替换命令词里的五个占位符：采集数据流路径、实验名、归档目录、
    本次实验标识、本次运行标识。

    占位符可独占一词，也可作为词内片段出现（如 {archive_dir}/x.csv，
    事项 I-186 返修 v2：分析步的数据流路径随归档目录拼接）。"""
    values = {
        "{collect_csv}": collect_csv or "",
        "{tag}": tag,
        "{archive_dir}": archive_dir,
        "{experiment_id}": experiment_id or "",
        "{run_id}": run_id or "",
    }
    out = []
    for word in argv:
        for ph, val in values.items():
            if ph in word:
                word = word.replace(ph, val)
        out.append(word)
    return out


def _append_registry(record):
    """实验登记表追加一行登记。登记表为流水账，一行一次执行，事项 I-078
    起为运行器自动维护，人类与工具都可追每一次实验的下落。"""
    line = {
        "started_at": record.get("started_at"),
        "experiment_id": record.get("experiment_id"),
        "run_id": record.get("run_id"),
        "item": record.get("item"),
        "digest": record.get("digest"),
        "digest_short12": record.get("digest_short12"),
        "tag": (record.get("env_applied") or {}).get("FLAPPER_TAG"),
        "config_path": record.get("config_path"),
        "archive_dir": record.get("archive_dir"),
        "complete": record["completeness"]["complete"],
        "stars": record.get("stars"),
        "steps": [{"name": s["name"], "mode": s["mode"],
                   "complete": s["complete"], "wall_s": s["wall_s"]}
                  for s in record.get("steps", [])],
    }
    os.makedirs(ARCHIVE_ROOT, exist_ok=True)
    with open(os.path.join(ARCHIVE_ROOT, REGISTRY_NAME), "a",
              encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


def _build_identity(config, digest, short, archive_dir):
    """生成本次运行的实验、事项、运行与基准身份（事项 I-186）。

    实验标识由配置摘要派生（实验等于一份配置，同配置同摘要即同实验）；
    运行标识为归档目录名（一次执行唯一）；事项与基准取配置声明，未声
    明记原因不虚构。身份在链步骤启动前写入归档目录 identity.json，供
    分析步与证据包直接读取。
    """
    run_name = os.path.basename(archive_dir)
    item_declared = config.get("item")
    baseline_declared = config.get("baseline")
    identity = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "experiment_id": "exp-%s" % short,
        "run_id": run_name,
        "item_id": item_declared if isinstance(item_declared, str)
        and item_declared.strip() else None,
        "item_id_reason": None if item_declared else
        "配置未声明 item 节，本次实验不虚构事项归属",
        "baseline_id": baseline_declared if isinstance(baseline_declared, str)
        and baseline_declared.strip() else None,
        "baseline_id_reason": None if baseline_declared else
        "配置未声明 baseline 节，本次非基准对比运行，不适用",
        "runner_item": "I-015",
        "derivation": {
            "experiment_id": "exp- 加配置摘要十二位短标识（同配置同实验）",
            "run_id": "归档目录名（一次执行唯一）",
            "item_id": "配置 item 节声明",
            "baseline_id": "配置 baseline 节声明",
        },
    }
    path = os.path.join(archive_dir, IDENTITY_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(identity, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return identity


def _find_fit_report(tag, not_before):
    """按标注定位拟合步产出的报告文件，返回绝对路径或空值。

    拟合产物目录名为标注加时间戳，逐目录核对修改时间，取不早于本次
    运行起点的最新一份。
    """
    root = os.path.join(LOGS_DIR, "ident_matrix")
    try:
        entries = os.listdir(root)
    except OSError:
        return None
    best = None
    for name in entries:
        if not name.startswith(tag + "_"):
            continue
        rp = os.path.join(root, name, "report.md")
        try:
            mt = os.path.getmtime(rp)
        except OSError:
            continue
        if mt >= not_before - 1.0 and (best is None or mt > best[1]):
            best = (rp, mt)
    return best[0] if best else None


def _parse_fit_metrics(report_path):
    """从拟合报告提取对照指标：三通道净比值、留出验证判定、饱和占比。

    提取为尽力而为，报告行缺失时对应指标留空，不影响汇总表产出。
    """
    out = {}
    try:
        with open(report_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return out
    lines = text.splitlines()
    for ch in ("wx", "wy", "wz"):
        for line in lines:
            s = line.strip()
            if s.startswith("- %s 净比值" % ch):
                try:
                    out["net_" + ch] = float(s.split()[-1])
                except ValueError:
                    pass
                break
    for line in lines:
        s = line.strip()
        if s.startswith("## 留出验证"):
            out["holdout"] = s.split("：", 1)[-1]
            break
    for line in lines:
        if "饱和占比" in line:
            out["saturation"] = line.strip()
            break
    return out


def _write_family_summary(config, digest, started_text, started_at,
                          steps_exec, archive_dir):
    """配置族汇总对照表，事项 I-083。链含两个及以上采集步时自动产
    出：每个采集步与其后首个拟合步配对成行，指标从拟合报告提取。"""
    rows = []
    cur = None
    for st in steps_exec:
        if st["mode"] == "isaac":
            cur = {"collect": st, "tag": st.get("tag"),
                   "csv": st.get("collect_csv"), "fit": None,
                   "metrics": {}, "fit_report": None}
            rows.append(cur)
        elif cur is not None and cur["fit"] is None:
            cur["fit"] = st
            rp = _find_fit_report(cur.get("tag")
                                 or config["tags"]["name"], started_at)
            if rp:
                cur["fit_report"] = rp
                cur["metrics"] = _parse_fit_metrics(rp)
    md = ["# 配置族汇总对照表（事项 I-083，运行器自动生成）", ""]
    md.append("实验名 %s，配置摘要 %s，执行起点 %s"
              % (config["tags"]["name"], digest, started_text))
    md.append("")
    md.append("| 采集步 | 标注 | 采集 | 数据流 | 拟合 | wx净比 | wy净比"
              "| wz净比 | 留出验证 |")
    md.append("|---|---|---|---|---|---|---|---|---|")
    for row in rows:
        c = row["collect"]
        f = row["fit"]
        m = row["metrics"]
        md.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            c["name"], row.get("tag") or "-",
            "完成" if c["complete"] else "未完成",
            os.path.basename(row["csv"]) if row.get("csv") else "-",
            "未执行" if f is None else
            ("完成" if f["complete"] else "未完成"),
            m.get("net_wx", "-"), m.get("net_wy", "-"),
            m.get("net_wz", "-"), m.get("holdout", "-")))
    for row in rows:
        if row.get("metrics", {}).get("saturation"):
            md.append("")
            md.append("%s %s" % (row["collect"]["name"],
                                 row["metrics"]["saturation"]))
            break
    for row in rows:
        rp = row.get("fit_report")
        if not rp:
            continue
        try:
            with open(rp, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        parsed = fit_diag.parse_report(text)
        if parsed.get("holdout_verdict") != "不过":
            continue
        diag = fit_diag.diagnose(parsed)
        row["diag"] = diag
        md.append("")
        md.append(fit_diag.render_block(row["collect"]["name"],
                                        parsed, diag))
    md_path = os.path.join(archive_dir, "family_summary.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")
    data = {"config_tag": config["tags"]["name"], "digest": digest,
            "rows": [{"collect_step": r["collect"]["name"],
                      "tag": r.get("tag"),
                      "collect_complete": r["collect"]["complete"],
                      "csv": r.get("csv"),
                      "fit_complete": (r["fit"] or {}).get("complete"),
                      "fit_report": r.get("fit_report"),
                      "metrics": r.get("metrics", {}),
                      "diag": r.get("diag")} for r in rows]}
    json_path = os.path.join(archive_dir, "family_summary.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return md_path


def _file_sha256(path):
    """返回文件内容的 sha256 十六进制串。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_kind(name):
    """按文件名判产物类别，用于产物清单与汇报。"""
    if "ident_stream" in name:
        return "九十三列数据流"
    if name.endswith(".meta.json"):
        return "数据流元数据"
    if "fw_ledger" in name:
        return "台账二进制"
    return "其他新产物"


def _archive_name(path):
    """把 logs 下的相对路径折算为归档目录内的唯一文件名。"""
    relative = os.path.relpath(path, LOGS_DIR)
    return relative.replace(os.sep, "__").replace("/", "__")


# 星标规则，事项 I-104，机主纪律：成功或有重要意义的采样文件名带全角
# 星，一眼分辨金子与沙子。判据通过加两颗全角星，有重要意义加一颗；
# 半角星号为系统禁用字符，任何场合不得使用，星标一律全角。
STAR_PASS = "★★"
STAR_IMPORTANT = "★"
SAMPLE_KINDS = ("九十三列数据流", "数据流元数据")


def _star_prefix(kind, stars):
    """按类别与星数给归档名取全角星前缀，不适用返回空值。

    只标采样类产物；星数为零或类别不符返回空值即原名归档。
    """
    if stars >= 2 and kind in SAMPLE_KINDS:
        return STAR_PASS
    if stars == 1 and kind in SAMPLE_KINDS:
        return STAR_IMPORTANT
    return None


def _decide_stars(config, steps_exec, started_at, archive_dir):
    """判定本次执行的星数，规则见星标规则注释。

    判据通过的信号取两类：链含拟合产物时以全部拟合报告的留出验证
    判定为准，全过两颗，任一不过零颗；无拟合产物时以步骤日志含基
    准通过结论行为准。两星优先，判据未过而配置声明重要意义时一颗。
    """
    verdicts = []
    cur_tag = None
    for st in steps_exec:
        if st.get("tag"):
            cur_tag = st["tag"]
        if st["mode"] == "python" and cur_tag:
            rp = _find_fit_report(cur_tag, started_at)
            if rp:
                try:
                    with open(rp, encoding="utf-8") as fh:
                        verdicts.append(
                            fit_diag.parse_report(fh.read()).get(
                                "holdout_verdict"))
                except OSError:
                    pass
    if verdicts:
        if all(v == "过" for v in verdicts):
            return 2
        return 0
    for st in steps_exec:
        lp = os.path.join(archive_dir, st["log"])
        try:
            with open(lp, encoding="utf-8", errors="replace") as fh:
                if "BENCH PASS" in fh.read():
                    return 2
        except OSError:
            continue
    if config.get("importance") is True:
        return 1
    return 0


def _build_child_env(config):
    """组装子进程环境，返回注入后的完整环境字典。"""
    env = dict(os.environ)
    for key in [k for k in env if k.startswith(config_schema.ENV_PREFIX)]:
        del env[key]
    # 禁用手动输入通道（事项 I-129 根因修复）：采集步无头无人值守，
    # 全系统键盘轮询会把其他会话的击键当手动接管，顶掉控制器若干步。
    env["FLAPPER_NO_MANUAL"] = "1"
    env["PYTHONUTF8"] = "1"
    env.update(STANDARD_LOCK)
    env["FLAPPER_TAG"] = config["tags"]["name"]
    env["FLAPPER_DESC"] = config["tags"]["desc"]
    env.update(config["overrides"])
    return env


def _check_ident_pointer(config, owned_paths):
    """核对数据流指针的归属。

    返回登记字典。指针指向的数据流属于本次运行的新产物且其旁挂元数据
    标注与本次实验名不符时，把该数据流与元数据从 owned_paths 中剔除。
    """
    record = {"file": None, "in_artifacts": False, "tag_match": None,
              "note": None}
    pointer_path = os.path.join(LOGS_DIR, IDENT_POINTER_NAME)
    try:
        with open(pointer_path, encoding="utf-8") as fh:
            pointer_text = fh.read().strip()
    except OSError:
        record["note"] = "数据流指针 %s 不可读" % IDENT_POINTER_NAME
        return record
    record["file"] = pointer_text or None
    if not pointer_text:
        record["note"] = "数据流指针为空"
        return record
    pointed = os.path.join(LOGS_DIR, pointer_text)
    if pointed not in owned_paths:
        record["note"] = "指针指向的文件不是本次运行的新产物"
        return record
    record["in_artifacts"] = True
    meta_path = pointed + ".meta.json"
    try:
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError) as exc:
        record["note"] = "旁挂元数据不可读：%s" % exc
        return record
    record["tag_match"] = (meta.get("tags") or "") == config["tags"]["name"]
    if record["tag_match"] is False:
        record["note"] = ("元数据标注与本次实验名不符，该数据流不属本次"
                          "运行，不归档")
        for drop in (pointed, meta_path):
            if drop in owned_paths:
                owned_paths.remove(drop)
    return record


def _wait_child(proc, mode, run_log_path, wall_cap, started_at):
    """等待子进程结束，返回结果字典。

    isaac 模式轮询运行日志找收尾里程碑，里程碑出现后再等一个宽限期；
    两种模式超时都不结束进程，只登记超时。
    """
    exit_code = None
    milestone_seen_at = None
    timed_out = False
    linger = False
    while True:
        exit_code = proc.poll()
        if mode == "isaac":
            if milestone_seen_at is None:
                try:
                    with open(run_log_path, encoding="utf-8",
                              errors="replace") as fh:
                        log_text = fh.read()
                except OSError:
                    log_text = ""
                if (MILESTONE_RUN_CAP in log_text
                        and MILESTONE_CAP_DONE in log_text):
                    milestone_seen_at = time.time()
                    print("[运行] 运行日志出现收尾里程碑")
        if exit_code is not None:
            break
        now = time.time()
        if (milestone_seen_at is not None
                and now - milestone_seen_at > MILESTONE_EXIT_GRACE_S):
            linger = True
            break
        if now - started_at > wall_cap:
            timed_out = True
            break
        time.sleep(POLL_INTERVAL_S)
    return {"exit_code": exit_code, "timed_out": timed_out, "linger": linger}


def _collect_artifacts(config, before, started_at, archive_dir, stars=0):
    """收集并复制本次运行的新产物，返回三元组。

    三元组依次为产物清单、数据流指针核对记录、提示列表。采样类产物
    按星标规则在归档名前加全角星，见星标规则注释。
    """
    notes = []
    after = _scan_log_files()
    new_paths = sorted(set(after.keys()) - set(before.keys()))
    owned_paths = []
    for path in new_paths:
        info = after[path]
        if info[1] is None or info[1] >= started_at - 1.0:
            owned_paths.append(path)
        else:
            notes.append("新文件 %s 的修改时间早于运行开始，判非本次运行"
                         "产物，不归档" % _archive_name(path))
    pointer_record = _check_ident_pointer(config, owned_paths)
    if pointer_record.get("note"):
        notes.append("数据流指针核对：%s" % pointer_record["note"])

    artifacts = []
    for path in owned_paths:
        source_relative = os.path.relpath(path, LOGS_DIR).replace(os.sep, "/")
        dest_name = _archive_name(path)
        prefix = _star_prefix(_artifact_kind(dest_name), stars)
        if prefix:
            dest_name = prefix + dest_name
        dest_path = os.path.join(archive_dir, dest_name)
        try:
            shutil.copy2(path, dest_path)
        except OSError as exc:
            notes.append("产物复制失败 %s：%s" % (source_relative, exc))
            continue
        artifacts.append({
            "original": source_relative,
            "archived": dest_name,
            "kind": _artifact_kind(dest_name),
            "bytes": os.path.getsize(dest_path),
            "sha256": _file_sha256(dest_path),
        })
    return artifacts, pointer_record, notes


def main(argv=None):
    """命令行入口，返回进程退出码。"""
    parser = argparse.ArgumentParser(
        description="声明式实验管线统一运行器，事项 I-015：读配置、注环境、"
                    "起子进程、归档产物、写运行记录")
    parser.add_argument("config", help="实验配置 JSON 文件路径")
    args = parser.parse_args(argv)

    config, problems = config_schema.load_config(args.config)
    if problems:
        print("=" * 78)
        print("配置校验未通过，拒绝运行")
        for problem in problems:
            print("[拒] " + problem)
        print("=" * 78)
        return 2
    print("[过] 配置校验通过：%s" % os.path.abspath(args.config))

    if set(STANDARD_LOCK) != set(config_schema.STANDARD_LOCK_VARS):
        print("[拒] 标准锁变量名与模式清单不一致；锁取值唯一来源为 "
              "modules/backend/bench_runner.py 的 STANDARD_LOCK，请先对齐"
              "再运行")
        return 2

    digest = hashing.digest_hex(config)
    short = hashing.short_id(config)
    print("配置摘要 %s" % digest)
    print("十二位短标识 %s" % short)

    mode = config["scene"]["mode"]
    chain_spec = config.get("chain")
    has_isaac_step = (mode == "isaac" if not chain_spec
                      else any(s.get("mode") == "isaac"
                               for s in chain_spec))
    if has_isaac_step:
        busy_list, method = detect_isaac_processes()
        if busy_list:
            busy_text = "、".join(
                "%s，进程号 %s" % (p.get("Name"), p.get("ProcessId"))
                for p in busy_list)
            print("[拒] 检测到既有 Isaac 进程：%s；按规则拒绝并行第二个"
                  "实例，也绝不结束既有进程。检测方式为 %s"
                  % (busy_text, method))
            return 2

    duration_s = float(config["scene"]["duration_s"])
    wall_cap = max(WALL_CAP_MIN_S, WALL_CAP_PER_DURATION_S * duration_s)

    archive_dir = os.path.join(ARCHIVE_ROOT, "%s_%s" % (short, _now_stamp()))
    os.makedirs(archive_dir, exist_ok=True)
    # 本次运行身份（事项 I-186）：实验标识派生自配置摘要，运行标识为归
    # 档目录名，事项与基准取配置声明；先于链步骤落盘供分析步读取。
    identity = _build_identity(config, digest, short, archive_dir)
    print("[身份] 实验 %s 运行 %s 事项 %s 基准 %s"
          % (identity["experiment_id"], identity["run_id"],
             identity["item_id"] or "未声明",
             identity["baseline_id"] or "不适用"))
    # 证据包起始采集挂点（事项 I-186 返修）：子进程启动前采集真正生效条件
    # 与可恢复源码差异；收尾构建只读此采集，不以收尾时配置补历史事实。
    try:
        if os.environ.get("FLAPPER_EVIDENCE_PACK", "1") != "0":
            from modules import evidence_pack as _ep0
            _ep0.capture_conditions(archive_dir, config)
            print("[登记] 运行起始条件已采集 %s"
                  % os.path.join(archive_dir, _ep0.CONDITIONS_NAME))
    except Exception as _ep0_exc:
        print("[提示] 起始条件采集失败（不影响运行）：%r" % _ep0_exc)
    run_log_path = os.path.join(archive_dir, RUN_LOG_NAME)

    notes = []
    if mode == "isaac":
        max_s_text = config["overrides"].get("FLAPPER_MAX_S")
        if max_s_text is not None:
            try:
                max_s = float(max_s_text)
            except ValueError:
                notes.append("参数节 FLAPPER_MAX_S 为 %s，不是数值"
                             % max_s_text)
            else:
                if abs(max_s - duration_s) > 1e-9:
                    notes.append("参数节 FLAPPER_MAX_S 为 %s，与场景节时长 "
                                 "%s 不一致；环境变量以参数节为准"
                                 % (max_s_text, duration_s))

    child_env = _build_child_env(config)
    # I-186 观察采集身份传递：isaac 子进程经环境变量获知归档目录与本次
    # 实验/运行身份（非 FLAPPER_ 前缀，不进入参数记账），供
    # runtime_observer 在真实生产点落档同 run 实际对象读数。
    child_env["I186_ARCHIVE_DIR"] = archive_dir
    child_env["I186_EXPERIMENT_ID"] = identity["experiment_id"]
    child_env["I186_RUN_ID"] = identity["run_id"]
    applied_text = " ".join(
        "%s=%s" % kv for kv in sorted(child_env.items())
        if kv[0].startswith(config_schema.ENV_PREFIX) or kv[0] == "PYTHONUTF8")
    print("[运行] 注入环境 %s" % applied_text)
    print("[运行] 归档目录 %s" % archive_dir)

    with open(args.config, "rb") as fh:
        config_file_sha = hashlib.sha256(fh.read()).hexdigest()

    chain = config.get("chain") or [
        {"name": "main", "mode": mode, "argv": config["scene"]["argv"]}]

    before_scan = _scan_log_files()
    started_at = time.time()
    started_text = time.strftime("%Y-%m-%d %H:%M:%S")

    steps_exec = []
    collect_csv = None
    complete = True
    rule = "链全部步骤完成；isaac 步骤以收尾里程碑判定完成性，python 步骤以退出码零判定"
    evidence = []
    exit_code = None
    timed_out = False
    linger = False
    n_isaac_steps = sum(1 for s in chain if s.get("mode") == "isaac")
    multi_isaac = n_isaac_steps > 1
    current_tag = config["tags"]["name"]

    for pos, step in enumerate(chain):
        st_name = step.get("name", "step%d" % (pos + 1))
        st_mode = step["mode"]
        single = len(chain) == 1
        st_log_name = (RUN_LOG_NAME if single
                       else "step_%02d_%s.log" % (pos + 1, st_name))
        st_log_path = os.path.join(archive_dir, st_log_name)
        if st_mode == "isaac":
            st_argv = step.get("argv") or config["scene"]["argv"]
            st_env = dict(child_env)
            for k, v in (step.get("overrides") or {}).items():
                st_env[k] = v
            if multi_isaac:
                st_env["FLAPPER_TAG"] = "%s_%s" % (
                    config["tags"]["name"], st_name)
            st_cap = wall_cap
        else:
            st_argv = _substitute_argv(step["argv"], collect_csv,
                                       current_tag, archive_dir,
                                       identity["experiment_id"],
                                       identity["run_id"])
            st_env = _python_child_env()
            st_cap = config_schema.CHAIN_PYTHON_WALL_CAP_S
        print("[链 %s] 模式 %s 命令 %s" % (st_name, st_mode,
                                            " ".join(st_argv)))
        st_launch = None
        st_wait = {"exit_code": None, "timed_out": False, "linger": False}
        st_started = time.time()
        with open(st_log_path, "w", encoding="utf-8",
                  errors="replace") as log_fh:
            try:
                proc = subprocess.Popen(st_argv, cwd=_REPO_ROOT,
                                        env=st_env, stdout=log_fh,
                                        stderr=subprocess.STDOUT)
            except OSError as exc:
                st_launch = str(exc)
                log_fh.write("子进程启动失败：%s\n" % exc)
                proc = None
            if proc is not None:
                st_wait = _wait_child(proc, st_mode, st_log_path, st_cap,
                                      st_started)
        st_wall = time.time() - st_started
        with open(st_log_path, encoding="utf-8", errors="replace") as fh:
            st_text = fh.read()
        st_milestone = (MILESTONE_RUN_CAP in st_text
                        and MILESTONE_CAP_DONE in st_text)
        st_crash = LOOP_CRASH_MARK in st_text
        st_exit_text = ("无，进程未退出" if st_wait["exit_code"] is None
                        else str(st_wait["exit_code"]))
        st_evidence = []
        if st_launch is not None:
            st_complete = False
            st_evidence.append("步骤 %s 启动失败：%s" % (st_name, st_launch))
        elif st_wait["timed_out"]:
            st_complete = False
            st_evidence.append("步骤 %s 超过墙钟上限 %.0f 秒，按规则不结"
                               "束进程" % (st_name, st_cap))
            st_evidence.append("步骤 %s 退出码 %s" % (st_name, st_exit_text))
        elif st_mode == "isaac":
            st_complete = st_milestone
            st_evidence.append("步骤 %s 收尾里程碑文本%s"
                               % (st_name, "已见" if st_milestone else "未见"))
            st_evidence.append("步骤 %s 退出码 %s，仅参考登记"
                               % (st_name, st_exit_text))
        else:
            st_complete = st_wait["exit_code"] == 0
            st_evidence.append("步骤 %s 退出码 %s" % (st_name, st_exit_text))
        if st_wait["linger"]:
            st_evidence.append("步骤 %s 里程碑已见但进程在宽限期内未退"
                               "出，按里程碑判定完成" % st_name)
        if st_crash:
            st_evidence.append("步骤 %s 运行日志含主循环异常标记 %s"
                               % (st_name, LOOP_CRASH_MARK))
        st_evidence.append("步骤 %s 墙钟用时 %.1f 秒" % (st_name, st_wall))
        exit_code = st_wait["exit_code"]
        timed_out = timed_out or st_wait["timed_out"]
        linger = linger or st_wait["linger"]
        # 执行子进程环境见证（事项 I-186 返修 v2）：逐步记录该步最终环
        # 境的实际注入（模式相关：isaac 步含标准锁与参数节覆盖，python
        # 步按隔离规则清空 FLAPPER_ 前缀）与实际解释器，供证据包把起始
        # 采集与实际执行环境逐项对应，不以另一组装的环境证明本次条件。
        env_witness = {
            "mode": st_mode,
            "interpreter": st_argv[0] if st_argv else None,
            "env_final": {k: v for k, v in sorted(st_env.items())
                          if k.startswith(config_schema.ENV_PREFIX)
                          or k == "PYTHONUTF8"},
        }
        steps_exec.append({
            "name": st_name, "mode": st_mode, "argv": st_argv,
            "log": st_log_name, "exit_code": st_wait["exit_code"],
            "timed_out": st_wait["timed_out"], "wall_s": round(st_wall, 1),
            "complete": st_complete, "evidence": st_evidence,
            "step_overrides": step.get("overrides") or {},
            "env_witness": env_witness,
        })
        evidence.extend(st_evidence)
        if st_mode == "isaac":
            current_tag = st_env.get("FLAPPER_TAG",
                                     config["tags"]["name"])
            steps_exec[-1]["tag"] = current_tag
            if st_complete:
                collect_csv = _find_collect_csv(current_tag, started_at)
                steps_exec[-1]["collect_csv"] = collect_csv
                if collect_csv is None:
                    evidence.append("步骤 %s 完成但未按标注定位到采集数据流"
                                    % st_name)
        if not st_complete:
            complete = False
            rule = ("链在步骤 %s 处未完成，后续步骤不再执行；各步完成性"
                    "依据见步骤记录" % st_name)
            break

    finished_at = time.time()
    finished_text = time.strftime("%Y-%m-%d %H:%M:%S")
    wall_s = finished_at - started_at
    evidence.append("墙钟用时 %.1f 秒" % wall_s)

    family_summary_path = None
    fit_diagnosis_path = None
    if n_isaac_steps >= 2:
        family_summary_path = _write_family_summary(
            config, digest, started_text, started_at, steps_exec,
            archive_dir)
        evidence.append("配置族汇总对照表已产出 %s"
                        % os.path.basename(family_summary_path))
    else:
        fit_rp = _find_fit_report(config["tags"]["name"], started_at)
        if fit_rp:
            try:
                with open(fit_rp, encoding="utf-8") as fh:
                    parsed = fit_diag.parse_report(fh.read())
            except OSError:
                parsed = {}
            if parsed.get("holdout_verdict") == "不过":
                blk = fit_diag.render_block(
                    config["tags"]["name"], parsed,
                    fit_diag.diagnose(parsed))
                fit_diagnosis_path = os.path.join(archive_dir,
                                                  "fit_diagnosis.md")
                with open(fit_diagnosis_path, "w",
                          encoding="utf-8") as fh:
                    fh.write(blk + "\n")
                evidence.append("留出验证未过，拟合质量诊断块已产出 "
                                "fit_diagnosis.md")

    stars = _decide_stars(config, steps_exec, started_at, archive_dir)

    artifacts, pointer_record, collect_notes = _collect_artifacts(
        config, before_scan, started_at, archive_dir, stars)
    notes.extend(collect_notes)
    if stars > 0:
        notes.append("星标 %s：采样类归档名已加全角星前缀"
                     % ("两颗，判据通过" if stars >= 2 else "一颗，重要意义"))

    env_applied = {"PYTHONUTF8": "1"}
    env_applied.update(STANDARD_LOCK)
    env_applied["FLAPPER_TAG"] = config["tags"]["name"]
    env_applied["FLAPPER_DESC"] = config["tags"]["desc"]
    env_applied.update(config["overrides"])

    record = {
        "schema_version": 2,
        "item": identity["item_id"],
        "item_id_reason": identity["item_id_reason"],
        "runner_item": "I-015",
        "experiment_id": identity["experiment_id"],
        "run_id": identity["run_id"],
        "baseline_id": identity["baseline_id"],
        "baseline_id_reason": identity["baseline_id_reason"],
        "runner": "modules/experiment_pipeline/runner.py",
        "config_path": os.path.abspath(args.config),
        "config": config,
        "config_file_sha256": config_file_sha,
        "digest": digest,
        "digest_short12": short,
        "command": config["scene"]["argv"],
        "mode": mode,
        "steps": steps_exec,
        "stars": stars,
        "family_summary": (os.path.basename(family_summary_path)
                           if family_summary_path else None),
        "fit_diagnosis": (os.path.basename(fit_diagnosis_path)
                          if fit_diagnosis_path else None),
        "env_applied": env_applied,
        "started_at": started_text,
        "finished_at": finished_text,
        "wall_s": round(wall_s, 1),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "completeness": {"complete": complete, "rule": rule,
                         "evidence": evidence},
        "ident_pointer": pointer_record,
        "notes": notes,
        "run_log": RUN_LOG_NAME,
        "artifacts": artifacts,
        "archive_dir": os.path.relpath(archive_dir, _REPO_ROOT).replace(
            os.sep, "/"),
    }
    record_path = os.path.join(archive_dir, RECORD_NAME)
    with open(record_path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)

    _append_registry(record)
    registry_path = os.path.join(ARCHIVE_ROOT, REGISTRY_NAME)
    print("[登记] 执行记录已追加 %s" % registry_path)
    # 证据包挂点（事项 I-186）：运行收尾后生成统一证据包与结果卡；默认开
    # 启，FLAPPER_EVIDENCE_PACK=0 关闭；只新增文件不改任何既有产物，失败
    # 仅提示不破坏运行记录（回退即不生成，旧包转存 prev）。
    try:
        if os.environ.get("FLAPPER_EVIDENCE_PACK", "1") != "0":
            from modules import evidence_pack as _ep
            _ep.build_pack(archive_dir)
            print("[登记] 证据包已生成 %s"
                  % os.path.join(archive_dir, _ep.PACK_NAME))
    except Exception as _ep_exc:
        print("[提示] 证据包生成失败（不影响运行记录与归档）：%r" % _ep_exc)
    for st in steps_exec:
        print("  步骤 %-12s %-6s %s 墙钟 %.1f 秒"
              % (st["name"], st["mode"],
                 "完成" if st["complete"] else "未完成", st["wall_s"]))

    print("=" * 78)
    print("[完成性] %s；依据：%s" % ("完成" if complete else "未完成", rule))
    for line in evidence:
        print("  证据 " + line)
    for line in notes:
        print("  提示 " + line)
    print("[产物] 共 %d 件归档" % len(artifacts))
    for artifact in artifacts:
        print("  %s %s，%d 字节" % (artifact["kind"], artifact["archived"],
                                     artifact["bytes"]))
    print("[记录] %s" % record_path)
    print("EXPERIMENT %s" % ("DONE 实验运行完成" if complete
                             else "INCOMPLETE 实验运行未完成"))
    return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(main())
