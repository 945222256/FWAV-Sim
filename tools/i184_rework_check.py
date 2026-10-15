# -*- coding: utf-8 -*-
"""i184_rework_check.py — I-184 返修验证：移植 Doro 独立探针二十四例。

依据 logs/hq_intake/doro_i184_independent_review_20261012/ 的独立复核与
返修决定、independent_probe.py 的固定配方与逐例期望。方法与原探针一致：
全部样例由真实 LedgerRecorder（返修版）按原配方重新生成于本目录，不覆写
Doro 原始样例与任何历史档案；检查调用真实 qualify、compare 与命令行入口，
不重写被测判断。另附：Doro 原始样例文件在返修后语义下的判定（仅记录，
旧记录器产物按待核规则处理）；运行身份保留检查；真实历史双档只作体部
逐字节诊断不补资格。

用法：python tools/i184_rework_check.py
产物：logs/i184_rework/（逐例 JSON 与汇总）、logs/i184_rework_samples/
"""
import contextlib
import datetime
import hashlib
import io
import json
import pathlib
import struct
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path("G:/Playground/isaac_flapper_sim")
BASE = ROOT / "logs" / "i184_rework"
SAMPLES = ROOT / "logs" / "i184_rework_samples"
DORO = ROOT / "logs/hq_intake/doro_i184_independent_review_20261012"
sys.path.insert(0, str(ROOT))
from modules import fw_iid_ledger as L   # 真实生产模块（返修版）

import shutil as _shutil
if SAMPLES.is_dir():
    _shutil.rmtree(SAMPLES)   # 每次全量重生成（与探针"不得已存在"纪律一致）
BASE.mkdir(exist_ok=True)
SAMPLES.mkdir(exist_ok=True)

names = ["flap_L", "flap_R", "twist_L", "twist_R",
         "fold_L", "fold_R", "tail_yaw", "tail_L", "tail_R"]
env = {"fixture": "doro-I184-rework", "planned_steps": "8"}
manifest = {"expected_steps": 8, "phys_dt": 1 / 120, "step_start": 1,
            "required_channels": {n: 0 for n, c in L.CHAN_LAYOUT},
            "run_conditions": env}
(BASE / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

REPORT = {"item": "I-184", "runner": "L2返修验证（移植Doro探针）",
          "generated_at": datetime.datetime.now(
              datetime.timezone(datetime.timedelta(hours=8))).isoformat(),
          "cases": [], "source_sha256": hashlib.sha256(
              (ROOT / "modules/fw_iid_ledger.py").read_bytes()).hexdigest()}


def save():
    (BASE / "rework_probe.json").write_text(
        json.dumps(REPORT, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8")


def produce(name, steps=range(1, 9), change=None):
    p = SAMPLES / (name + ".bin")
    assert not p.exists(), p
    r = L.LedgerRecorder(p, names, 1 / 120,
                         meta={"tag": "independent-fixture",
                               "run_id": name, "env": env})
    for step in steps:
        f = {n: [float(step) + j * 0.01 for j in range(c)]
             for n, c in L.CHAN_LAYOUT}
        f["bp"] = [1., 2., 3., 1., 0., 0., 0.]
        if change:
            change(f, step)
        r.record(step, f)
    r.close()
    return p


def anatomy(p):
    raw = p.read_bytes()
    hlen = struct.unpack("<I", raw[8:12])[0]
    start = 12 + hlen
    at = raw.find(b"#TAIL#", start)
    end = len(raw) if at < 0 else at
    return raw, json.loads(raw[12:start]), start, end, \
        None if at < 0 else json.loads(raw[at + 6:])


def mutate(src, name, header_fn=None, body_fn=None, tail_fn=None,
           drop_tail=False):
    raw, h, start, end, t = anatomy(src)
    a = np.frombuffer(raw[start:end], dtype="<f8").reshape(-1, 56).copy()
    if header_fn:
        header_fn(h)
    if body_fn:
        a = body_fn(a)
    if tail_fn:
        tail_fn(t)
    hb = json.dumps(h, ensure_ascii=False).encode("utf-8")
    tail = b"" if drop_tail else \
        b"#TAIL#" + json.dumps(t, ensure_ascii=False).encode("utf-8")
    p = SAMPLES / (name + ".bin")
    assert not p.exists(), p
    p.write_bytes(raw[:8] + struct.pack("<I", len(hb)) + hb
                  + a.astype("<f8").tobytes() + tail)
    return p


def qual(label, p, expected, man=manifest):
    q = L.qualify(str(p), man)
    row = {"id": label, "operation": "qualify", "input": str(p),
           "expected": expected, "observed": q["status"],
           "pass_expected": q["status"] == expected,
           "reason": q["reason"], "result": q}
    REPORT["cases"].append(row)
    save()
    return q


def cmp(label, a, b, accepted, man=manifest):
    dest = BASE / (label + ".json")
    s = io.StringIO()
    with contextlib.redirect_stdout(s):
        rc = L.compare(str(a), str(b), json_out=str(dest), manifest=man)
    result = json.loads(dest.read_text(encoding="utf-8"))
    passed = (rc == 0) if accepted else \
        (rc != 0 and result.get("verdict") != "identical")
    row = {"id": label, "operation": "compare",
           "inputs": [str(a), str(b)], "manifest": bool(man),
           "expected": "完整一致" if accepted else "不得判完整一致",
           "returncode": rc, "pass_expected": passed,
           "verdict": result.get("verdict"), "reasons": result.get("reasons"),
           "result": result}
    REPORT["cases"].append(row)
    save()
    return result, rc


def main():
    # —— 二十四例（配方与期望逐字对齐 Doro 探针）——
    a = produce("normal")
    b = produce("normal_repeat")
    qual("normal_complete", a, "valid")
    cmp("normal_repeat_complete", a, b, True)

    def changed(f, s):
        if s == 4:
            f["Fa"][1] += 0.125
    diff = produce("one_force_difference", change=changed)
    r, rc = cmp("first_difference", a, diff, False)
    assert r["s_star"] == 4 and r["first_channel"] == "Fa.[1]", r

    empty = produce("empty", steps=[])
    qual("empty_rejected", empty, "invalid")

    tr = mutate(a, "jointly_short_a",
                body_fn=lambda ar: ar[:4], tail_fn=lambda t: t.update(n_steps=4))
    tr2 = mutate(b, "jointly_short_b",
                 body_fn=lambda ar: ar[:4], tail_fn=lambda t: t.update(n_steps=4))
    cmp("both_short_with_manifest", tr, tr2, False)
    cmp("both_short_missing_manifest", tr, tr2, False, None)
    cmp("different_length", a, tr, False, None)

    qual("gap_rejected", produce("gap", steps=[1, 2, 3, 5, 6, 7, 8, 9]),
         "invalid")
    qual("adjacent_duplicate_rejected",
         produce("adjacent_duplicate", steps=[1, 2, 2, 4, 5, 6, 7, 8]),
         "invalid")
    qual("nonadjacent_duplicate_rejected",
         produce("nonadjacent_duplicate", steps=[1, 2, 1, 2, 3, 4, 5, 6]),
         "invalid")
    qual("descending_steps_rejected",
         produce("descending_steps", steps=[1, 0, -1, -2, -3, -4, -5, -6]),
         "invalid")

    def missing_je(f, s):
        if s == 6:
            f.pop("je")
    miss = produce("missing_je", change=missing_je)
    qual("required_channel_missing_rejected", miss, "invalid")

    def short_je(f, s):
        f["je"] = f["je"][:8]
    qual("undersized_channel_rejected",
         produce("undersized_je", change=short_je), "invalid")

    def missing_tgt_late(f, s):
        if s == 6:
            f.pop("tgt")
    late = produce("missing_target_at_step6", change=missing_tgt_late)
    default_man = {k: v for k, v in manifest.items()
                   if k != "required_channels"}
    qual("late_missing_target_default_exception_rejected", late,
         "invalid", default_man)

    missing_count = mutate(miss, "missing_je_counter",
                           tail_fn=lambda t: t["miss_counts"].pop("je"))
    qual("missing_tail_channel_counter_unknown", missing_count, "unknown")

    old = mutate(a, "old_missing_tail", drop_tail=True)
    qual("old_missing_tail_unknown", old, "unknown")
    cmp("old_compare_not_qualified", old, old, False)

    import copy as _copy
    bad_dt = _copy.deepcopy(manifest)
    bad_dt["phys_dt"] = 0.01
    qual("time_step_mismatch", a, "invalid", bad_dt)

    def make_nan(f, s):
        if s == 5:
            f["bv"][2] = float("nan")
    qual("nonfinite_rejected", produce("nonfinite", change=make_nan),
         "invalid")

    layout = mutate(a, "bad_layout",
                    header_fn=lambda h: h["channels"].__setitem__(
                        0, ["invalid_bp", 7]))
    qual("layout_mismatch", layout, "invalid")

    missing_environment = mutate(a, "missing_environment",
                                 header_fn=lambda h: h.pop("env"))
    qual("missing_run_conditions", missing_environment, "unknown")

    phys_bad = mutate(a, "wrong_run_conditions",
                      header_fn=lambda h: h["env"].update(fixture="other-run"))
    qual("run_conditions_mismatch", phys_bad, "invalid")

    # 运行身份保留（返修复核项）
    q = L.qualify(str(a), manifest)
    REPORT["run_identity_retention"] = {
        "producer_header_run_id": "normal",
        "qualifier_run_id": (q.get("run_identity") or {}).get("run_id"),
        "retained": (q.get("run_identity") or {}).get("run_id") == "normal",
        "range": q.get("range"),
    }

    # 真实历史双档只作体部逐字节诊断（不补采集前清单、不授予资格）
    real_a = ROOT / "logs/fw_ledger_fw-iid-ph1-ctrl-p3_0929_102252.bin"
    real_b = ROOT / "logs/fw_ledger_fw-iid-ph1-ctrl-p3_0929_102430.bin"
    ra, ha, sa, ea, ta = anatomy(real_a)
    rb, hb, sb, eb, tb = anatomy(real_b)
    REPORT["historical_diagnostic"] = {
        "files": [str(real_a), str(real_b)],
        "sha256": [hashlib.sha256(ra).hexdigest(),
                   hashlib.sha256(rb).hexdigest()],
        "body_bytes_equal": ra[sa:ea] == rb[sb:eb],
        "scope": "真实历史体部比较口径诊断；旧档缺位置与欠长证据，"
                 "资格按待核处理，不补造清单。",
    }

    # 命令行入口两例
    for label, file, man in [("cli_valid", a, manifest),
                             ("cli_no_complete_range", tr, None)]:
        out = BASE / (label + ".json")
        argv = [sys.executable, str(ROOT / "modules/fw_iid_ledger.py"),
                "qualify", str(file), "--json", str(out)]
        if man:
            argv += ["--manifest", str(BASE / "manifest.json")]
        p = subprocess.run(argv, capture_output=True, encoding="utf-8",
                           errors="replace")
        REPORT["cases"].append({
            "id": label, "operation": "CLI qualify",
            "returncode": p.returncode,
            "observed": json.loads(out.read_text(encoding="utf-8"))["status"],
            "expected": "valid" if man else "unknown",
            "pass_expected": p.returncode == (0 if man else 3),
        })
        save()

    # Doro 原始样例在返修语义下的判定（旧记录器产物，仅记录；
    # 断言唯一底线：原反例不得被判完整一致或 valid 放行完整范围）
    originals = {}
    for name, exp_hint in [("normal.bin", "旧档无欠长统计"),
                           ("jointly_short_a.bin", "截短"),
                           ("missing_target_at_step6.bin", "旧档无位置证据"),
                           ("undersized_je.bin", "旧档无欠长统计"),
                           ("missing_je_counter.bin", "计数键缺失")]:
        p = DORO / "independent_samples" / name
        if not p.exists():
            continue
        q = L.qualify(str(p), manifest)
        originals[name] = {"status": q["status"], "reason": q["reason"][:160]}
    pair = [DORO / "independent_samples/jointly_short_a.bin",
            DORO / "independent_samples/jointly_short_b.bin"]
    dest = BASE / "originals_pair.json"
    with contextlib.redirect_stdout(io.StringIO()):
        rc_pair = L.compare(str(pair[0]), str(pair[1]), json_out=str(dest))
    orig_pair = json.loads(dest.read_text(encoding="utf-8"))
    ok_originals = (all(v["status"] != "valid" for v in originals.values())
                    and rc_pair != 0
                    and orig_pair.get("verdict") != "identical")
    REPORT["original_files_under_reworked_semantics"] = {
        "note": "旧记录器产物：无位置与欠长证据，按待核处理，不放行完整一致",
        "qualify": originals,
        "jointly_short_pair_compare": {"returncode": rc_pair,
                                       "verdict": orig_pair.get("verdict")},
        "bottom_line_pass": ok_originals,
    }

    REPORT["summary"] = {
        "cases": len(REPORT["cases"]),
        "match_expected": sum(bool(c["pass_expected"])
                              for c in REPORT["cases"]),
        "counterexamples": [c["id"] for c in REPORT["cases"]
                            if not c["pass_expected"]],
        "original_bottom_line_pass": ok_originals,
    }
    save()
    print(json.dumps(REPORT["summary"], ensure_ascii=False))
    for c in REPORT["cases"]:
        print("%s %s 期望=%s 实际=%s" % (
            "过" if c["pass_expected"] else "拒", c["id"],
            c.get("expected"), c.get("observed", c.get("verdict"))))
    print("运行身份保留:", REPORT["run_identity_retention"]["retained"],
          "| 历史诊断体部一致:",
          REPORT["historical_diagnostic"]["body_bytes_equal"],
          "| 原样例底线:", ok_originals)
    return 0 if (REPORT["summary"]["match_expected"] == len(REPORT["cases"])
                 and ok_originals) else 1


if __name__ == "__main__":
    sys.exit(main())
