# -*- coding: utf-8 -*-
"""lab_payload.py — 参数实验室陈列载荷构建（I-130 阶段一，本地与云端共用）。

输入是参数登记库形状的 params 字典与退役档案列表，输出页面载荷。
数据纪律：本模块不存任何参数值；级别与教学文本是陈列元数据，
逐条标注来源（graph 字段优先，规则表暂定并明示）。
设计依据：需求定稿 D-047、设计方案四稿、参数分级架构 D-045、
I-127 接口契约。仅使用 Python 标准库。
"""
import datetime
import json
import re

# ── 级别与区（D-045 第一节；四稿第三章三之一） ─────────────────────
LEVEL_NAMES = {
    0: "第零级 约定环境量", 1: "第一级 物理值", 2: "第二级 作动与结构特性",
    3: "第三级 气动系数", 4: "第四级 力律结构", 5: "第五级 配平点族",
    6: "第六级 辨识参数", 7: "第七级 控制参数", 8: "第八级 科目判据",
    9: "第九级 学习资产",
}

ZONES = [
    {"id": "core", "no": 1, "title": "核心区", "levels": [0, 1, 2], "layout": "core",
     "lead": "这一区回答：这架飞机是什么。中央是整机三维模型，左右两列是不改硬件就不变的底层参数。"},
    {"id": "actuation", "no": 2, "title": "作动与结构区", "levels": [2], "layout": "grid",
     "lead": "这一区回答：它的肌肉与骨骼怎样响应。执行链的固有频率、阻尼、延迟与拍动驱动增益全部在这里。"},
    {"id": "aero", "no": 3, "title": "气动与力律区", "levels": [3, 4], "layout": "grid",
     "lead": "这一区回答：我们对它的空气动力知道多少、还猜着多少。每个系数都带有效域标注，这是工程诚实的部分。"},
    {"id": "flight", "no": 4, "title": "飞行数据区", "levels": [5, 6], "layout": "grid",
     "lead": "这一区回答：它怎样被一步步调教到能飞。配平点是力与力矩平衡的解，辨识参数是从飞行数据里测出的规律。"},
    {"id": "control", "no": 5, "title": "控制区", "levels": [7, 8, 9], "layout": "grid",
     "lead": "这一区回答：谁在开这架飞机。控制增益按通道分组陈列配比，科目判据是必须通过的主线关卡。"},
]

SAMPLES = [
    {"id": "215g", "name": "二百一十五克现行构型", "state": "active",
     "tagline": "全机参数纵览", "locked": False},
    {"id": "700g", "name": "七百克未来样本", "state": "locked",
     "tagline": "下一形态", "locked": True,
     "hint": "下一形态未解锁：第一级全部待取新值，第三级气动系数超出有效域须重建，第五至第七级全部重算。完整逐级工作量清单见参数分级架构第三节。"},
]

# 需求定稿第九节第一项的指挥部暂定值（机主未推翻）。
PLACEHOLDERS = [
    {"level": 1, "subclass": "翼型剖面",
     "text": "翼型剖面子类占位：当前模型未启用，对接实物前必补。",
     "status": "缺失"},
    {"level": 2, "subclass": "柔度",
     "text": "柔度子类占位：当前模型未启用，对接实物前必补。",
     "status": "缺失"},
]

# ── 暂定定级规则表（盘点落库前的前置陈列用） ────────────────────────
# 依据：D-045 各级定义与 docs/params_migration_map.md 的六域归属。
# graph.level 在册时一律优先；规则定级的参数在页面上明示“暂定待盘点”。
_RULES = [
    (r"^aero\.rho_air$", 0, "D-045 第零级：环境常量"),
    (r"^vehicle\.", 1, "D-045 第一级：样本固有物理量"),
    (r"^link\.", 1, "D-045 第一级：URDF 构件质量惯量"),
    (r"^base_link\.", 1, "D-045 第一级：URDF 构件质量惯量"),
    (r"^aero\.geometry\.", 1, "D-045 第一级：几何尺寸"),
    (r"^aero\.fuselage\.frontal_area$", 1, "D-045 第一级：几何尺寸"),
    (r"^wave\.", 2, "D-045 第二级：拍动驱动与波形机构特性"),
    (r"^ctrl\.drive", 2, "D-045 第二级：拍动驱动增益"),
    (r"^aero\.", 3, "D-045 第三级：气动系数"),
    (r"^ctrl\.ref\.", 5, "D-045 第五级：配平参考状态（I-127 种子链同款）"),
    (r"^probe\.", 6, "D-045 第六级：辨识激励与试验族"),
    (r"^search\.", 6, "D-045 第六级：辨识搜索线（迁移映射表激励探针域）"),
    (r"^ctrl\.", 7, "D-045 第七级：控制参数"),
]

# ── 教学内容包（陈列元数据，非参数值） ─────────────────────────────
# family 级讲解：按参数族给一句话解释与教学四块的族级文本；
# params 级条目覆盖族级（逐参数讲解）。
_FAMILY = {
    "aero.rho_air": {
        "explanation": "飞行实验约定的空气密度，海平面标准大气值。",
        "definition": "空气密度是单位体积空气的质量，千克每立方米。本机的气动力全部与它成正比。",
        "increase": "密度改大等效于空气更“稠”：同速度下升力与阻力同步增大，模型里它随实验地点与高度变化。"},
    "vehicle.mass": {
        "explanation": "全机或部件的质量，硬件不变则不变。",
        "definition": "质量是抵抗加速度的物理量。总质量决定维持飞行所需的总升力。",
        "increase": "质量增大需要更大升力，拍动更费力，续航与机动性下降。"},
    "vehicle.inertia": {
        "explanation": "绕各体轴的转动惯量，抵抗姿态变化的物理量。",
        "definition": "转动惯量是抵抗角加速度的物理量，等于质量乘以到转轴距离的平方对全机积分。",
        "increase": "惯量增大时同样的力矩产生的角加速度变小，姿态响应变慢但更稳。"},
    "link": {
        "explanation": "URDF 构件的质量属性，骨架模型的硬件本体。",
        "definition": "连杆是骨架模型里的刚体构件，其质量与惯量从 URDF 文件直读。",
        "increase": "构件质量增大会同时抬高总质量与转动惯量。"},
    "aero.geometry": {
        "explanation": "机翼几何尺寸：翼展、弦长与离散条数。",
        "definition": "翼展是左右翼尖之间的距离，弦长是机翼顺气流方向的宽度。",
        "increase": "翼面积增大升力变大，但惯量与重量也随之上升。"},
    "wave.kinematics": {
        "explanation": "拍动波形的运动学参数：频率、幅值、相位形状。",
        "definition": "拍动运动学描述翅膀按什么轨迹与节奏拍动，是作动指令的形状层。",
        "increase": "拍频或幅值增大出力变大，但驱动功率与结构负荷同步上升。"},
    "wave.drive": {
        "explanation": "拍动驱动回路的驱动侧参数。",
        "definition": "驱动参数描述拍动指令怎样变成关节目标位置。",
        "increase": "驱动更激进则跟踪更快，超调与振荡风险上升。"},
    "ctrl.drive": {
        "explanation": "拍动关节的驱动增益（D-045 第二级明列项）。",
        "definition": "关节驱动增益是仿真关节执行器的刚度与阻尼整定值。",
        "increase": "增益增大跟踪更硬，过大会在离散步长下失稳。"},
    "aero.wing": {
        "explanation": "机翼气动系数：升阻力曲线的拟合常数。",
        "definition": "机翼气动系数把拍动运动映射为升力与阻力，取自 Robofly 实测极曲线拟合。",
        "increase": "升力系数增大同拍动下出力更大，有效域外的外推不可信。"},
    "aero.tail": {
        "explanation": "尾翼气动系数：面积、力臂、法向力斜率。",
        "definition": "尾翼用平板法向力模型产生姿态控制力矩，力臂是尾压心到重心的距离。",
        "increase": "面积或力臂增大力矩变大，姿态控制更有力但阻力上升。"},
    "aero.stall": {
        "explanation": "动态失速模型参数（Leishman-Beddoes 类）。",
        "definition": "动态失速描述俯仰振荡中失速的延迟与涡升力现象，参数标定自 NACA0012 振荡实验。",
        "increase": "失速触发角放宽可飞更大攻角，超过有效域则模型不可信。"},
    "aero.highar": {
        "explanation": "高展弦比机翼路线的薄翼理论系数。",
        "definition": "高展弦比路线用薄翼理论算升力线斜率与型阻，与叶素路线并行。",
        "increase": "升力线斜率增大巡航效率上升，超出薄翼假设域则失真。"},
    "aero.attached": {
        "explanation": "附着流路线的气动参数。",
        "definition": "附着流路线假设气流始终贴附翼面，是两条气动路线之一。",
        "increase": "系数放大同状态下力变大，注意与失速路线的有效域边界。"},
    "aero.fuselage": {
        "explanation": "机身阻力参数：迎风阻力系数与正面积。",
        "definition": "机身按圆柱绕流取典型阻力系数，正面积由机身半径换算。",
        "increase": "阻力增大巡航功耗上升。"},
    "ctrl.ref": {
        "explanation": "配平参考状态：巡航速度等指挥基准。",
        "definition": "参考状态是控制器跟踪的目标工作点，由配平计算得出（第五级派生量）。",
        "increase": "参考速度增大巡航更快，所需推力与结构负荷上升。"},
    "probe.exp": {
        "explanation": "自动试验族的激励探针参数（辨识产线用）。",
        "definition": "激励探针在输入通道叠加小幅激励信号，供辨识管线从飞行数据测出动态规律。",
        "increase": "探针幅值增大信噪比变好，但对飞行的扰动也变大。"},
    "search.hard": {
        "explanation": "辨识搜索的硬边界（参数取值域约束）。",
        "definition": "硬边界是搜索算法允许参数取值的上下限，来自物理合理性判断。",
        "increase": "边界放宽搜索空间变大，可能落进物理不合理的区域。"},
    "search.domain": {
        "explanation": "辨识搜索的初值域设定。",
        "definition": "初值域定义搜索从哪些候选值出发。",
        "increase": "初值域放宽覆盖更广，搜索耗时上升。"},
    "search.criteria": {
        "explanation": "搜索的验收判据（损失门槛与留出验证要求）。",
        "definition": "搜索判据决定哪组参数算“找到”：损失低于门槛且过留出验证。",
        "increase": "门槛收紧筛出的参数更可信，搜索失败率上升。"},
    "search.engine": {
        "explanation": "搜索引擎运行参数（并发与资源）。",
        "definition": "搜索引擎参数控制搜索的计算资源分配。",
        "increase": "并发增大搜索更快，机器负荷上升。"},
    "search.time": {
        "explanation": "搜索的时间预算设定。",
        "definition": "时间预算限制单轮搜索最长运行时长。",
        "increase": "预算增大搜得更深，出结果更慢。"},
    "search.anchor": {
        "explanation": "搜索锚点（训练环境锚定值）。",
        "definition": "锚点是搜索所依赖的基准构型取值（D-045 第九级失效条件关联项）。",
        "increase": "锚点变更须重锚训练环境（第九级联动）。"},
    "ctrl.gain": {
        "explanation": "控制回路增益：误差到指令的放大倍数。",
        "definition": "增益决定控制器对误差反应的强度，按通道与层级整定（第七级，按第六级整定）。",
        "increase": "增益增大响应变快，过大导致振荡甚至失稳。"},
    "ctrl.tecs": {
        "explanation": "总能量控制系统参数（速度与高度解耦控制）。",
        "definition": "总能量控制把速度与高度的调节解耦为能量总量与分配两条回路。",
        "increase": "参数增大对应回路更激进，能量分配振荡风险上升。"},
    "ctrl.damp": {
        "explanation": "阻尼项增益：抑制姿态角速度。",
        "definition": "阻尼增益按角速度成比例出力，抑制振荡。",
        "increase": "阻尼增大更稳但响应变钝。"},
    "ctrl.limit": {
        "explanation": "控制量限幅：指令的上下界。",
        "definition": "限幅裁剪控制指令，保护结构与驱动。",
        "increase": "限幅放宽可用出力变大，保护变弱。"},
    "ctrl.eff": {
        "explanation": "操纵效能相关参数（指令到力矩的换算）。",
        "definition": "操纵效能描述单位舵偏或差动能产生多少控制力矩。",
        "increase": "效能增大同样指令产生的力矩更大。"},
    "ctrl.bank": {
        "explanation": "压坡度转弯通道参数。",
        "definition": "坡度通道控制滚转角以实现协调转弯（科目一关联通道）。",
        "increase": "参数增大转弯更快，过载与失速风险上升。"},
    "ctrl.dp": {
        "explanation": "定高通路（DP 通路）参数。",
        "definition": "定高通路边沿高度误差输出俯仰修正。",
        "increase": "增益增大高度保持更紧，俯仰振荡风险上升。"},
    "ctrl.prot": {
        "explanation": "保护逻辑参数（飞行包线保护）。",
        "definition": "保护逻辑在接近包线边界时接管或限制指令。",
        "increase": "保护边界收紧更安全，机动空间变小。"},
    "ctrl.filt": {
        "explanation": "控制回路滤波器参数。",
        "definition": "滤波器抑制测量噪声与高频分量进入控制律。",
        "increase": "截止频率放宽延迟变小但噪声透传变多。"},
    "ctrl.fd": {
        "explanation": "前馈项参数。",
        "definition": "前馈按预期动力学直接给指令，不等人误差出现。",
        "increase": "前馈增大跟踪更快，模型不准时误差变大。"},
    "ctrl.manual": {
        "explanation": "手动映射增益（人操通道）。",
        "definition": "手动映射把人的输入换算为指令偏置。",
        "increase": "增益增大手感更灵敏，超调风险上升。"},
    "ctrl.dm": {
        "explanation": "差动混控参数（拍动差动出力分配）。",
        "definition": "差动混控决定左右翼拍动差多少产生滚转力矩。",
        "increase": "混控比增大同样差动出力更大。"},
    "ctrl.sd": {
        "explanation": "侧向与方向通道参数。",
        "definition": "侧向通道控制侧滑与航向。",
        "increase": "增益增大航向更硬，荷兰滚振荡风险上升。"},
    "ctrl.da3d": {
        "explanation": "三维差动协议参数。",
        "definition": "三维差动协议把差动指令分解到多个自由度。",
        "increase": "参数增大各自由度耦合更强。"},
    "ctrl.mix": {
        "explanation": "混控矩阵参数。",
        "definition": "混控矩阵把期望力矩分配到各作动通道。",
        "increase": "分配系数变化改变各通道承担的比例。"},
    "ctrl.deriv": {
        "explanation": "微分项参数。",
        "definition": "微分项按误差变化率出力，提供超前修正。",
        "increase": "微分增大抑制超调，噪声敏感度上升。"},
    "ctrl.slew": {
        "explanation": "指令斜率限制。",
        "definition": "斜率限制约束指令变化速度，防冲击。",
        "increase": "限制放宽指令更陡，冲击风险上升。"},
    "ctrl.gate": {
        "explanation": "通道闸门参数。",
        "definition": "闸门按飞行阶段启闭控制通道。",
        "increase": "阈值变化改变通道启闭时机。"},
    "probe.ail": {
        "explanation": "副翼通道激励探针参数。",
        "definition": "副翼探针在差动通道叠加激励供辨识。",
        "increase": "幅值增大辨识信噪比变好，扰动变大。"},
}

# 逐参数覆盖（讲解优先级高于族级）。
_PARAM_CONTENT = {
    "vehicle.mass.total": {
        "explanation": "全机总质量，现行构型的最底层硬件值之一。",
        "definition": "总质量是全机各构件质量之和，决定悬停与巡航所需的平均升力。",
        "increase": "总质量每增大一分，所需升力与拍动功耗同步增大；七百克构型因此须重建第三级气动系数。",
        "affects_note": "种子链在册：质量是巡航速度的上游。"},
    "aero.rho_air": {
        "explanation": "约定空气密度，海平面十五摄氏度标准大气。",
        "definition": "空气密度是单位体积空气的质量。动压等于二分之一密度乘速度平方，一切气动力的尺度因子。",
        "increase": "密度增大同速度下气动力变大；换实验地点或高度时才允许变更（第零级变更条件）。",
        "affects_note": "种子链在册：密度经质量传导到巡航速度。"},
    "ctrl.ref.cruise_vx": {
        "explanation": "巡航参考速度（第五级配平派生量）。",
        "definition": "巡航参考速度是速度环跟踪的目标，由配平计算按第一至第四级求解得出。",
        "increase": "参考速度增大巡航更快，所需推力与攻角上升。",
        "affects_note": "种子链末端：受密度与质量两级上游传导。"},
    "vehicle.wingspan": {
        "explanation": "翼展：左右翼尖总距离，论文口径复核在册。",
        "definition": "翼展是左右翼尖之间的总距离，决定机翼面积与展弦比的上限。",
        "increase": "翼展增大升力面积变大，滚转惯量按距离平方增长。"},
}

# 控制区第七级的通道分组（陈列元数据；配比由前端按实时数值归一计算）。
LOOP_GROUPS = [
    {"id": "da", "name": "滚转差动环", "keys": ["ctrl.gain.k_da", "ctrl.gain.k_da_r"]},
    {"id": "a", "name": "副翼通道环", "keys": ["ctrl.gain.kp_a", "ctrl.gain.ki_a"]},
    {"id": "z", "name": "高度环", "keys": ["ctrl.gain.kp_z", "ctrl.gain.kd_z"]},
    {"id": "xy", "name": "水平位置环", "keys": ["ctrl.gain.kp_xy", "ctrl.gain.ki_xy"]},
    {"id": "r", "name": "滚转角环", "keys": ["ctrl.gain.kp_r", "ctrl.gain.roll_ki"]},
    {"id": "v", "name": "速度环", "keys": ["ctrl.gain.kp_v"]},
    {"id": "chi", "name": "航迹角环", "keys": ["ctrl.gain.kp_chi"]},
    {"id": "c", "name": "配平修正族", "keys": ["ctrl.gain.kc_pz", "ctrl.gain.kc_dz",
                                                "ctrl.gain.kc_fv", "ctrl.gain.kc_iz"]},
]

# 第八级与第九级的空态说明（结构说明，非参数值）。
EMPTY_LEVEL_NOTES = {
    4: "第四级力律结构是公式形态的架构决定，以架构决定记录在案，当前无常驻数值参数入册。",
    8: "第八级科目判据是验收阈值，由指挥部冻结管理；阈值数值判据尚未入参数登记库，在看板事项中跟踪。",
    9: "第九级学习资产是训练出的组件，失效后须重锚；当前无网络权重类数值参数入册。",
}


def _family_of(key):
    parts = key.split(".")
    for n in range(len(parts), 0, -1):
        fam = ".".join(parts[:n])
        if fam in _FAMILY:
            return _FAMILY[fam]
    return None


def _level_of(key, rec):
    g = rec.get("graph") or {}
    if isinstance(g, dict) and isinstance(g.get("level"), int):
        return g["level"], "graph", "I-127 图字段（盘点在册）"
    for pat, lv, cite in _RULES:
        if re.match(pat, key):
            return lv, "rule", cite
    return None, "none", "未定级"


def _downstream_closure(params):
    """全部参数的下游计数与首层下游清单（graph 边，只增不改）。"""
    counts = {k: 0 for k in params}
    first = {k: [] for k in params}
    for key, rec in params.items():
        for d in (rec.get("graph") or {}).get("downstream") or []:
            if d in counts:
                counts[key] += 1
                first[key].append(d)
    return counts, first


def build_payload(params, archive):
    """构建参数实验室阶段一陈列载荷（只读，无副作用）。"""
    archive_by_param = {}
    for e in archive:
        archive_by_param.setdefault(e.get("param"), []).append(e)

    counts, first = _downstream_closure(params)
    out_params = []
    stats = {"total": len(params), "graph": 0, "rule": 0, "none": 0}
    by_level = {}
    for key, rec in params.items():
        level, source, cite = _level_of(key, rec)
        if source == "graph":
            stats["graph"] += 1
        elif source == "rule":
            stats["rule"] += 1
        else:
            stats["none"] += 1
        if level is not None:
            by_level[level] = by_level.get(level, 0) + 1
        fam = _family_of(key)
        pc = _PARAM_CONTENT.get(key) or {}
        explanation = (pc.get("explanation") or (fam or {}).get("explanation")
                       or "教学文本待盘点补齐。")
        teaching = {
            "definition": (pc.get("definition") or (fam or {}).get("definition")
                           or "定义待盘点补齐；可先读登记库来源栏。"),
            "affects": (first.get(key) or []),
            "affects_note": (pc.get("affects_note")
                             or ("在册下游 %d 个，链条明细见传导查询（阶段二视图）。"
                                 % counts[key] if counts[key]
                                 else "依赖边待盘点：图结构登记库目前只有种子链连边。")),
            "increase": (pc.get("increase") or (fam or {}).get("increase")
                         or "方向性讲解待盘点补齐。"),
            "history": (archive_by_param.get(key)
                        or "尚无退役历史：现行值自登记日起未变更。"),
        }
        out_params.append({
            "key": key,
            "value": rec.get("value"),
            "unit": rec.get("unit"),
            "lifecycle": rec.get("status"),
            "source": rec.get("source"),
            "since": rec.get("since"),
            "note": rec.get("note"),
            "graph": rec.get("graph"),
            "sample": rec.get("sample", "215g"),
            "level": level, "level_source": source, "level_rule": cite,
            "downstream_count": counts[key],
            "explanation": explanation,
            "teaching": teaching,
        })
    stats["by_level"] = by_level
    return {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "samples": SAMPLES,
        "zones": ZONES,
        "level_names": {str(k): v for k, v in LEVEL_NAMES.items()},
        "placeholders": PLACEHOLDERS,
        "empty_level_notes": {str(k): v for k, v in EMPTY_LEVEL_NOTES.items()},
        "loop_groups": LOOP_GROUPS,
        "params": out_params,
        "stats": stats,
    }
