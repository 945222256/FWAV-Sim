# -*- coding: utf-8 -*-
"""I-204 v2 verdict 生成器：把规格文书 v2 的主张逐条对仓库机器核验。

核验七类：Doro 反例数值复算、来源行号内容命中、v1 缺陷表述删除核验、
空集禁令、冻结文件未改动、语言检查、零新增在环运行。
输出同目录 verdict.json。
"""
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_ROOT, "items_registry"))
sys.path.insert(0, _HERE)
import langcheck  # noqa: E402

checks = []


def ck(name, passed, detail):
    checks.append({"check": name, "pass": bool(passed), "detail": detail})


def read(path):
    with open(os.path.join(_ROOT, path), "r", encoding="utf-8",
              errors="replace") as f:
        return f.read()


# 一、Doro 反例数值复算；counterexamples.py 未改动时复用上次已通过的
# 数值结果，不重跑耗时样例（机主 2026-10-13 接续指令）
import hashlib
import counterexamples  # noqa: E402
ce_sha = hashlib.sha256(
    open(os.path.join(_HERE, "counterexamples.py"), "rb").read()).hexdigest()
prev = None
if os.path.exists(os.path.join(_HERE, "verdict.json")):
    try:
        prev = json.load(open(os.path.join(_HERE, "verdict.json"),
                              encoding="utf-8"))
    except Exception:
        prev = None
prev_ce_sha = (prev or {}).get("counterexamples_sha256")
prev_checks = {c.get("check"): c for c in (prev or {}).get("checks", [])
               if str(c.get("check", "")).startswith("数值反例复算")}
if prev_ce_sha == ce_sha and len(prev_checks) >= 6         and all(c["pass"] for c in prev_checks.values()):
    for name, c in sorted(prev_checks.items()):
        ck(name, True, "缓存复用（脚本未改，sha256 前缀 %s…），不重跑耗时样例"
           % ce_sha[:12])
else:
    for r in counterexamples.main():
        ck("数值反例复算 %s" % r["case"], r["assert_pass"],
           json.dumps({k: v for k, v in r.items() if k != "case"},
                      ensure_ascii=False)[:220])

# 二、来源行号命中
fc = read("modules/flight_controller.py").splitlines()
ck("控制器返回含 wavegen_freq 键",
   any("wavegen_freq" in ln for ln in fc[1320:1332]),
   "modules/flight_controller.py 1323 至 1331 行")
wa = read("modules/wing_actuation.py").splitlines()
ck("生产关节驱动读 freq 键",
   any('ap_out["freq"]' in ln for ln in wa[80:88]),
   "modules/wing_actuation.py 83 行附近")
ck("气动侧左右差动注入在案",
   any("wing_left.set_frequency(_fc + _dfq)" in ln for ln in wa)
   and any("wing_right.set_frequency(_fc - _dfq)" in ln for ln in wa),
   "modules/wing_actuation.py 差动注入段 96 至 107 行")
ck("两翼相位为分别积分状态在案",
   "相位积分状态" in read("modules/aero/kinematics.py"),
   "modules/aero/kinematics.py 相位积分状态注释")
st = read("airworthiness/steps.py").splitlines()
ck("S6 周期矩阵只含被控对象声明",
   any("只含被控对象" in ln for ln in st[517:524]),
   "airworthiness/steps.py 519 至 521 行")

# 三、v1 缺陷表述删除核验与空集禁令
spec = read("ltv/i204_stability_scope_20261013/spec.md")
ck("半拍保证删除声明在文",
   "删除，不再作为现行保证" in "".join(spec.split()),
   "v2 第六节载明删除，跨行归并后检索")
ck("目标不变集写未定义待核而非空集",
   "未定义，待核" in spec and "全项目无在案目标不变集定义" not in spec,
   "v2 第一节按返修要求改写")
ck("全机左右相位恒等表述撤销",
   "不写全机左右相位恒等" in spec,
   "v2 第五节按返修要求撤销")
ck("角速度世界系契约声明在文",
   "原始角速度" in spec and "世界系" in spec and "显式旋转换算" in spec,
   "第一节 S1 转动行按二版决定一更正")
ck("dfreq 单位赫兹声明在文",
   "df_cmd 频率差动量单位赫兹" in spec,
   "第一节 S4 行按二版决定一更正")
ck("复现标准锁与物理锁定分列更正在文",
   "复现标准锁只保存复现条件，不锁定任何机体位置与姿态自由度" in spec,
   "第一节约束表按二版决定一更正")
spec_flat2 = "".join(spec.split())
ck("未声明即未锁错误措辞已移除",
   "未声明即未锁" not in spec_flat2,
   "第三版复核决定指出的条件缺失转已确认表述删除")
ck("锁定判定三例文书对照在文",
   "锁定情况登记为缺失" in spec_flat2
   and "登记为未知" in spec_flat2
   and "才登记未锁" in spec_flat2
   and "未声明时锁定情况为缺失或未知" in spec_flat2,
   "缺字段缺失、信息不足未知、生效来源证实未锁三例按第三版复核决定补齐")
ck("返回映射矩阵式与横截条件在文",
   "外积" in spec and "横截条件" in spec
   and "链式法则错误，本版撤销更正" in spec,
   "第三节按二版决定二更正")
spec_flat = "".join(spec.split())
ck("发生器候选修正声明在文",
   "末尾的半径因子不可省略" in spec_flat
   and "原点是不收敛到目标圆的平衡点" in spec_flat
   and "局部收敛率为二kappa乘幅值平方" in spec_flat,
   "第四节按二版决定三与探针读数更正，跨行归并后检索")
ck("离散同源衔接声明在文",
   "离散转移矩阵取连续生成矩阵按明确步长的矩阵指数" in spec
   and "v2 用同一数字分别充当连续生成矩阵与离散转移矩阵的写法撤销" in spec,
   "第四节按二版决定四更正")
ck("存活集与一阶近似声明在文",
   "不是已经证明的正向不变集" in spec
   and "一阶线性近似" in spec,
   "第三节与第六节按二版决定五更正")
ck("查重按完整来源表更新在文",
   "mainline_recovery" in spec and "三件各限其对象" .replace(" ","") in spec.replace(" ","")
   or ("mainline_recovery" in spec and "三者都不能充当完整飞行闭环" in spec),
   "第四节按二版决定六更新")
ck("未找到断言附检索范围",
   spec.count("检索范围") >= 2,
   "第四节与第六节各附查重范围声明")

# 四、冻结文件未改动
for fp in ("ltv/hybrid_flight_spec.json", "ltv/hss_spec.json",
           "ltv/wavegen_prereg_20261009.md", "ltv/i172_wavegen_prereg.md",
           "modules/wing_actuation.py", "config/sim_config.py"):
    d = subprocess.run(["git", "diff", "HEAD", "--", fp],
                       cwd=_ROOT, capture_output=True, text=True)
    ck("冻结或生产文件未改动 %s" % os.path.basename(fp),
       d.stdout.strip() == "", "git diff 为空即未改")
d_fc = subprocess.run(["git", "diff", "HEAD", "--",
                       "modules/flight_controller.py"],
                      cwd=_ROOT, capture_output=True, text=True).stdout
ck("控制器工作区改动为 I-138 在案预算实现而非本项改动",
   ("ALT_BUDGET" in d_fc) and ("WAVEGEN" not in d_fc),
   "工作区既有改动含 ALT_BUDGET 键属 I-138 待领地确认件，"
   "不含发生器相关改动")

# 五、语言检查
lc = langcheck.scan_file(os.path.join(_HERE, "spec.md"))
ck("规格文书语言检查", not lc, "无违规" if not lc else str(lc[:3]))

# 六、零新增在环运行：目录只含文书、核验与反例脚本，无结果数据
entries = [e for e in os.listdir(_HERE) if e != "__pycache__"]
allowed = {"build_verdict.py", "spec.md", "verdict.json",
           "counterexamples.py"}
ck("零新增在环运行",
   set(entries) <= allowed
   and all(os.path.isfile(os.path.join(_HERE, e)) for e in entries),
   "目录仅含 %s，缓存目录除外" % sorted(entries))

verdict = {
    "item": "I-204",
    "revision": "v3 第三版修订，对应 Doro 2026-10-13 第二版复验决定一、三、四",
    "session": "sess_0d49b7dd-70de-4141-b478-b94ad2d2097a",
    "deliverable": "spec.md v2 七节，counterexamples.py 数值反例样例",
    "rework_map": [
        {"decision": "二版决定一", "section": "第一节 v3：角速度原始世界系与分析体轴分列并保留旋转依据，dfreq 单位改赫兹，复现标准锁与物理锁定分列更正，I-102 十九态含控制器状态清单入表"},
        {"decision": "二版决定二", "section": "第三节 v3：返回映射导数矩阵式 DP=Dphi+f(P)grad(t_r)转置，维度与横截条件与切空间投影，链式法则错误撤销，可核查例子数值差分对照"},
        {"decision": "二版决定三", "section": "第四节 v3：真实径向方程含半径因子，适用域限正半径有界区域，原点不收敛声明，局部收敛率 2kA²，真实参考核核验非单位幅值与原点"},
        {"decision": "二版决定四", "section": "第四节 v3：同一连续对象按 exp(A·dt) 同源衔接，每秒与每步权重分列，双步长趋近验证"},
        {"decision": "二版决定五", "section": "第三节与第六节 v3：存活集非已证不变集声明，事件时刻一阶近似与有界误差区分"},
        {"decision": "二版决定六", "section": "第四节 v3：查重按 Anni 完整历史来源表更新，三件李雅普诺夫相关资产各限对象"},
    ],
    "key_decisions": [
        "角速度原始量为世界系，分析体轴量须显式转换并保留依据",
        "dfreq 单位为赫兹不是弧度每秒",
        "复现标准锁不锁定机体位置姿态，物理锁定仅以 physical_constraints 为准",
        "返回映射导数为固定时间雅可比加返回点速度向量与返回时刻梯度的外积，横截条件不为零",
        "外驱固定时间映射须驱动周期独立于被扰动状态",
        "真实径向方程含半径因子，候选导数含半径，原点不收敛到目标圆，适用域限正半径有界区域",
        "局部收敛率为二 kappa 乘幅值平方，真实参考核实测二点零零零八对二",
        "离散口径由连续对象按矩阵指数同源衔接，离散解随步长缩小趋近连续解",
        "段谱半径乘积非一般上界，上界限于同一诱导范数，体积增长限行列式口径",
        "有限拍存活集为数值刻画非已证不变集，事件时刻影响区分一阶近似与有界误差",
        "查重按 Anni 完整来源表更新：三件李雅普诺夫相关资产各限对象",
    ],
    "checks": checks,
    "counterexamples_sha256": ce_sha,
    "verdict_pass": all(c["pass"] for c in checks),
    "verdict_meaning": "verdict_pass 仅表示本项 v2 文书主张与来源机器核验、"
                       "数值反例复算、语言检查与冻结生产文件未改全部成立，"
                       "不构成任何模型、发生器或全机的稳定性通过结论；本项"
                       "不自行关闭，候 Doro 复核",
}

with open(os.path.join(_HERE, "verdict.json"), "w", encoding="utf-8") as f:
    json.dump(verdict, f, ensure_ascii=False, indent=1)
print("checks:", len(checks), "pass:", verdict["verdict_pass"])
for c in checks:
    if not c["pass"]:
        print("  [不过] %s -> %s" % (c["check"], c["detail"][:160]))
