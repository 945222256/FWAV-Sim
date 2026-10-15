# -*- coding: utf-8 -*-
"""i184_samples_check.py — I-184 完整性资格与完整比较检查的真实样例套。

判据依据：I-184 冻结范围——长度不同、空记录、共同截短不得当完整一致；
缺通道、缺步、重复步、步长、非有限数、布局不符返回原因；旧档缺完整性
依据返回待核；饱和触地等事件按清单规则报告、仅违反适用条件才判不合格。

方法约束（I-190 验收教训在案）：本套只驱动 modules/fw_iid_ledger.py 的
真实生产函数（qualify、compare、LedgerRecorder、CLI），不重写被测逻辑；
故障样例为真实记录器产出或真实档案副本的受控注入，不是构造的替身；
每个用例断言期望结论与原因，退出码与报告字段逐项核对。

用法：python tools/i184_samples_check.py
产物：logs/i184_资格与比较检查_<时间戳>.json（含每用例输入来源与哈希）
      logs/i184_samples/（故障样例与副本，小样随施工入库，大副本不入库）
"""
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules import fw_iid_ledger as L   # 真实生产模块

REAL_P3_A = os.path.join(ROOT, "logs", "fw_ledger_fw-iid-ph1-ctrl-p3_0929_102252.bin")
REAL_P3_B = os.path.join(ROOT, "logs", "fw_ledger_fw-iid-ph1-ctrl-p3_0929_102430.bin")
REAL_P1_A = os.path.join(ROOT, "logs", "fw_ledger_fw-iid-ph1-ctrl-p1_0929_101501.bin")
REAL_P1_B = os.path.join(ROOT, "logs", "fw_ledger_fw-iid-ph1-ctrl-p1_0929_101620.bin")
REAL_I095 = os.path.join(ROOT, "logs", "fw_ledger_I095_NEW_1006_114846.bin")
SAMPLE_DIR = os.path.join(ROOT, "logs", "i184_samples")
PHYS_DT = 1.0 / 120.0

RESULTS = []


def sha256_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def record(cid, desc, ok, detail=""):
    RESULTS.append({"id": cid, "desc": desc, "pass": bool(ok),
                    "detail": str(detail)[:300]})


def ref(path):
    return {"path": os.path.abspath(path), "sha256": sha256_of(path),
            "size": os.path.getsize(path)}


def full_fields(step, z=1.0, with_nan=False):
    f = {name: [float(step)] * n for name, n in L.CHAN_LAYOUT}
    f["bp"] = [0.0, 0.0, z, 1.0, 0.0, 0.0, 0.0]
    if with_nan:
        f["bv"] = [float("nan")] * 6
    return f


def make_record(path, steps, omit=None, z=1.0, with_nan=False):
    """用真实 LedgerRecorder 产出样例（生产代码路径，非替身）。"""
    rec = L.LedgerRecorder(path, [f"j{i}" for i in range(9)], PHYS_DT,
                           meta={"tag": "i184-sample"})
    for s in steps:
        f = full_fields(s, z=z, with_nan=with_nan)
        if omit:
            f.pop(omit, None)
        rec.record(s, f)
    rec.close()
    return path


def truncate_with_tail(src, dst, keep_steps):
    """真实档案副本体部截短到 keep_steps 步并改尾统计为一致（受控注入：
    结构各自有效但短于原始，用于长度不同与共同截短用例）。"""
    raw = open(src, "rb").read()
    hlen = int.from_bytes(raw[8:12], "little")
    body_start = 12 + hlen
    tail_at = raw.find(L.TAIL_MARK, body_start)
    tail = json.loads(raw[tail_at + len(L.TAIL_MARK):].decode("utf-8"))
    n_floats = sum(n for _, n in L.CHAN_LAYOUT)
    per = (1 + n_floats) * 8
    body = raw[body_start:body_start + keep_steps * per]
    tail["n_steps"] = keep_steps
    with open(dst, "wb") as f:
        f.write(raw[:body_start] + body + L.TAIL_MARK
                + json.dumps(tail, ensure_ascii=False).encode("utf-8"))
    return dst


def strip_tail(src, dst):
    """真实档案副本去尾标记（旧档缺完整性依据用例）。"""
    raw = open(src, "rb").read()
    hlen = int.from_bytes(raw[8:12], "little")
    tail_at = raw.find(L.TAIL_MARK, 12 + hlen)
    with open(dst, "wb") as f:
        f.write(raw[:tail_at])
    return dst


def patch_layout(src, dst):
    """真实档案副本头内交换前两个通道名（布局不符用例，总元素数不变）。"""
    raw = open(src, "rb").read()
    hlen = int.from_bytes(raw[8:12], "little")
    header = json.loads(raw[12:12 + hlen].decode("utf-8"))
    header["channels"][0][0], header["channels"][1][0] = \
        header["channels"][1][0], header["channels"][0][0]
    hj = json.dumps(header, ensure_ascii=False).encode("utf-8")
    with open(dst, "wb") as f:
        f.write(raw[:8] + len(hj).to_bytes(4, "little") + hj
                + raw[12 + hlen:])
    return dst


def main():
    if os.path.isdir(SAMPLE_DIR):
        shutil.rmtree(SAMPLE_DIR)
    os.makedirs(SAMPLE_DIR)
    for p in (REAL_P3_A, REAL_P3_B, REAL_P1_A, REAL_P1_B, REAL_I095):
        assert os.path.exists(p), "真实档案缺失: " + p

    # R1 真实逐位相同对（ctrl-p3，旧档）：无清单待核，不判完整一致（返修语义）
    rc = L.compare(REAL_P3_A, REAL_P3_B)
    record("R1", "真实相同对（旧档）无清单：待核返回 3，不判完整一致",
           rc == 3, f"rc={rc}")
    qa = L.qualify(REAL_P3_A)
    record("R1b", "真实相同对 A 资格待核（缺范围依据、旧档无位置证据）",
           qa["status"] == "unknown", qa["reason"][:120])
    qb1 = L.qualify(REAL_P3_A, manifest={"expected_steps": 4200,
                                         "phys_dt": PHYS_DT})
    record("R1c", "真实对带清单仍待核（tgt 旧档累计数不可定位）",
           qb1["status"] == "unknown", qb1["reason"][:120])

    # R2 真实注入差对（ctrl-p1，仪器校验 ULP 注入）→ 差异定位首差
    jo = os.path.join(SAMPLE_DIR, "r2.json")
    rc = L.compare(REAL_P1_A, REAL_P1_B, json_out=jo)
    rep = json.load(open(jo, encoding="utf-8"))
    hA, A = L.load(REAL_P1_A)
    hB, B = L.load(REAL_P1_B)
    neq = A.view(np.uint64) != B.view(np.uint64)
    rows = np.flatnonzero(neq.any(axis=1))
    s = int(rows[0])
    cols = np.flatnonzero(neq[s])
    okR2 = (rc == 1 and rep["verdict"] == "different"
            and rep["n_diff_steps"] == int(len(rows))
            and rep["s_star"] == int(A[s, 0])
            and rep["first_col"] == int(cols[0]))
    record("R2", "真实不同对 compare 返回 1 且首差步与首差列独立复算一致",
           okR2, f"rc={rc} s*={rep.get('s_star')} 差步={rep.get('n_diff_steps')}")

    # R3 真实完整样例 I095_NEW 配方级清单检查（env MAX_S=60 → 7200 步）
    man_i095 = {"expected_steps": 7200, "phys_dt": PHYS_DT,
                "run_conditions": {"FLAPPER_LEDGER": "1",
                                   "FLAPPER_MAX_S": "60"}}
    q = L.qualify(REAL_I095, manifest=man_i095)
    okR3 = (q["status"] == "unknown" and q["steps"] == 7200
            and q["input_refs"]["tail_present"])
    record("R3", "真实完整样例按配方清单：旧档 tgt 累计数不可定位判待核",
           okR3, q["reason"][:120])

    # R4 长度不同但公共段相同（核心缺陷用例：旧版返回 0）
    trunc = truncate_with_tail(
        REAL_P3_A, os.path.join(SAMPLE_DIR, "p3_trunc3000.bin"), 3000)
    rc = L.compare(trunc, REAL_P3_B)
    jo4 = os.path.join(SAMPLE_DIR, "r4.json")
    rc2 = L.compare(trunc, REAL_P3_B, json_out=jo4)
    rep4 = json.load(open(jo4, encoding="utf-8"))
    okR4 = (rc != 0 and rc2 != 0 and rep4["verdict"] == "not_complete"
            and any("长度不同" in r for r in rep4["reasons"])
            and rep4["common_part_identical"] is True
            and rep4["identical"] is False)
    record("R4", "长度不同公共段相同：不得判完整一致（旧版缺陷的正向反例）",
           okR4, f"rc={rc} reasons={rep4.get('reasons')}")

    # R5 空记录（真实记录器产出）
    empty = make_record(os.path.join(SAMPLE_DIR, "empty.bin"), [])
    q = L.qualify(empty)
    okR5 = q["status"] == "invalid" and "空记录" in q["reason"]
    rc5 = L.compare(empty, empty)
    record("R5", "空记录：资格 invalid 含原因，compare 不返回 0",
           okR5 and rc5 != 0, f"{q['status']}（{q['reason']}）rc={rc5}")

    # R6 两份共同截短对照完整范围（清单预期 4200，两份各 3000）
    t2 = truncate_with_tail(
        REAL_P3_B, os.path.join(SAMPLE_DIR, "p3_trunc3000_b.bin"), 3000)
    man4200 = {"expected_steps": 4200}
    q6a = L.qualify(trunc, manifest=man4200)
    q6b = L.qualify(t2, manifest=man4200)
    okR6 = ("截短" in q6a["reason"] and "截短" in q6b["reason"]
            and q6a["status"] == "invalid")
    jo6 = os.path.join(SAMPLE_DIR, "r6.json")
    rc6 = L.compare(trunc, t2, manifest=man4200, json_out=jo6)
    record("R6", "两份共同截短：各自 invalid 截短原因，compare 拒绝且非 0",
           okR6 and rc6 != 0, f"rc={rc6} A:{q6a['reason']}")

    # R7 缺必需通道（真实记录器逐步缺 je；默认清单不允许）
    p7 = make_record(os.path.join(SAMPLE_DIR, "miss_je.bin"),
                     range(1, 11), omit="je")
    q7 = L.qualify(p7)
    okR7 = (q7["status"] == "invalid" and "缺 10 步" in q7["reason"]
            and "无预先批准例外" in q7["reason"])
    man7 = {"expected_steps": 10, "phys_dt": PHYS_DT,
            "approved_misses": {"je": {"max": 10,
                                       "at_steps": list(range(1, 11))}}}
    q7b = L.qualify(p7, manifest=man7)
    record("R7", "缺必需通道 je：默认 invalid；预先批准例外加位置证据后 valid",
           okR7 and q7b["status"] == "valid",
           f"{q7['status']}；批准后 {q7b['status']}（{q7b['reason'][:80]}）")

    # R8 缺步（步号 1,2,3,5）
    p8 = make_record(os.path.join(SAMPLE_DIR, "gap.bin"), [1, 2, 3, 5])
    q8 = L.qualify(p8)
    record("R8", "缺步样例：invalid 且原因含缺步",
           q8["status"] == "invalid" and "缺步" in q8["reason"], q8["reason"])

    # R9 重复步（步号 1,2,2,4）
    p9 = make_record(os.path.join(SAMPLE_DIR, "dup.bin"), [1, 2, 2, 4])
    q9 = L.qualify(p9)
    record("R9", "重复步样例：invalid 且原因含重复步号",
           q9["status"] == "invalid" and "重复步号" in q9["reason"], q9["reason"])

    # R10 步长不符（清单 0.01 对档案 1/120）
    q10 = L.qualify(REAL_I095, manifest={"phys_dt": 0.01})
    record("R10", "步长不符样例：invalid 且原因含步长不符",
           q10["status"] == "invalid" and "步长不符" in q10["reason"],
           q10["reason"])

    # R11 非有限数（真实记录器写入 NaN）
    p11 = make_record(os.path.join(SAMPLE_DIR, "nan.bin"),
                      range(1, 11), with_nan=True)
    q11 = L.qualify(p11)
    okR11 = (q11["status"] == "invalid" and "非有限" in q11["reason"]
             and q11["nonfinite"]["total"] > 0
             and "bv" in q11["nonfinite"]["by_channel"])
    record("R11", "非有限数样例：invalid 且分通道统计在案", okR11,
           f"{q11['status']}（{q11['reason']}）")

    # R12 布局不符（头内通道名交换的真实档案副本）
    p12 = patch_layout(REAL_P3_A, os.path.join(SAMPLE_DIR, "layout.bin"))
    q12 = L.qualify(p12)
    rc12 = L.compare(p12, REAL_P3_B)
    record("R12", "布局不符样例：qualify invalid 布局不符，compare 拒绝非 0",
           q12["status"] == "invalid" and "布局不符" in q12["reason"]
           and rc12 != 0, f"{q12['status']} rc={rc12}")

    # R13 旧档缺完整性依据（去尾副本）→ 待核
    p13 = strip_tail(REAL_P3_A, os.path.join(SAMPLE_DIR, "notail.bin"))
    q13 = L.qualify(p13)
    jo13 = os.path.join(SAMPLE_DIR, "r13.json")
    rc13 = L.compare(p13, REAL_P3_B, json_out=jo13)
    rep13 = json.load(open(jo13, encoding="utf-8"))
    okR13 = (q13["status"] == "unknown" and "待核" in q13["reason"]
             and rc13 == 3 and rep13["verdict"] == "unknown")
    record("R13", "旧档缺尾部依据：qualify 待核，compare 返回 3 待核",
           okR13, f"{q13['status']}（{q13['reason']}）rc={rc13}")

    # R14 事件规则：触地报告不判不合格 vs 声明适用条件后判不合格
    p14 = make_record(os.path.join(SAMPLE_DIR, "ground.bin"),
                      range(1, 201), z=0.02)
    rule = {"name": "ground_contact", "channel": "bp", "component": 2,
            "op": "<", "threshold": 0.05, "after_step": 50,
            "disqualify_on": False}
    q14a = L.qualify(p14, manifest={"expected_steps": 200,
                                    "phys_dt": PHYS_DT,
                                    "event_rules": [rule]})
    rule_bad = dict(rule, disqualify_on=True)
    q14b = L.qualify(p14, manifest={"expected_steps": 200,
                                    "phys_dt": PHYS_DT,
                                    "event_rules": [rule_bad]})
    n_expect = sum(1 for s in range(1, 201) if s >= 50)   # 独立计算：151
    okR14 = (q14a["status"] == "valid"
             and q14a["events"][0]["n_violating_steps"] == n_expect
             and q14b["status"] == "invalid"
             and "违反适用条件" in q14b["reason"])
    record("R14", "事件规则：触地 %d 步报告不判不合格；声明后 invalid" % n_expect,
           okR14, f"报告态 {q14a['status']}/事件 {q14a['events'][0]['n_violating_steps']}"
                  f"；判不合格态 {q14b['status']}（{q14b['reason']}）")

    # R15 有清单无事件规则时触地不作为无条件不合格依据
    q15 = L.qualify(p14, manifest={"expected_steps": 200, "phys_dt": PHYS_DT})
    record("R15", "无清单事件规则：触地样例仍 valid（不无条件判不合格）",
           q15["status"] == "valid", q15["reason"])

    # R16 命令行入口（生产入口实际执行）
    jo16 = os.path.join(SAMPLE_DIR, "r16.json")
    fresh = make_record(os.path.join(SAMPLE_DIR, "fresh_complete.bin"),
                        range(1, 51))
    man16 = os.path.join(SAMPLE_DIR, "man16.json")
    with open(man16, "w", encoding="utf-8") as f16:
        json.dump({"expected_steps": 50, "phys_dt": PHYS_DT}, f16)
    r16a = subprocess.run(
        [sys.executable, os.path.join(ROOT, "modules", "fw_iid_ledger.py"),
         "qualify", fresh, "--manifest", man16, "--json", jo16],
        capture_output=True, text=True)
    r16b = subprocess.run(
        [sys.executable, os.path.join(ROOT, "modules", "fw_iid_ledger.py"),
         "qualify", REAL_I095], capture_output=True, text=True)
    record("R16", "命令行入口：新完整样例带清单退出 0；旧档无清单待核退出 3",
           r16a.returncode == 0 and r16b.returncode == 3,
           f"新样例 rc={r16a.returncode} 旧档 rc={r16b.returncode}")

    # N1/N2 首步目标缺失：预先批准例外（位置证据）valid；无批准 invalid
    p_n = os.path.join(SAMPLE_DIR, "tgt_first.bin")
    rec_n = L.LedgerRecorder(p_n, [f"j{i}" for i in range(9)], PHYS_DT,
                             meta={"tag": "i184-n1"})
    for s_n in range(1, 6):
        f_n = full_fields(s_n)
        if s_n == 1:
            f_n.pop("tgt")
        rec_n.record(s_n, f_n)
    rec_n.close()
    qn1 = L.qualify(p_n, manifest={"expected_steps": 5, "phys_dt": PHYS_DT,
                                   "approved_misses": {"tgt": {"max": 1,
                                                               "at_steps": [1]}}})
    qn2 = L.qualify(p_n, manifest={"expected_steps": 5, "phys_dt": PHYS_DT})
    record("N1", "首步 tgt 缺失在预先批准例外内（位置证据在案）valid",
           qn1["status"] == "valid", qn1["reason"][:100])
    record("N2", "同一记录无预先批准例外：invalid 且位置在原因中",
           qn2["status"] == "invalid" and "位置 [1]" in qn2["reason"],
           qn2["reason"][:100])

    # R17 周期积分既有功能保留（真实不同对，窗口 0.556 秒）
    jo17 = os.path.join(SAMPLE_DIR, "r17.json")
    rc17 = L.compare(REAL_P1_A, REAL_P1_B, json_out=jo17, period_s=0.556)
    rep17 = json.load(open(jo17, encoding="utf-8"))
    record("R17", "周期积分差分功能保留（--period-s 输出 period_integral）",
           rc17 == 1 and "period_integral" in rep17
           and len(rep17["period_integral"]) == 3, f"rc={rc17}")

    # 汇总与归档（输入来源与哈希逐用例留存）
    n_pass = sum(1 for r in RESULTS if r["pass"])
    summary = {"total": len(RESULTS), "pass": n_pass,
               "fail": len(RESULTS) - n_pass,
               "verdict": "pass" if n_pass == len(RESULTS) else "fail"}
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    inputs = {
        "REAL_P3_A": ref(REAL_P3_A), "REAL_P3_B": ref(REAL_P3_B),
        "REAL_P1_A": ref(REAL_P1_A), "REAL_P1_B": ref(REAL_P1_B),
        "REAL_I095": ref(REAL_I095),
    }
    out = {"item": "I-184", "generated_at": ts,
           "production_module": "modules/fw_iid_ledger.py",
           "source_version": "check%d/fmt%d" % (L.CHECK_VERSION, L.FMT_VERSION),
           "summary": summary, "inputs": inputs, "checks": RESULTS}
    path = os.path.join(ROOT, "logs", "i184_资格与比较检查_%s.json" % ts)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False))
    for r in RESULTS:
        print("%s %s %s %s" % ("过" if r["pass"] else "拒", r["id"],
                               r["desc"], r["detail"]))
    print("产物:", path)
    return 0 if summary["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
