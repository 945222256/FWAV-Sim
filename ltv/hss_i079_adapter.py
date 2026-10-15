# -*- coding: utf-8 -*-
"""
hss_i079_adapter.py — 周期系统稳定性判读工具对 I-055 拟合产物格式的适
配，L4 线事项 I-079，续 I-047 工具。

适配范围三件。第一件，通用读取器：读辨识产线 matrix.json 格式（I-055
现行格式与 I-006 同族），提取常数阵 A、周期项 Ac 与 As（有则读，无则零）、
拍频 f_stroke_hz、窗口与卷标，状态维度不限，翼态扩维后的七乘七及以上矩
阵直接可用。第二件，判读核：A of t 等于 A0 加各阶余弦周期项与正弦周期项
之和，四阶龙格库塔积分单周期状态转移矩阵，输出周期增长倍率（特征值模升
序）与弗洛凯指数实部（倍率对数除以周期），读数口径与 ltv/hss_floquet.py
一致。第三件，报告器：逐矩阵输出判读语句与读数表，含与解析值的交叉验证
（常数阵对照矩阵指数，周期阵对照十倍细分步长参考积分）。

合成样例三档预登记（先于运行）：样例一为三乘三常数阵，真值倍率为矩阵指
数特征值模，容差倍率相对误差不超一点零乘十的负十次方；样例二为三乘三一
阶周期阵，参考真值为十倍细分步长积分，容差不超一点零乘十的负九次方；
样例三为七乘七翼态形态周期阵（体轴三元加翼态四元，翼块含周期项），同款
参考与容差。三样例全部以 I-055 matrix.json 格式写成临时文件再读回，验证
读取器与判读核的完整链路。

就绪演练：对已落地的 I-055 拟合产物直接出真实判读，证明数据落地后当日
产出的时限能力已满足。

用法： python ltv/hss_i079_adapter.py [matrix.json 路径 ...]
不带参数跑合成样例三档与就绪演练。产出写 ltv/i079_results 时间戳目录。

I-185 增补：控制使用限制沿读取与输出全程保留。读取器按 I-183 冻结
规则评估模型控制使用资格，读数环境与全部输出携带使用资格、限制原因、
验证状态、源模型身份与内容哈希；正常数学读数不解除控制限制，缺资格
返回待核实，资格矛盾保留禁用并记不合格；未标记限制不构成控制资格通
过。合成样例自带禁用标记，五情形正反例随机检查入档。本工具不产生控
制资格通过结论，资格以验证来源与上游限制为准。
"""
import hashlib
import json
import os
import sys
import tempfile
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
from hss_floquet import stm_rk4, floquet_read, expm_eig  # noqa: E402

SPEC_PATH = os.path.join(_HERE, "hss_spec.json")
TOL = {"const": 1.0e-10, "periodic": 1.0e-9}
READER_NAME = "ltv/hss_i079_adapter.py"
READER_REVISION = "i185-2"
CONTROL_USE_MARK = {
    "qualified": "",
    "diagnostic_only": "【仅诊断·不得用于控制】",
    "unknown": "【控制资格待核实·不得用于控制】",
    "missing": "【控制资格缺失·不得用于控制】",
    "stale": "【控制资格过期·不得用于控制】",
    "not_applicable": "【控制资格不适用·不得用于控制】",
}
REHEARSAL = [
    "logs/ident_matrix/I055turn2deg_20261005_182811/matrix.json",
    "logs/ident_matrix/I055turn3deg_20261005_183132/matrix.json",
    "logs/ident_matrix/I055fixrep55_20261003_132955/matrix.json",
]


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def sha256_file(path):
    """文件内容哈希，作为源模型身份与读取器版本的机器凭据。"""
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for blk in iter(lambda: fp.read(65536), b""):
            h.update(blk)
    return h.hexdigest()


def reader_version():
    """读取器版本身份：路径、修订号与源文件内容哈希。"""
    return {"name": READER_NAME, "revision": READER_REVISION,
            "sha256": sha256_file(__file__)}


def evaluate_control_use(m, path, sha):
    """按 I-183 冻结规则评估模型控制使用资格，返修版 i185-2。

    规则全部来自 I-183 首批接口规格正式版的 model_no_qualification
    行与 control_use 状态枚举，本读取器不新增规则；本版落实 Doro
    2026-10-13 复核五反例的返修要求：
    一，not_for_control 须为严格布尔：数字零、空列表等非法类型不作
    合法假值，返回待核实。
    二，禁用与验证通过声明冲突时保留禁用，原始限制原因原文保留在
    reason 字段，冲突说明另列 conflict_notes，不替代原文。
    三，留出验证失败与验证通过对象并存属资格矛盾，不得放行，返回待
    核实并记不合格。
    四，缺状态名、输入名或尺度声明时按 I-183 model 行以诊断口径使
    用，机器资格同步降为仅诊断，与文字说明一致。
    五，not_for_control 为假须同时有验证记录、适用范围与批准使用条
    件才输出 qualified；缺任一项输出待核实。
    本函数只评估并报告，不授予资格；资格以验证来源与上游限制为准。
    """
    now = _now_iso()
    refs = {"path": path, "sha256": sha}
    base = {"category": "control_use", "source_tool": READER_NAME,
            "source_version": reader_version(), "generated_at": now,
            "input_refs": refs}
    validation = m.get("validation")
    nfc = m.get("not_for_control")
    reason = m.get("not_for_control_reason")
    validation_pass = isinstance(validation, dict) \
        and validation.get("pass") is True
    claimed_qualified = m.get("control_use_status") == "qualified"
    holdout_false = ("holdout_pass" in m
                     and m.get("holdout_pass") is False)
    coords_declared = all(m.get(k) for k in
                          ("state_names", "input_names", "scales"))
    conflict_notes = []
    additional_limits = []

    type_invalid = nfc is not None and not isinstance(nfc, bool)
    if type_invalid:
        cu = dict(base, status="unknown",
                  reason="not_for_control 字段类型非法，须为严格布尔，"
                         "实为 %s；数字零与空列表不作合法假值，待核实"
                         % type(nfc).__name__,
                  reason_status="missing",
                  conflict_notes=conflict_notes,
                  additional_limits=additional_limits)
    elif nfc is True:
        if reason:
            cu_reason, reason_status = str(reason), "present"
        else:
            cu_reason, reason_status = ("上游禁用标记为真但未携带限制原"
                                        "因，缺原因不抹禁用"), "missing"
        if validation_pass or claimed_qualified:
            conflict_notes.append("禁用标记与验证通过或合格声明并存，"
                                  "保留禁用")
        cu = dict(base, status="diagnostic_only",
                  reason=cu_reason, reason_status=reason_status,
                  conflict_notes=conflict_notes,
                  additional_limits=additional_limits)
    elif nfc is None:
        conflict_notes_hv = holdout_false and validation_pass
        if conflict_notes_hv:
            conflict_notes.append("留出验证失败与验证通过声明并存，资格"
                                  "矛盾不得放行")
        cu = dict(base, status="unknown",
                  reason="模型未携带任何控制使用资格字段，待核实；未标"
                         "记限制不构成控制资格通过",
                  reason_status="missing",
                  conflict_notes=conflict_notes,
                  additional_limits=additional_limits)
    else:
        holdout_conflict = holdout_false and validation_pass
        if holdout_conflict:
            conflict_notes.append("留出验证失败与验证通过声明并存，资格"
                                  "矛盾不得放行")
            cu = dict(base, status="unknown",
                      reason="留出验证明确失败，同时另有验证通过声明，资"
                             "格矛盾待核实，不放行",
                      reason_status="present",
                      conflict_notes=conflict_notes,
                      additional_limits=additional_limits)
        else:
            has_scope = bool(m.get("control_scope"))
            has_approval = bool(m.get("approved_use"))
            gaps = []
            if not validation_pass:
                gaps.append("验证记录")
            if not has_scope:
                gaps.append("适用范围声明")
            if not has_approval:
                gaps.append("批准使用条件")
            if gaps:
                cu = dict(base, status="unknown",
                          reason="not_for_control 为假但缺" + "、".join(gaps)
                                 + "，假标记不单独构成控制资格",
                          reason_status="missing",
                          conflict_notes=conflict_notes,
                          additional_limits=additional_limits)
            elif not coords_declared:
                additional_limits.append("缺状态名、输入名或尺度声明，按"
                                         " I-183 model 行以诊断口径使用")
                cu = dict(base, status="diagnostic_only",
                          reason="资格声明齐备但模型未声明状态坐标与尺度，"
                                 "按规格限诊断使用",
                          reason_status="present",
                          conflict_notes=conflict_notes,
                          additional_limits=additional_limits)
            else:
                cu = dict(base, status="qualified",
                          reason="not_for_control 为假，验证记录、适用范"
                                 "围与批准使用条件齐备",
                          reason_status="present",
                          conflict_notes=conflict_notes,
                          additional_limits=additional_limits)

    if isinstance(validation, dict):
        vstat = "valid" if validation.get("pass") is True else "unknown"
        vreason = ("验证记录对象声明通过" if vstat == "valid"
                   else "验证记录对象存在但未声明通过")
    elif "holdout_pass" in m:
        if m.get("holdout_pass") is False:
            vstat, vreason = "invalid", "留出验证未通过，holdout_pass 为假"
        else:
            vstat, vreason = "unknown", "仅有留出通过布尔标记，缺验证记录对象"
    else:
        vstat, vreason = "missing", "模型未携带验证记录"
    if cu["conflict_notes"]:
        vstat = "invalid"
        vreason = "资格矛盾：" + "；".join(cu["conflict_notes"]) + "，判不合格"
    vs = {"category": "validation", "status": vstat, "reason": vreason,
          "source_tool": READER_NAME, "source_version": reader_version(),
          "generated_at": now, "input_refs": refs}
    return {"control_use": cu, "validation_status": vs,
            "conflict": bool(cu["conflict_notes"]),
            "coords_declared": coords_declared}


def read_matrix(path):
    """通用读取器。返回读数环境字典，状态维度不限。

    I-185 起同步提取并保留控制使用限制：使用资格、限制原因、验证状
    态、源模型身份与内容哈希随读数环境输出；正常数学读数不解除控制
    限制。模型未声明状态坐标与尺度时，读数按诊断口径解释。"""
    m = json.load(open(path, encoding="utf-8"))
    A0 = np.array(m["A"], dtype=float)
    Ac = [np.array(x, dtype=float) for x in m.get("Ac", [])]
    As = [np.array(x, dtype=float) for x in m.get("As", [])]
    if len(Ac) != len(As):
        raise ValueError("Ac 与 As 阶数不一致：%s" % path)
    for k in range(len(Ac)):
        if Ac[k].shape != A0.shape or As[k].shape != A0.shape:
            raise ValueError("周期项维度与 A 不一致：%s" % path)
    sha = sha256_file(path)
    qual = evaluate_control_use(m, path, sha)
    coords_declared = ("state_names" in m and "input_names" in m
                       and "scales" in m)
    return {"tag": m.get("tag", os.path.basename(os.path.dirname(path))),
            "path": path, "A0": A0, "Ac": Ac, "As": As,
            "K": len(Ac),
            "f_stroke": float(m["f_stroke_hz"]),
            "window": m.get("window"),
            "state_dim": A0.shape[0],
            "model_sha256": sha,
            "source_model": {"path": path, "tag": m.get("tag"),
                             "state_names": m.get("state_names"),
                             "input_names": m.get("input_names"),
                             "scales": m.get("scales")},
            "reader_version": reader_version(),
            "control_use": qual["control_use"],
            "validation_status": qual["validation_status"],
            "restriction_conflict": qual["conflict"],
            "reading_basis": {
                "method": "单周期状态转移矩阵四阶龙格库塔积分，输出周"
                          "期增长倍率与弗洛凯指数实部",
                "state_coordinates_declared": coords_declared,
                "note": ("模型已声明状态坐标与尺度，读数按声明口径解释"
                         if coords_declared else
                         "模型未声明状态坐标与尺度，读数按诊断口径解释，"
                         "不用于控制")}}


def build_Afun(env):
    """按读数环境构造时变系统矩阵函数。"""
    A0, Ac, As = env["A0"], env["Ac"], env["As"]
    om = 2 * np.pi * env["f_stroke"]

    def A_fun(t):
        At = A0.copy()
        for k in range(len(Ac)):
            At = At + Ac[k] * np.cos((k + 1) * om * t) \
                 + As[k] * np.sin((k + 1) * om * t)
        return At
    return A_fun


def read_floquet(env, n_steps=4000):
    """判读核：单周期状态转移矩阵积分与读数。

    I-185 起读数携带使用资格对象：环境未携带资格时输出 unknown 即待
    核实，未标记限制不构成控制资格通过；数值行为与容差不变。"""
    T = 1.0 / env["f_stroke"]
    Phi = stm_rk4(build_Afun(env), T, n_steps)
    mu, re = floquet_read(Phi, T)
    cu = env.get("control_use")
    if cu is None:
        cu = {"category": "control_use", "status": "unknown",
              "reason": "读数环境未携带控制使用资格对象，待核实；未标"
                        "记限制不构成控制资格通过",
              "reason_status": "missing",
              "conflict_notes": [], "additional_limits": [],
              "source_tool": READER_NAME,
              "source_version": reader_version(),
              "generated_at": _now_iso(),
              "input_refs": {"path": env.get("path"),
                             "sha256": env.get("model_sha256")}}
    return {"T_s": T, "mu": mu, "re_per_s": re,
            "max_mu": float(mu[-1]), "max_re": float(re[-1]),
            "control_use": cu}


def reading_text(env, r):
    cu = r.get("control_use") or env.get("control_use") \
        or {"status": "unknown"}
    status = cu.get("status", "unknown")
    mark = CONTROL_USE_MARK.get(status, CONTROL_USE_MARK["unknown"])
    suffix = "" if status == "qualified" \
        else "，读数仅限诊断用途不得用于控制"
    if r["max_mu"] < 0.999:
        stance = "稳定（每拍衰减 %.2f%%）" % ((1 - r["max_mu"]) * 100)
    elif r["max_mu"] < 1.001:
        stance = "近中性（每拍变化 %+.3f%%）" % ((r["max_mu"] - 1) * 100)
    else:
        stance = "增长（每拍增长 %.2f%%）" % ((r["max_mu"] - 1) * 100)
    return ("%s状态维 %d、周期项阶数 %d、拍周期 %.4f 秒：单拍最大增长倍率 "
            "%.5f，最大弗洛凯指数实部 %+.4f 每秒，判读 %s%s"
            % (mark, env["state_dim"], env["K"], r["T_s"], r["max_mu"],
               r["max_re"], stance, suffix))


def write_matrix_json(path, tag, A0, Ac, As, f_stroke, window):
    """按辨识产线格式写合成样例文件，验证读取链路。

    I-185 起合成样例自带禁用标记：合成矩阵是数学回归夹具，不是真实
    模型，读取器应将其评为仅诊断。"""
    obj = {"tag": tag, "csv": "synthetic", "window": window,
           "freqs_hz": [0.2, 0.5, 1.0, 1.5, 2.33, 3.0, 5.0, 8.0],
           "f_stroke_hz": f_stroke, "A": A0.tolist(),
           "B": np.zeros((A0.shape[0], 6)).tolist(),
           "C_w_moment": np.zeros_like(A0).tolist(),
           "C_d_moment": np.zeros((A0.shape[0], 6)).tolist(),
           "resonance_fn_hz": [2.5] * 6,
           "not_for_control": True,
           "not_for_control_reason": "合成样例，数学回归夹具，非真实模型"}
    if Ac:
        obj["Ac"] = [x.tolist() for x in Ac]
        obj["As"] = [x.tolist() for x in As]
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(obj, fp, ensure_ascii=False, indent=1)


def synthetic_cases(tmpdir):
    """三档合成样例：写成产物格式再读回，返回校验记录。"""
    rng = np.random.default_rng(20261005)
    f_stroke = 1.96
    cases = []
    # 样例一：三乘三常数阵
    A0 = rng.uniform(-0.8, -0.1, 3)[:, None] * np.eye(3) \
        + rng.uniform(-0.04, 0.04, (3, 3))
    p1 = os.path.join(tmpdir, "syn_const3.json")
    write_matrix_json(p1, "syn_const3", A0, [], [], f_stroke, [5.0, 30.0])
    env = read_matrix(p1)
    r = read_floquet(env)
    mu_true = np.sort(np.abs(np.linalg.eigvals(expm_eig(
        A0 / f_stroke))))
    rel = float(np.max(np.abs(r["mu"] - mu_true) / mu_true))
    cases.append({"case": "const3", "pass": rel <= TOL["const"],
                  "mu_relerr_vs_expm": rel})
    # 样例二：三乘三一阶周期阵
    Ac = [rng.uniform(-0.05, 0.05, (3, 3))]
    As = [rng.uniform(-0.05, 0.05, (3, 3))]
    p2 = os.path.join(tmpdir, "syn_period3.json")
    write_matrix_json(p2, "syn_period3", A0, Ac, As, f_stroke, [5.0, 30.0])
    env2 = read_matrix(p2)
    r2 = read_floquet(env2, 4000)
    ref = read_floquet(env2, 40000)
    rel2 = float(np.max(np.abs(r2["mu"] - ref["mu"]) / ref["mu"]))
    cases.append({"case": "period3", "pass": rel2 <= TOL["periodic"],
                  "mu_relerr_vs_fine_ref": rel2})
    # 样例三：七乘七翼态形态周期阵（体轴三元加翼态四元）
    B7 = np.zeros((7, 7))
    B7[:3, :3] = A0
    B7[0, 3] = 2.5
    B7[1, 4] = -2.0
    B7[3, 3] = -6.0
    B7[4, 4] = -6.5
    B7[5, 5] = -7.0
    B7[6, 6] = -5.5
    Ac7 = [np.zeros((7, 7))]
    As7 = [np.zeros((7, 7))]
    Ac7[0][:3, 3:7] = rng.uniform(-0.08, 0.08, (3, 4))
    As7[0][3:7, :3] = rng.uniform(-0.08, 0.08, (4, 3))
    p3 = os.path.join(tmpdir, "syn_wing7.json")
    write_matrix_json(p3, "syn_wing7", B7, Ac7, As7, f_stroke, [5.0, 30.0])
    env3 = read_matrix(p3)
    r3 = read_floquet(env3, 4000)
    ref3 = read_floquet(env3, 40000)
    rel3 = float(np.max(np.abs(r3["mu"] - ref3["mu"]) / ref3["mu"]))
    cases.append({"case": "wing7", "pass": rel3 <= TOL["periodic"],
                  "mu_relerr_vs_fine_ref": rel3,
                  "reading": reading_text(env3, r3)})
    return cases


def _tiny_model(tmpdir, name, extra):
    """写三乘三最小合成模型并叠加资格字段，供正反例使用。"""
    rng = np.random.default_rng(20261012)
    A0 = -0.5 * np.eye(3) + rng.uniform(-0.02, 0.02, (3, 3))
    p = os.path.join(tmpdir, name + ".json")
    write_matrix_json(p, name, A0, [], [], 1.96, [5.0, 30.0])
    m = json.load(open(p, encoding="utf-8"))
    for k, v in extra.items():
        if v is None:
            m.pop(k, None)
        else:
            m[k] = v
    with open(p, "w", encoding="utf-8") as fp:
        json.dump(m, fp, ensure_ascii=False, indent=1)
    return p


def i185_selftest(tmpdir):
    """I-185 六情形正反例：资格评估与输出限制逐例机器可查。

    覆盖判据一、二、五的正反两面：上游禁用保留原因且输出仅诊断；
    缺资格字段返回待核实；假标记不单独放行；资格齐备才评合格；资
    格矛盾保留禁用并记不合格；裸环境经判读核输出待核实。"""
    cases = []
    reason = "留出验证未过，控制律禁读直至结构升级重估"
    # 例一 反例：资格字段齐备但无状态坐标与尺度声明，按规格降为仅诊断
    p = _tiny_model(tmpdir, "q_ok", {
        "not_for_control": False,
        "validation": {"pass": True, "record": "selftest"},
        "control_scope": "自测样例范围", "approved_use": "仅自测批准"})
    env = read_matrix(p)
    r = read_floquet(env)
    txt = reading_text(env, r)
    cases.append({"case": "qualified_without_coords",
                  "expect": "diagnostic_only",
                  "status": env["control_use"]["status"],
                  "pass": env["control_use"]["status"] == "diagnostic_only"
                          and "【仅诊断·不得用于控制】" in txt
                          and len(env["control_use"]["additional_limits"]) > 0})
    # 例二 反例：上游禁用 → 仅诊断，原因原文保留
    p = _tiny_model(tmpdir, "q_disabled", {
        "not_for_control": True, "not_for_control_reason": reason,
        "holdout_pass": False})
    env = read_matrix(p)
    r = read_floquet(env)
    txt = reading_text(env, r)
    cases.append({"case": "upstream_disabled",
                  "expect": "diagnostic_only",
                  "status": env["control_use"]["status"],
                  "reason_kept": env["control_use"]["reason"] == reason,
                  "validation_status": env["validation_status"]["status"],
                  "pass": env["control_use"]["status"] == "diagnostic_only"
                          and env["control_use"]["reason"] == reason
                          and env["validation_status"]["status"] == "invalid"
                          and "【仅诊断·不得用于控制】" in txt
                          and "控制可用" not in txt})
    # 例三 反例：缺资格字段 → 待核实，未标记不等于合格
    p = _tiny_model(tmpdir, "q_nofield", {
        "not_for_control": None, "not_for_control_reason": None})
    env = read_matrix(p)
    r = read_floquet(env)
    txt = reading_text(env, r)
    cases.append({"case": "field_missing",
                  "expect": "unknown", "status": env["control_use"]["status"],
                  "pass": env["control_use"]["status"] == "unknown"
                          and "待核实" in txt})
    # 例四 反例：假标记单独放行被拒 → 待核实
    p = _tiny_model(tmpdir, "q_false_only", {"not_for_control": False})
    env = read_matrix(p)
    r = read_floquet(env)
    cases.append({"case": "false_not_enough",
                  "expect": "unknown", "status": env["control_use"]["status"],
                  "pass": env["control_use"]["status"] == "unknown"})
    # 例五 反例：禁用与验证通过并存 → 矛盾，保留禁用并记不合格
    p = _tiny_model(tmpdir, "q_conflict", {
        "not_for_control": True, "not_for_control_reason": "禁用",
        "validation": {"pass": True, "record": "selftest"}})
    env = read_matrix(p)
    r = read_floquet(env)
    cases.append({"case": "conflict_kept_disabled",
                  "expect": "diagnostic_only",
                  "status": env["control_use"]["status"],
                  "conflict": env["restriction_conflict"],
                  "validation_status": env["validation_status"]["status"],
                  "pass": env["control_use"]["status"] == "diagnostic_only"
                          and env["restriction_conflict"] is True
                          and env["validation_status"]["status"] == "invalid"})
    # 例六 管线式裸环境：判读核自动补待核实，不破坏既有调用方
    env = {"tag": "bare", "path": "构造环境", "A0": -0.5 * np.eye(3),
           "Ac": [], "As": [], "K": 0, "f_stroke": 1.96,
           "window": None, "state_dim": 3}
    r = read_floquet(env)
    cases.append({"case": "bare_env_unknown",
                  "expect": "unknown", "status": r["control_use"]["status"],
                  "pass": r["control_use"]["status"] == "unknown"})

    # ── i185-2 返修补例，对齐 Doro 2026-10-13 五反例 ──
    full_decl = {
        "validation": {"pass": True, "source": "selftest"},
        "control_scope": {"states": ["p", "q"], "operating_point": "x"},
        "approved_use": {"decision": "selftest-only"},
        "state_names": ["p", "q"], "input_names": ["u"],
        "scales": {"state": [1.0, 1.0], "input": [1.0]}}
    orig_reason = "原始限制：本模型留出误差超出阈值；禁止用于控制。"
    # 例七 反例（复核反例一）：禁用与验证通过冲突，原始原因原文保留
    p = _tiny_model(tmpdir, "q_conflict_reason", dict(
        full_decl, not_for_control=True, not_for_control_reason=orig_reason))
    env = read_matrix(p)
    cu = env["control_use"]
    cases.append({"case": "conflict_preserves_original_reason",
                  "expect": "diagnostic_only", "status": cu["status"],
                  "reason_kept": cu["reason"] == orig_reason,
                  "conflict_notes": len(cu["conflict_notes"]),
                  "pass": cu["status"] == "diagnostic_only"
                          and cu["reason"] == orig_reason
                          and len(cu["conflict_notes"]) > 0
                          and "矛盾" not in cu["reason"]})
    # 例八 反例（复核反例二）：禁用字段为数字零，不作合法假值
    p = _tiny_model(tmpdir, "q_zero_flag", dict(full_decl, not_for_control=0))
    env = read_matrix(p)
    cu = env["control_use"]
    cases.append({"case": "malformed_false_zero",
                  "expect": "unknown", "status": cu["status"],
                  "pass": cu["status"] == "unknown"})
    # 例九 反例（复核反例三）：禁用字段为空列表，不作合法假值
    p = _tiny_model(tmpdir, "q_empty_flag",
                    dict(full_decl, not_for_control=[]))
    env = read_matrix(p)
    cu = env["control_use"]
    cases.append({"case": "malformed_false_empty",
                  "expect": "unknown", "status": cu["status"],
                  "pass": cu["status"] == "unknown"})
    # 例十 反例（复核反例四）：留出失败与验证通过并存，不得放行
    p = _tiny_model(tmpdir, "q_holdout_conflict", dict(
        full_decl, not_for_control=False, holdout_pass=False))
    env = read_matrix(p)
    cu, vs = env["control_use"], env["validation_status"]
    cases.append({"case": "holdout_validation_conflict",
                  "expect": "unknown", "status": cu["status"],
                  "conflict": env["restriction_conflict"],
                  "validation_status": vs["status"],
                  "pass": cu["status"] == "unknown"
                          and env["restriction_conflict"] is True
                          and vs["status"] == "invalid"})
    # 例十一 反例（复核反例五）：缺状态名输入名尺度，机器与文字一致降诊断
    decl_wo_coords = {k: v for k, v in full_decl.items()
                      if k not in ("state_names", "input_names", "scales")}
    p = _tiny_model(tmpdir, "q_no_coords",
                    dict(decl_wo_coords, not_for_control=False))
    env = read_matrix(p)
    r = read_floquet(env)
    txt = reading_text(env, r)
    cu = env["control_use"]
    cases.append({"case": "missing_coords_consistent",
                  "expect": "diagnostic_only", "status": cu["status"],
                  "text_diagnostic": "【仅诊断·不得用于控制】" in txt,
                  "pass": cu["status"] == "diagnostic_only"
                          and "【仅诊断·不得用于控制】" in txt
                          and len(cu["additional_limits"]) > 0})
    # 例十二 正例：齐备资格加完整坐标声明评 qualified，机器与文字一致放行
    p = _tiny_model(tmpdir, "q_ok_full",
                    dict(full_decl, not_for_control=False))
    env = read_matrix(p)
    r = read_floquet(env)
    txt = reading_text(env, r)
    cu = env["control_use"]
    cases.append({"case": "qualified_with_coords",
                  "expect": "qualified", "status": cu["status"],
                  "scope_echo": bool(env["source_model"]["state_names"]),
                  "pass": cu["status"] == "qualified"
                          and "不得用于控制" not in txt
                          and env["reading_basis"]["state_coordinates_declared"]
                          is True})
    return cases


UNCOVERED_CONSUMERS = [
    {"consumer": "airworthiness/steps.py step6_floquet",
     "territory": "L2 流程",
     "note": "判读核返回值自动携带资格对象，该工具的报告与 verdict "
             "输出路径归二队，本项未改，限制在该路径的落款待后续事项"},
    {"consumer": "ltv/hss_i102_stab.py",
     "territory": "L4，属 I-102 工具",
     "note": "独立工具与输出路径，本项未改，列待后续事项"},
    {"consumer": "tools/ident_fd_estimator.py 与其模型输出",
     "territory": "L1 生产方",
     "note": "生产方字段补齐需求另行登记，本项不跨领地改"},
    {"consumer": "控制器与遥测链路",
     "territory": "L1 与 L2",
     "note": "现无对模型矩阵的直接消费，本项不涉及，登记备查"},
]


def restriction_pipeline_check(real_entries):
    """判据五：限制沿本项读取与输出环节逐项检查。

    补齐决定 2026-10-13（logs/hq_intake/doro_i185_rework_review_
    20261013/复验与补齐决定.md）：限制与待核输入不得允许使用；正常
    样例按已确认资格、范围与批准条件保留状态；工具检查结果只表达
    工具自身检查成败，不授予任何新模型或飞行能力。"""
    checks = []
    checks.append({
        "step": "读取器逐模型携带使用资格与验证状态对象",
        "pass": all(e["control_use"] and e["validation_status"]
                    for e in real_entries)})
    checks.append({
        "step": "受限与待核输入的输出带不得用于控制标记且机器资格不"
                "为可用",
        "pass": all(("不得用于控制" in e["reading"])
                    and (e["control_use"]["status"] != "qualified")
                    and (e["control_usable"] is False)
                    for e in real_entries
                    if e["control_use"]["status"] != "qualified")})
    checks.append({
        "step": "正常合格输入按已确认资格范围保留可用标记",
        "pass": all(e["control_usable"] is True
                    and e["control_use"]["status"] == "qualified"
                    for e in real_entries
                    if e["control_use"]["status"] == "qualified")})
    checks.append({
        "step": "逐例文本与机器资格一致，不含未授予的可用结论",
        "pass": all(e["control_usable"]
                    == (e["control_use"]["status"] == "qualified")
                    for e in real_entries)})
    checks.append({
        "step": "输出时内容哈希与读取时一致",
        "pass": all(e["hash_match_at_output"] for e in real_entries)})
    return checks


def main(argv):
    spec_sha = hashlib.sha256(open(SPEC_PATH, "rb").read()).hexdigest()
    tmpdir = tempfile.mkdtemp(prefix="i079syn_")
    cases = synthetic_cases(tmpdir)
    i185_cases = i185_selftest(tmpdir)
    for c in cases:
        print("样例 %s：通过=%s %s" % (
            c["case"], c["pass"],
            {k: "%.2e" % v for k, v in c.items()
             if isinstance(v, float)}))
    for c in i185_cases:
        print("资格例 %s：期望=%s 实得=%s 通过=%s" % (
            c["case"], c["expect"], c["status"], c["pass"]))
    real = []
    targets = argv if argv else [os.path.join(_ROOT, p) for p in REHEARSAL]
    for p in targets:
        env = read_matrix(p)
        r = read_floquet(env)
        txt = reading_text(env, r)
        cross = None
        if env["K"] == 0:
            mu_true = np.sort(np.abs(np.linalg.eigvals(expm_eig(
                env["A0"] / env["f_stroke"]))))
            cross = float(np.max(np.abs(r["mu"] - mu_true) / mu_true))
        sha_out = sha256_file(p)
        real.append({"tag": env["tag"], "path": p,
                     "state_dim": env["state_dim"], "K": env["K"],
                     "f_stroke": env["f_stroke"], "window": env["window"],
                     "mu": r["mu"].tolist(), "re_per_s": r["re_per_s"].tolist(),
                     "max_mu": r["max_mu"], "max_re": r["max_re"],
                     "reading": txt, "cross_vs_expm": cross,
                     "model_sha256": env["model_sha256"],
                     "hash_match_at_output": sha_out == env["model_sha256"],
                     "reader_version": env["reader_version"],
                     "control_use": env["control_use"],
                     "validation_status": env["validation_status"],
                     "restriction_conflict": env["restriction_conflict"],
                     "reading_basis": env["reading_basis"],
                     "control_usable":
                         env["control_use"]["status"] == "qualified"})
        print("[%s] %s%s" % (env["tag"], txt,
                             "，交叉验证 %.1e" % cross if cross else ""))
    pipe_checks = restriction_pipeline_check(real)
    for c in pipe_checks:
        print("限制环节检查 %s：通过=%s" % (c["step"], c["pass"]))
    verdict = {
        "item": "I-079",
        "item_i185": "I-185 周期分析读取与输出保留模型控制使用限制",
        "spec_sha256": spec_sha,
        "reader_version": reader_version(),
        "tolerances": TOL,
        "synthetic_cases": cases,
        "i185_selftest_cases": i185_cases,
        "restriction_pipeline_check": pipe_checks,
        "uncovered_consumers": UNCOVERED_CONSUMERS,
        "control_use_conclusion":
            "本工具不产生控制资格通过结论；模型控制资格由验证来源与上"
            "游限制共同确定，本工具只评估并保留",
        "verdict_meaning":
            "verdict_pass 仅表示诊断工具自身合成样例回归、资格正反例与"
            "读取链路通过，不构成模型控制资格、数据资格或飞行能力结论",
        "real_readings": real,
        "readiness_note": "已落地 I-055 产物当班出真实判读，当日时限就绪",
        "verdict_pass": all(c["pass"] for c in cases)
                        and all(c["pass"] for c in i185_cases)
                        and all(c["pass"] for c in pipe_checks)
                        and len(real) > 0,
    }
    out_dir = os.path.join(_HERE, "i079_results",
                           time.strftime("i079_%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "verdict.json"), "w",
              encoding="utf-8") as fp:
        json.dump(verdict, fp, ensure_ascii=False, indent=2)
    rep = ["# I-079 判读工具适配验收报告", "",
           "读取 I-055 拟合产物格式（matrix.json，状态维度不限，周期项有则"
           "读），判读核与 I-047 工具同口径。I-185 起控制使用限制沿读取与"
           "输出全程保留。", "",
           "## 合成样例", "",
           "| 样例 | 校验 | 结果 |", "|---|---|---|"]
    for c in cases:
        key = [k for k in c if k.endswith(("expm", "ref"))][0]
        rep.append("| %s | %s = %.2e 对容差 %s | %s |" % (
            c["case"], key, c[key],
            "1e-10" if c["case"] == "const3" else "1e-9",
            "过" if c["pass"] else "不过"))
    rep += ["", "## 使用资格与限制（I-185）", "",
            "读取器按 I-183 冻结规则评估模型控制使用资格，六情形正反例：", "",
            "| 情形 | 期望 | 实得 | 结果 |", "|---|---|---|---|"]
    for c in i185_cases:
        rep.append("| %s | %s | %s | %s |" % (
            c["case"], c["expect"], c["status"],
            "过" if c["pass"] else "不过"))
    rep += ["", "评估规则四条：上游禁用保留原因原文且输出仅诊断；缺资格"
                "字段返回待核实；禁用假标记不单独放行，假标记须验证记录、"
                "适用范围与批准使用条件齐备才评合格；资格矛盾保留禁用并"
                "记不合格。未标记限制不构成控制资格通过，正常数学读数不"
                "解除控制限制，本工具不产生控制资格通过结论。", ""]
    rep += ["## 限制环节逐项检查（判据五）", ""]
    for c in pipe_checks:
        rep.append("- %s：%s" % (c["step"], "过" if c["pass"] else "不过"))
    rep += ["", "## 未覆盖控制消费者清单", "",
            "本项只覆盖 ltv/hss_i079_adapter.py 的读取与输出路径，以下消"
            "费者不在本项覆盖内，不宣称全项目限制传递已完成：", ""]
    for u in UNCOVERED_CONSUMERS:
        rep.append("- %s（%s）：%s" % (u["consumer"], u["territory"],
                                       u["note"]))
    rep += ["", "## 已落地产物真实判读（就绪演练）", ""]
    for r in real:
        rep.append("- **%s**（维 %d，阶 %d，窗 %s，拍频 %.3f，模型哈希 "
                   "%s…）：%s%s"
                   % (r["tag"], r["state_dim"], r["K"], r["window"],
                      r["f_stroke"], r["model_sha256"][:12], r["reading"],
                      "，与矩阵指数交叉验证 %.1e" % r["cross_vs_expm"]
                      if r["cross_vs_expm"] is not None else ""))
    rep += ["", "## 生产方补齐需求（交 Doro 登记，不跨领地改）", "",
            "三份在案真实模型均未声明 state_names、input_names、scales "
            "与 validation 对象，读数按诊断口径解释。请求辨识生产方按 "
            "I-183 字段表补齐状态坐标、输入清单、尺度与验证记录对象；补"
            "齐前资格保持待核实或仅诊断。", "",
            "机器可读判 verdict.json 同目录，含读取器版本自哈希与逐模型"
            "内容哈希。", ""]
    with open(os.path.join(out_dir, "report.md"), "w",
              encoding="utf-8") as fp:
        fp.write("\n".join(rep))
    print("产出目录：%s" % out_dir)
    return verdict


if __name__ == "__main__":
    v = main(sys.argv[1:])
    sys.exit(0 if v["verdict_pass"] else 1)
