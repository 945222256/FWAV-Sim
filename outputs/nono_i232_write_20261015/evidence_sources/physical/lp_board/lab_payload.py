# -*- coding: utf-8 -*-
"""lab_payload.py — 参数实验室陈列载荷构建（I-136 第二版，本地与云端共用）。

输入是参数登记库形状的 params 字典与退役档案列表，输出页面载荷。
数据纪律：本模块不存任何参数值；域族树、公式墙、短名与级别是陈列
元数据，逐条标注来源（graph 字段优先，规则表暂定并明示；公式出处
为代码文件加类名加函数名加行号，行号为 2026-10-10 实测值）。
设计依据：需求定稿 D-047、设计方案第二版（board_ui/lab_design/）、
Ciri 评审意见 2026-10-10 与评审并入修订附录。仅使用 Python 标准库。

第二版断言三条（任一失败即构建失败，不允许静默降级）：
  一，全覆盖：登记库每个键都落入唯一族，未命中者进「待归族」显式
     陈列并在 stats 里计数，不隐藏不丢弃；
  二，短名优先使用独立已核语义；未核时显示具体原因和稳定参数键，
      不以族名加数字补造定义。记录身份始终由参数键确定；
  三，公式符号映射：每个可点符号的目标键或目标族必须在册。
"""
import datetime
import json
import os
import re

try:
    from params_registry import semantics as _semantics
except ModuleNotFoundError as exc:
    if exc.name != "params_registry":
        raise
    # lp_board/server.py adds params_registry itself to sys.path.
    import semantics as _semantics

# ── 级名（降为卡片标签，不再是组织轴）与机型（沿用阶段一） ─────────
LEVEL_NAMES = {
    0: "第零级 约定环境量", 1: "第一级 物理值", 2: "第二级 作动与结构特性",
    3: "第三级 气动系数", 4: "第四级 力律结构", 5: "第五级 配平点族",
    6: "第六级 辨识参数", 7: "第七级 控制参数", 8: "第八级 科目判据",
    9: "第九级 学习资产",
}

SAMPLES = [
    {"id": "215g", "name": "二百一十五克现行构型", "state": "active",
     "tagline": "全机参数纵览", "locked": False},
    {"id": "700g", "name": "七百克候选（Eagle X 动作预览）", "state": "candidate",
     "tagline": "模型动作展示与几何数据", "locked": False,
     "hint": "候选状态：动作与几何为视觉资产开发样例；质量、惯量、气动与飞行能力均未确认，不构成物理构型通过。"},
]

# 需求定稿第九节第一项的指挥部暂定值（机主未推翻）。第二版随族陈列。
PLACEHOLDERS = [
    {"level": 1, "subclass": "翼型剖面", "family": "geometry",
     "text": "翼型剖面子类占位：当前模型未启用，对接实物前必补。",
     "status": "缺失"},
    {"level": 2, "subclass": "柔度", "family": "wave-drive",
     "text": "柔度子类占位：当前模型未启用，对接实物前必补。",
     "status": "缺失"},
]

# ── 九个意义域（评审第一节：索引带只放域级芯片） ────────────────────
DOMAINS = [
    {"id": "body", "no": 1, "name": "机身本体",
     "lead": "这架飞机是什么：质量、惯量、骨架构件与几何尺寸。硬件不变则不变。"},
    {"id": "env", "no": 2, "name": "飞行环境",
     "lead": "实验约定的环境条件，一切气动力的公共前提。"},
    {"id": "flap", "no": 3, "name": "拍动机构",
     "lead": "它的肌肉与骨骼怎样响应：波形运动学、驱动回路与关节增益。"},
    {"id": "wingaero", "no": 4, "name": "机翼气动",
     "lead": "翅膀怎样产生力：拟合系数、动态失速与两条平行气动路线。族划分与有效域口径以 I-134 二维系数表对账报告为准（中小攻角段模型现用值高于二维计算值，出处 cfd/docs/二维系数表报告_法向力对账.md）。"},
    {"id": "tailaero", "no": 5, "name": "尾翼与机身气动",
     "lead": "姿态控制的气动来源：尾面、垂尾、滑流与机身阻力。"},
    {"id": "trim", "no": 6, "name": "配平与飞行数据",
     "lead": "它怎样被配平到能飞：参考状态是配平计算的派生量，计算公式见公式墙。"},
    {"id": "ident", "no": 7, "name": "辨识产线",
     "lead": "运动规律怎样被测出：激励探针与搜索产线的全部配置。"},
    {"id": "control", "no": 8, "name": "控制律",
     "lead": "谁在开这架飞机：按通道与职能归纳的一百余项控制参数。",
     "groups": [
         {"name": "通道增益", "families": ["gain", "damp", "deriv", "slew", "gate"]},
         {"name": "模态与科目", "families": ["tecs", "bank", "dp", "sd", "ail", "da3d"]},
         {"name": "权限与保护", "families": ["limit", "prot", "eff"]},
         {"name": "分配与滤波", "families": ["mix", "dm", "filt", "fd", "manual"]},
     ]},
    {"id": "govern", "no": 9, "name": "治理参数",
     "lead": "控制台并行与上下文守门的登记值，服务编排治理。"},
]

# ── 族定义（匹配规则自上而下取首个命中；键集以登记库实时键为准） ──
# match 形态：{"prefix": "x."} 前缀匹配；{"keys": [...]} 精确键集。
# source 标注族划分依据三档：graph=I-127 图字段、cfd=I-134 对账报告、
# rule=阶段一前缀规则表（暂定）。
FAMILY_DEFS = [
    ("air-density", "空气密度", "env", {"keys": ["aero.rho_air"]},
     "飞行实验约定的空气密度。", "graph"),
    ("mass-cg", "全机质量与重心", "body",
     {"keys": ["vehicle.mass.total", "vehicle.mass.total.700g"],
      "prefix": "vehicle.cg."},
     "全机总质量与重心位置，最底层的硬件值。", "graph"),
    ("links", "骨架构件质量惯量", "body",
     {"prefixes": ["link.", "base_link.", "vehicle.mass.link.", "vehicle.inertia.link."]},
     "URDF 骨架各刚体构件的质量与惯量属性，硬件本体清单。", "graph"),
    ("inertia", "惯量张量", "body", {"prefix": "vehicle.inertia."},
     "全机合成惯量与参考惯量：抵抗姿态变化的物理量。", "graph"),
    ("geometry", "整机几何尺寸", "body",
     {"prefixes": ["vehicle.wingspan.", "aero.geometry."]},
     "翼展、弦长与叶素离散：机翼的几何骨架。", "graph"),
    ("wave-kin", "拍动波形运动学", "flap", {"prefix": "wave.kinematics."},
     "翅膀按什么轨迹与节奏拍动：形状层。", "rule"),
    ("wave-drive", "拍动驱动回路", "flap", {"prefix": "wave.drive."},
     "拍动指令的驱动侧设定。", "rule"),
    ("joint-drive", "关节驱动增益", "flap", {"prefix": "ctrl.drive"},
     "仿真关节执行器的刚度与阻尼整定值。", "graph"),
    ("wing-fit", "机翼气动系数拟合", "wingaero",
     {"keys": ["aero.wing.cn_fit_amp", "aero.wing.cn_fit_phase_deg",
               "aero.wing.ct_fit_amp", "aero.wing.ct_fit_offset",
               "aero.wing.max_wing_vel"]},
     "罗布蝇实测极曲线拟合常数与有效域限值（I-134 对账在案）。", "cfd"),
    ("wing-added", "附加质量与旋转效应", "wingaero",
     {"keys": ["aero.wing.added_mass_coeff", "aero.wing.c_rot",
               "aero.wing.rot_damp", "aero.wing.rot_ref_chord_frac"]},
     "非定常附加项：旋转环流力与附加质量。", "rule"),
    ("stall", "动态失速", "wingaero", {"prefix": "aero.stall."},
     "Leishman-Beddoes 类动态失速模型参数。", "rule"),
    ("attached", "附着流路线", "wingaero", {"prefix": "aero.attached."},
     "附着流气动路线（Wagner 函数暂态）。", "rule"),
    ("highar", "高展弦比路线", "wingaero", {"prefix": "aero.highar."},
     "高展弦比薄翼理论路线，与叶素路线并行。", "rule"),
    ("tail", "尾面气动", "tailaero",
     {"keys": ["aero.tail.area", "aero.tail.cn_slope", "aero.tail.lever_arm",
               "aero.tail.panel_y", "aero.tail.stall_angle_deg"]},
     "水平尾面的面积、法向力斜率、力臂与失速角。", "rule"),
    ("vfin", "垂尾气动", "tailaero", {"prefix": "aero.tail.vfin."},
     "垂直尾面气动参数。", "rule"),
    ("slipstream", "尾翼滑流", "tailaero", {"prefix": "aero.tail.slipstream."},
     "拍动滑流对尾翼的增速效应模型。", "rule"),
    ("fuselage", "机身阻力", "tailaero", {"prefix": "aero.fuselage."},
     "机身圆柱绕流阻力系数与正面积。", "rule"),
    ("ref", "配平参考状态（派生量）", "trim", {"prefix": "ctrl.ref."},
     "控制器跟踪的目标工作点，由配平计算求解，不独立取值。", "graph"),
    ("trim", "配平修正", "trim", {"prefix": "ctrl.trim"},
     "配平修正偏置。", "rule"),
    ("probe-exp", "激励探针协议", "ident",
     {"prefixes": ["probe.exp.", "probe.multisine", "probe.freq"]},
     "辨识激励信号族的协议设定。", "rule"),
    ("probe-chan", "通道探针", "ident",
     {"prefixes": ["probe.ail.", "probe.da", "probe.elev", "probe.rud"]},
     "各通道的激励探针参数。", "rule"),
    ("search-hard", "搜索硬边界", "ident", {"prefix": "search.hard."},
     "参数取值域的物理合理边界。", "rule"),
    ("search-domain", "搜索初值域", "ident", {"prefix": "search.domain."},
     "搜索出发点候选域。", "rule"),
    ("search-crit", "搜索判据", "ident", {"prefix": "search.criteria."},
     "哪组参数算找到的验收门槛。", "rule"),
    ("search-engine", "搜索引擎运行", "ident", {"prefix": "search.engine."},
     "搜索的计算资源分配。", "rule"),
    ("search-time", "搜索时间预算", "ident", {"prefix": "search.time."},
     "单轮搜索时长上限。", "rule"),
    ("search-anchor", "搜索锚点", "ident", {"prefix": "search.anchor."},
     "搜索依赖的基准构型取值。", "rule"),
    ("gain", "控制增益", "control", {"prefix": "ctrl.gain."},
     "按通道分组陈列（沿用阶段一八组），配比由前端按实时数值归一。", "graph"),
    ("damp", "阻尼项", "control", {"prefix": "ctrl.damp."},
     "按角速度成比例出力抑制振荡。", "rule"),
    ("deriv", "微分项", "control", {"prefix": "ctrl.deriv."},
     "按误差变化率出力的超前修正。", "rule"),
    ("slew", "指令斜率限制", "control", {"prefix": "ctrl.slew."},
     "约束指令变化速度防冲击。", "rule"),
    ("gate", "通道闸门", "control", {"prefix": "ctrl.gate."},
     "按飞行阶段启闭控制通道。", "rule"),
    ("tecs", "总能量控制", "control", {"prefix": "ctrl.tecs."},
     "速度与高度解耦的能量回路。", "rule"),
    ("bank", "压坡度转弯", "control", {"prefix": "ctrl.bank."},
     "坡度通道实现协调转弯（科目一关联）。", "rule"),
    ("dp", "定高通路", "control", {"prefix": "ctrl.dp."},
     "高度误差到俯仰修正的通路。", "rule"),
    ("sd", "侧向与方向", "control", {"prefix": "ctrl.sd."},
     "侧滑与航向通道。", "rule"),
    ("ail", "副翼权限", "control", {"prefix": "ctrl.ail."},
     "副翼通道权限设定。", "rule"),
    ("da3d", "三维差动协议", "control", {"prefix": "ctrl.da3d."},
     "差动指令到多自由度的分解协议。", "rule"),
    ("limit", "控制量限幅", "control", {"prefix": "ctrl.limit."},
     "指令上下界，保护结构与驱动。", "rule"),
    ("prot", "飞行包线保护", "control", {"prefix": "ctrl.prot."},
     "接近包线边界时的接管与限制。", "rule"),
    ("eff", "操纵效能", "control", {"prefix": "ctrl.eff."},
     "单位舵偏或差动到力矩的换算。", "rule"),
    ("mix", "混控矩阵", "control", {"prefix": "ctrl.mix."},
     "期望力矩到各作动通道的分配。", "rule"),
    ("dm", "差动混控", "control", {"prefix": "ctrl.dm."},
     "左右翼拍动差的出力分配。", "rule"),
    ("filt", "回路滤波器", "control", {"prefix": "ctrl.filt."},
     "抑制测量噪声进入控制律。", "rule"),
    ("fd", "前馈项", "control", {"prefix": "ctrl.fd."},
     "按预期动力学直接给指令。", "rule"),
    ("manual", "手动映射", "control", {"prefix": "ctrl.manual."},
     "人操输入到指令偏置的换算。", "rule"),
    ("console", "控制台治理", "govern", {"prefix": "console."},
     "编排控制台的并行与上下文守门登记值。", "rule"),
]

# ── 公式墙（评审第二节：出处为文件＋类＋函数＋行号，行号 2026-10-10 实测） ──
# symbol 字段：sym 公式记号；name 中文名；target_key 跳转目标键；
# target_family 跳转目标族；两者皆空为非登记量（只陈列不可点）。
FORMULAS = [
    {"id": "q", "name": "动压",
     "text": "q = ½ · ρ · |v|²",
     "note": "一切气动力的公共尺度因子；|v| 为叶素相对气流速度，由飞行状态实时决定，非登记参数。ρ 三处直接出现之一。",
     "symbols": [
         {"sym": "ρ", "name": "空气密度", "target_key": "aero.rho_air"}],
     "source": "modules/aero/sane_dickinson.py FlapperAeroModel._wing_forces 266行"},
    {"id": "cn", "name": "机翼法向力系数",
     "text": "C_N(α) = A_N · sin(2α + φ_N)",
     "note": "罗布蝇实测极曲线拟合；A_N 与 φ_N 即参数卡上的拟合幅值与相位。",
     "symbols": [
         {"sym": "A_N", "name": "法向力拟合幅值", "target_key": "aero.wing.cn_fit_amp"},
         {"sym": "φ_N", "name": "拟合相位", "target_key": "aero.wing.cn_fit_phase_deg"}],
     "source": "modules/aero/sane_dickinson.py FlapperAeroModel._C_N 183行"},
    {"id": "ct", "name": "机翼切向力系数",
     "text": "C_T(α) = A_T · cos(2α + φ_N) − C_T0",
     "note": "与法向力系数共用同一相位角，二者来自同一次实测拟合。",
     "symbols": [
         {"sym": "A_T", "name": "切向力拟合幅值", "target_key": "aero.wing.ct_fit_amp"},
         {"sym": "φ_N", "name": "拟合相位", "target_key": "aero.wing.cn_fit_phase_deg"},
         {"sym": "C_T0", "name": "切向力拟合偏置", "target_key": "aero.wing.ct_fit_offset"}],
     "source": "modules/aero/sane_dickinson.py FlapperAeroModel._C_T 187行"},
    {"id": "strip", "name": "叶素微元力",
     "text": "dF = q · C(α) · c(r) · dr",
     "note": "沿翼展把机翼切成叶素逐段求和；c(r) 由根弦、尖弦与展长线性插值，段数由离散条数决定。ρ 经动压 q 间接进入本式。",
     "symbols": [
         {"sym": "c(r)", "name": "弦长分布", "target_family": "geometry"}],
     "source": "modules/aero/sane_dickinson.py FlapperAeroModel._wing_forces 270至274行"},
    {"id": "rot", "name": "旋转附加力",
     "text": "dF_rot = c_rot · ρ · c · |v| · α̇ · dr",
     "note": "攻角变化率驱动的旋转环流力；ρ 三处直接出现之二。",
     "symbols": [
         {"sym": "c_rot", "name": "旋转力系数", "target_key": "aero.wing.c_rot"},
         {"sym": "ρ", "name": "空气密度", "target_key": "aero.rho_air"}],
     "source": "modules/aero/sane_dickinson.py FlapperAeroModel._wing_forces 279行"},
    {"id": "tail", "name": "尾面法向力与俯仰力矩",
     "text": "N_tail = q · S_t · k_n · (α + δ_e)；M_pitch = N_tail · L_t",
     "note": "尾面按平板法向力模型出力并绕重心生矩；δ_e 为升降舵偏指令，是控制量不是登记参数。",
     "symbols": [
         {"sym": "S_t", "name": "尾面面积", "target_key": "aero.tail.area"},
         {"sym": "k_n", "name": "法向力斜率", "target_key": "aero.tail.cn_slope"},
         {"sym": "L_t", "name": "尾力臂", "target_key": "aero.tail.lever_arm"}],
     "source": "modules/aero/tail_fuselage.py TailFuselageAero.compute 66至72行"},
    {"id": "roll", "name": "差动滚转力矩",
     "text": "T_roll = q · S_t · k_n · δ_a · y_p",
     "note": "左右尾面反偏产生纯滚转力矩；δ_a 为差动指令，是控制量不是登记参数。",
     "symbols": [
         {"sym": "S_t", "name": "尾面面积", "target_key": "aero.tail.area"},
         {"sym": "k_n", "name": "法向力斜率", "target_key": "aero.tail.cn_slope"},
         {"sym": "y_p", "name": "尾面展向位置", "target_key": "aero.tail.panel_y"}],
     "source": "modules/aero/tail_fuselage.py TailFuselageAero.compute 75行"},
    {"id": "fus", "name": "机身阻力",
     "text": "D = ½ · ρ · V² · C_D · A_f",
     "note": "机身按圆柱绕流取阻力系数；ρ 三处直接出现之三。",
     "symbols": [
         {"sym": "ρ", "name": "空气密度", "target_key": "aero.rho_air"},
         {"sym": "C_D", "name": "机身阻力系数", "target_key": "aero.fuselage.drag_coeff"},
         {"sym": "A_f", "name": "机身正面积", "target_key": "aero.fuselage.frontal_area"}],
     "source": "modules/aero/tail_fuselage.py TailFuselageAero.compute 99至101行"},
    {"id": "stall", "name": "动态失速静态分离",
     "text": "f_ss(α) = clip(1 − exp((α − α_ds) / s), 0, 1)",
     "note": "静态分离点曲线：攻角超过失速触发角后附着流比例快速下降。",
     "symbols": [
         {"sym": "α_ds", "name": "失速触发角", "target_key": "aero.stall.alpha_ds_deg"},
         {"sym": "s", "name": "分离陡度", "target_key": "aero.stall.separation_s_deg"}],
     "source": "modules/aero/dynamic_stall.py DynamicStallModel._static_separation 81行"},
    {"id": "flap", "name": "拍动角",
     "text": "φ(t) = φ₀ + A · sin(ω t)",
     "note": "拍动角的主谐波；φ₀ 为中位偏置与均值，属波形族设定。",
     "symbols": [
         {"sym": "A", "name": "拍动幅值", "target_key": "wave.kinematics.amplitude"},
         {"sym": "ω", "name": "拍动角频率", "target_key": "wave.kinematics.frequency"}],
     "source": "modules/aero/kinematics.py WingKinematics.evaluate 126行"},
]

# ── 短名逐参数覆盖（其余按族名加后缀生成，断言族内唯一） ────────────
SHORT_OVERRIDE = {
    "aero.rho_air": "空气密度",
    "vehicle.mass.total": "全机总质量",
    "vehicle.mass.total.700g": "全机总质量（七百克占位）",
    "vehicle.cg": "重心位置",
    "vehicle.wingspan.total": "翼展",
    "aero.geometry.span": "半翼展",
    "aero.geometry.root_chord": "根弦长",
    "aero.geometry.tip_chord": "尖弦长",
    "aero.geometry.n_strips": "叶素离散条数",
    "aero.wing.cn_fit_amp": "法向力拟合幅值",
    "aero.wing.cn_fit_phase_deg": "法向力拟合相位",
    "aero.wing.ct_fit_amp": "切向力拟合幅值",
    "aero.wing.ct_fit_offset": "切向力拟合偏置",
    "aero.wing.c_rot": "旋转力系数",
    "aero.tail.area": "尾面面积",
    "aero.tail.cn_slope": "尾面法向力斜率",
    "aero.tail.lever_arm": "尾力臂",
    "aero.fuselage.drag_coeff": "机身阻力系数",
    "aero.fuselage.frontal_area": "机身正面积",
    "aero.stall.alpha_ds_deg": "失速触发角",
    "ctrl.ref.cruise_vx": "巡航参考速度（派生量）",
    "wave.kinematics.frequency": "拍动频率",
    "wave.kinematics.amplitude": "拍动幅值",
    "ctrl.gain.kp_z": "高度环比例增益",
    "ctrl.gain.kd_z": "高度环微分增益",
}

# ── 教学内容包（阶段一资产原样保留：四十一族族级文本） ───────────────
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

# 逐参数覆盖（讲解优先级高于族级；阶段一资产原样保留）。
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

# 控制增益族的通道分组（阶段一资产原样保留；配比由前端按实时数值归一计算）。
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

# ── 暂定定级规则表（阶段一资产；graph.level 在册时一律优先） ──────────
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
    """全部参数的下游计数、首层清单与传递闭包链（graph 边，只读）。"""
    counts = {k: 0 for k in params}
    first = {k: [] for k in params}
    for key, rec in params.items():
        for d in (rec.get("graph") or {}).get("downstream") or []:
            if d in counts:
                counts[key] += 1
                first[key].append(d)

    def chain(key, seen=None):
        seen = seen or {key}
        out = []
        for d in first.get(key, []):
            if d in seen:
                continue
            seen.add(d)
            out.append({"key": d, "via": chain(d, seen)})
        return out

    chains = {k: chain(k) for k in params}
    return counts, first, chains


def _match_families(params):
    """键归族：FAMILY_DEFS 自上而下首个命中；未命中进待归族。"""
    fam_of = {}
    counts = {d[0]: [] for d in FAMILY_DEFS}
    for key in params:
        hit = None
        for fid, _name, _dom, match, _lead, _src in FAMILY_DEFS:
            keys = match.get("keys")
            prefixes = match.get("prefixes") or ([match["prefix"]] if match.get("prefix") else [])
            if key in (keys or []) or any(key.startswith(p) for p in prefixes):
                hit = fid
                break
        if hit is None:
            hit = "_orphan"
        fam_of[key] = hit
        counts.setdefault(hit, []).append(key)
    return fam_of, counts


def _short_names(params, fam_of, fam_counts):
    """I-232: independently verified names, or an explicit unresolved key."""
    return {key: _semantics.project_text(key, rec["semantics"], "name", rec["semantics_diagnostics"])
            for key, rec in params.items()}


def _normalize(rec):
    """记录规范化（I-130 读回以 extra 为准规则的落点）：原始平铺记录
    的完整镜像是 extra 串，graph、sample 等图字段在镜像里，解开覆盖。"""
    out = dict(rec)
    extra = rec.get("extra")
    if isinstance(extra, str) and extra.strip():
        try:
            mirror = json.loads(extra)
            if isinstance(mirror, dict):
                for k, v in mirror.items():
                    if k != "extra":
                        out[k] = v
        except ValueError:
            pass
    # Keep the existing I-130 core projection policy. Only semantics has a new
    # canonical precedence; nested graph/sample are deliberately not promoted.
    out["semantics"], out["semantics_diagnostics"] = _semantics.read_semantics(rec)
    return out



# ── I-202：七百克候选动作预览数据节 ─────────────────────────────────
# 数据纪律：本节不写死任何几何数值，全部从 Jane 交付文件现读；数值
# 携带文件路径与版本日期作为来源指针。上游复核（I-201 L8、I-203 L3）
# 未关闭前，本节整体标注为开发样例，页面显示候选状态。
_EAGLEX_BASES = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "eagle_shell", "jane_preview_20261013_tail", "output"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "eaglex"),
]
_EAGLEX_V2_BASES = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "eagle_shell", "l8_i201_animv2", "output"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "eaglexv2"),
]
# 第二版动作段展示名（L8 交付 joint_commands 的 mode 原名与中文展示名）。
# 元素为（命令段 mode，中文展示名，clips 目录实际文件名）。
_V2_SEGMENT_NAMES = [
    ("FLAP_INHERIT", "拍动（翼尖继承）", "flap_inherit.mp4"),
    ("TWIST_INHERIT", "翼根迎角调节（翼尖跟随）", "twist_inherit.mp4"),
    ("TIP_DIHEDRAL_UP", "翼尖上反", "tip_dihedral_up.mp4"),
    ("TIP_DOWNFOLD", "翼尖下折", "tip_downfold.mp4"),
    ("FLAP_PLUS_FOLD", "拍动叠加折叠", "flap_plus_fold.mp4"),
    ("TAIL_YAW_WHOLE", "整尾偏航", "tail_yaw_whole.mp4"),
    ("TAIL_ROLL_WHOLE", "整尾滚转", "tail_roll_whole.mp4"),
    ("TAIL_OPEN_LINKED_3DOF", "平尾开合（联动三自由度对照）", "tail_open_linked.mp4"),
    ("TAIL_ALL_TWO_WHOLE_PLUS_OPEN", "整尾两整体动作叠加开合", "tail_all.mp4"),
    ("OPEN_INDEPENDENT_4DOF_COMPARE", "平尾独立开合（四自由度对照）", "open_independent_compare.mp4"),
]
_EAGLEX_MOTION_SEGMENTS = [
    {"id": "flap", "label": "拍动", "time_s": [0, 2]},
    {"id": "twist", "label": "扭转", "time_s": [2, 4]},
    {"id": "fold", "label": "翼尖折叠（旧法向轴样例）", "time_s": [4, 6]},
    {"id": "tail", "label": "整尾偏航（旧单轴样例）", "time_s": [6, 8]},
    {"id": "combo", "label": "组合", "time_s": [8, 10]},
]


def _mechanism_note(joints):
    """机构说明（I-202 接续，2026-10-13 机主构型澄清版）：分三层陈述——
    冻结物理机构事实（保持原值）、机主澄清的展示构型要求、未确认项。
    层级由交付 joints 表现算核实；耦合自由度不写成实物事实。"""
    tjs = {j["joint"]: j for j in joints if j["joint"].startswith("tail")}
    yaw = tjs.get("tail_yaw")
    panels = [tjs.get("tail_L"), tjs.get("tail_R")]
    hierarchy_ok = bool(
        yaw and yaw.get("parent") == "base_link"
        and all(p and p.get("parent") == yaw.get("child") for p in panels))
    facts = ("冻结物理机构（二百一十五克，保持原值，不因视觉候选改变）：整尾偏航基座"
             "带动整尾，左右尾面各挂独立转动关节。")
    clarified = ("机主 2026-10-13 澄清的展示构型：翼尖只有相对主翼绕弦向上反下折（一个"
             "折叠角，铰线沿弦向位于两件实际交界，前后缘接缝全程连续，零位法向旋转撤除）；"
             "整尾带整体偏航与整体滚转两个整体角，同时带动垂尾与两侧平尾共同移动；"
             "两片平尾相对尾翼连杆开合，接缝在整个动作范围内保持连续。")
    unconfirmed = ("平尾开合的联动关系未经实物资料确认：左右独立为四自由度候选、"
             "存在一条联动关系为三自由度候选，两者均未确认，不写成实物事实；"
             "实物轴位、尺寸、限位、舵机数量与质量未知留空。")
    sample_gap = ("本页当前为开发样例（旧动画构型）：翼尖折叠为绕法向轴的旧实现"
             "（铰点靠后缘），整尾仅单轴偏航且平尾为绕局部横向轴转动，均与上述澄清"
             "构型不符，仅作诊断对照，待 L8 新构型版本（含接缝逐帧间隙量测通过）"
             "与 I-203 登记后替换（依据 Jane 2026-10-13 机主构型澄清文档）。")
    head = "" if hierarchy_ok else "交付 joints 层级与冻结形态不符，机构语义待核对。"
    return head + facts + clarified + unconfirmed + sample_gap


def _v2_stage_note(rep):
    """第二版阶段说明：事实逐条从修复轮报告现读（I-202 复工第二轮），
    不把已修复问题写成当前事实，也不把阶段交付标成已通过。"""
    fixes = rep.get("fixes_this_round") or {}
    axes = rep.get("wing_axes") or {}
    left = axes.get("L") or {}
    frac = left.get("twist_axis_fraction_from_le")
    jic = rep.get("glb_joint_index_check") or {}
    joints_ok = bool(jic.get("results")) and all(
        r.get("passed") for r in jic.get("results", []))
    ev = rep.get("exported_model_edge_verification") or {}
    tail_max = max(
        [abs((ev.get(k) or {}).get("inside", {}).get("max_stretch") or 0)
         for k in ev if "tail_flat" in k] or [0])
    parts = ["阶段预览（未独立复验，机主已认可尾部构型方向）："]
    cn = {"20": "二十", "4": "四", "3": "三"}
    pct = lambda v: cn.get(str(v), str(v))
    parts.append("翼根迎角调节轴候选位于前缘起约百分之%s弦长（靠机头侧；候选轴：%s；实物铰位与限位资料缺项）"
                 % (pct(round(frac * 100)) if frac is not None else "未知",
                    "是（未经实物资料确认）" if left.get("axis_is_candidate") else "否"))
    parts.append("导出模型顶点骨骼编号已修复（直读导出模型复验，越界顶点为零%s）"
                 % ("，全部蒙皮件通过" if joints_ok else "（存在未通过件）"))
    parts.append("视频由载入导出模型渲染，量测与交互模型同一蒙皮实现（不再有两套变形函数）")
    motion = rep.get("exported_motion_axis_verification") or {}
    if motion.get("passed"):
        parts.append("动画导出轴系已修正并通过全帧机检：翼根迎角绕随翼展向轴、整尾偏航绕机体z、整尾滚转绕随动x；不增加水平扫翼自由度")
    if tail_max:
        parts.append("剩余未全解项：平尾过渡区局部边长变化最大约百分之%s（过渡区连续弯曲代价已分列峰值边与帧，区外小于百分之一）" % pct(round(tail_max * 100)))
    parts.append("独立复验未完成，本页保持候选与未复验标注；交互模型载入待独立复验与登记后随最终替换进行，本阶段只接视频")
    return "；".join(parts) + "。"


def _build_animv2():
    """动画第二版视频节（I-202 复工接入，机主 2026-10-13 认可尾部构型
    方向）。从 L8 animv2 交付现读：视频清单、命令段序、版本元数据。
    阶段事实如实标注：翼根轴位、蒙皮毛边、导出绑定仍在修复；交互
    模型因导出骨骼编号越界（应为皮肤内零一编号，现写入全局节点号）
    暂不载入，本节只接视频。"""
    import hashlib

    def _sha(path):
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()[:12]

    base = next((d for d in _EAGLEX_V2_BASES
                 if os.path.exists(os.path.join(d, "anim_config_v2_report.json"))), None)
    if base is None:
        return {"available": False,
                "reason": "动画第二版交付未部署（找过 %s）" % _EAGLEX_V2_BASES}
    with open(os.path.join(base, "anim_config_v2_report.json"), encoding="utf-8") as fh:
        rep = json.load(fh)
    report_sha12 = _sha(os.path.join(base, "anim_config_v2_report.json"))
    clips_dir = os.path.join(base, "clips")
    clips = sorted(fn for fn in os.listdir(clips_dir) if fn.endswith(".mp4"))         if os.path.isdir(clips_dir) else []
    clips_png = set(fn for fn in os.listdir(clips_dir) if fn.endswith(".png"))         if os.path.isdir(clips_dir) else set()
    with open(os.path.join(base, "joint_commands.csv"), encoding="utf-8-sig") as fh:
        rows = [ln.split(",") for ln in fh.read().strip().splitlines()]
    modes = []
    for r in rows[1:]:
        if len(r) > 1 and r[1] not in modes:
            modes.append(r[1])
    segs = []
    for mode, label, fn in _V2_SEGMENT_NAMES:
        stem = fn[:-4]
        poster = stem + "_mid.png" if (stem + "_mid.png") in clips_png else None
        segs.append({"id": mode.lower(), "label": label, "clip": fn,
                     "in_commands": mode in modes,
                     "asset": "lab/eaglex700v2/clips/" + fn + "?v=" + report_sha12,
                     "poster": "lab/eaglex700v2/clips/" + poster + "?v=" + report_sha12 if poster else None})
    missing = [sg["clip"] for sg in segs
               if sg["clip"] not in clips]
    return {
        "available": len(missing) == 0 and len(segs) > 0,
        "missing_clips": missing,
        "source": {"dir": "eagle_shell/l8_i201_animv2/output",
                   "report_sha12": report_sha12,
                   "version_name": rep.get("version_name"), "date": rep.get("date"),
                   "simulator_used": rep.get("simulator_used")},
        "supersedes_note": "替代旧版刚性法向折叠与三片独立旋转的动画语义（旧版保留为历史诊断，不进入最终页面）",
        "owner_ack": "机主 2026-10-13 认可第二版尾部构型与动作方向（整尾偏航及滚转共同带动垂尾与平尾，平尾相对载体开合）；不自动确认实物材料、质量、舵机数量、限位或左右开合机械联动",
        "stage_note": _v2_stage_note(rep),
        "segments": segs,
    }


def build_eaglex700():
    """构建七百克候选动作预览数据节（只读 Jane 交付文件与渲染报告）。"""
    import hashlib

    def _sha(path):
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()[:12]

    base = next((d for d in _EAGLEX_BASES
                 if os.path.exists(os.path.join(d, "geometry_and_binding.json"))), None)
    if base is None:
        return {"available": False,
                "reason": "上游几何交付未部署（找过 %s）" % _EAGLEX_BASES,
                "candidate_status": {"mass": "待确认", "inertia": "待确认",
                                      "aero": "待确认", "flight": "待确认"}}
    with open(os.path.join(base, "geometry_and_binding.json"), encoding="utf-8") as fh:
        geo = json.load(fh)
    chord_path = os.path.join(base, "chord_stations.csv")
    with open(chord_path, encoding="utf-8-sig") as fh:
        chord_rows = [ln.split(",") for ln in fh.read().strip().splitlines()]
    chord_header = chord_rows[0]
    chord = [dict(zip(chord_header, r)) for r in chord_rows[1:]]

    # 帧资产结构元数据随交付数据一并部署（render_report.json），云端
    # 与本机都从 base 目录读，不依赖仓库相对路径。
    frames_meta = []
    report_path = os.path.join(base, "render_report.json")
    if not os.path.exists(report_path):
        report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "render_report.json")
    if os.path.exists(report_path):
        with open(report_path, encoding="utf-8") as fh:
            rep = json.load(fh)
        by_seg = {r["segment"]: r for r in rep["segments"]}
        for seg in _EAGLEX_MOTION_SEGMENTS:
            got = by_seg.get(seg["id"], {})
            frames_meta.append({**seg, "frames": got.get("frames"),
                                "asset": "lab/eaglex700/%s/frame_##.webp" % seg["id"]})

    parts_out = []
    for name, g in geo["geometry"].items():
        parts_out.append({
            "part": name,
            "dimensions_m": g.get("dimensions_m"),
            "surface_area_m2": g.get("surface_area_m2"),
            "projected_planform_area_m2": g.get("projected_planform_area_m2"),
            "spanwise_extent_m": g.get("spanwise_extent_m"),
            "triangles": g.get("triangles"),
        })
    joints_out = [{"joint": j["joint"], "parent": j["parent"], "child": j["child"],
                   "origin_xyz_m": j["origin_xyz_m"], "axis": j["axis"],
                   "range_rad": [j["lower_rad"], j["upper_rad"]]}
                  for j in geo["joints"]]
    src = {"dir": "eagle_shell/jane_preview_20261013_tail/output",
           "geo_sha12": _sha(os.path.join(base, "geometry_and_binding.json")),
           "chord_sha12": _sha(chord_path),
           "date": geo.get("date"), "asset": geo.get("asset")}
    return {
        "available": True,
        "source": src,
        "provenance": geo.get("provenance"),
        "limitations": geo.get("limitations"),
        "verification_note": "delivery_checks 为交付方自检，L8 独立复核（I-201）与 L3 登记复核（I-203）未关闭",
        "sample_disclaimer": "开发样例（诊断预览）：翼根轴位与尾部完整动作尚未通过复核，非最终展示资产；最终采用 L8 复核版本及 L3 登记数据",
        "candidate_status": {"mass": "待确认", "inertia": "待确认",
                             "aero": "待确认", "flight": "待确认"},
        "physics_frozen": "二百一十五克现行构型的质量、惯量、气动参数全部保持原值；新构型物理接入另办验证",
        "motion": {"segments": frames_meta,
                   "unit": "rad（关节指令，运动学角度驱动，固定机身，未启动仿真器）",
                   "channels": [j["joint"] for j in geo["joints"]],
                   "mechanism_note": _mechanism_note(geo["joints"])},
        "geometry": {"parts": parts_out,
                     "chord_stations": {"unit_note": "站位与弦长单位为米（字段名带 _m 后缀）",
                                        "rows": len(chord), "columns": chord_header,
                                        "data": chord}},
        "bindings": joints_out,
        "preview": geo.get("preview"),
        "animv2": _build_animv2(),
    }


def build_payload(params, archive):
    """构建参数实验室第二版陈列载荷（只读，无副作用，断言三条）。"""
    params = {k: _normalize(v) for k, v in params.items()}
    fam_of, fam_counts = _match_families(params)
    shorts = _short_names(params, fam_of, fam_counts)

    # 断言三：公式符号目标必须在册。
    fam_ids = {d[0] for d in FAMILY_DEFS}
    for f in FORMULAS:
        for s in f["symbols"]:
            if s.get("target_key") and s["target_key"] not in params:
                raise AssertionError("公式 %s 符号 %s 目标键不在册" % (f["id"], s["sym"]))
            if s.get("target_family") and s["target_family"] not in fam_ids:
                raise AssertionError("公式 %s 符号 %s 目标族不在册" % (f["id"], s["sym"]))

    # 参数被哪些公式引用（键级反查）。
    formulas_by_key = {}
    for f in FORMULAS:
        for s in f["symbols"]:
            tk = s.get("target_key")
            if tk:
                formulas_by_key.setdefault(tk, [])
                if f["id"] not in [x["id"] for x in formulas_by_key[tk]]:
                    formulas_by_key[tk].append({"id": f["id"], "name": f["name"],
                                                "text": f["text"]})

    archive_by_param = {}
    for e in archive:
        archive_by_param.setdefault(e.get("param"), []).append(e)

    counts, first, chains = _downstream_closure(params)

    # 族与域载荷（含实时键数与占位归族）。
    dom_defs = {d["id"]: d for d in DOMAINS}
    fam_names = {d[0]: d[1] for d in FAMILY_DEFS}
    fam_dom = {d[0]: d[2] for d in FAMILY_DEFS}
    fam_lead = {d[0]: d[4] for d in FAMILY_DEFS}
    fam_src = {d[0]: d[5] for d in FAMILY_DEFS}
    if fam_counts.get("_orphan"):
        fam_names["_orphan"] = "待归族"
        fam_dom["_orphan"] = "_orphan"
        fam_lead["_orphan"] = "以下键未命中族定义规则，显式陈列待指挥部归族，不隐藏。"
        fam_src["_orphan"] = "assert"
    families = []
    for fid, name in fam_names.items():
        did = fam_dom[fid]
        families.append({
            "id": fid, "name": name, "domain": did,
            "lead": fam_lead[fid], "source": fam_src[fid],
            "keys": sorted(fam_counts.get(fid, [])),
            "derived": fid == "ref",
        })
    domains_out = []
    for d in DOMAINS:
        n = sum(len(f["keys"]) for f in families if f["domain"] == d["id"])
        domains_out.append({**d, "count": n})
    if fam_dom.get("_orphan") == "_orphan" and fam_counts.get("_orphan"):
        domains_out.append({"id": "_orphan", "no": 10, "name": "待归",
                            "lead": "未命中族规则的键在此显式陈列。", "count": len(fam_counts["_orphan"])})

    out_params = []
    stats = {"total": len(params), "graph": 0, "rule": 0, "none": 0,
             "families": len(families), "domains": len(domains_out),
             "orphan": len(fam_counts.get("_orphan", []))}
    for key, rec in params.items():
        level, source, cite = _level_of(key, rec)
        stats[source] = stats.get(source, 0) + 1
        fam = _family_of(key)
        pc = _PARAM_CONTENT.get(key) or {}
        definition = _semantics.project_text(key, rec["semantics"], "definition", rec["semantics_diagnostics"])
        explanation = definition
        teaching = {
            "definition": definition,
            "affects": (first.get(key) or []),
            "affects_note": (pc.get("affects_note")
                             or ("在册下游 %d 个，链条明细见下方传导链条块。" % counts[key]
                                 if counts[key]
                                 else "暂无在册下游边：图结构登记库目前只有种子链连边。")),
            "increase": (pc.get("increase") or (fam or {}).get("increase")
                         or "方向性讲解待盘点补齐。"),
            "history": (archive_by_param.get(key)
                        or "尚无退役历史：现行值自登记日起未变更。"),
        }
        out_params.append({
            "key": key,
            "short_name": shorts[key],
            "semantics": rec["semantics"],
            "semantics_diagnostics": rec["semantics_diagnostics"],
            "family": fam_of[key],
            "formulas": formulas_by_key.get(key, []),
            "chain": chains.get(key, []),
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
    return {
        "schema": "lab-payload-v2",
        "metadata_schema": _semantics.METADATA_SCHEMA,
        "eaglex700": build_eaglex700(),
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "samples": SAMPLES,
        "domains": domains_out,
        "families": families,
        "formulas": FORMULAS,
        "level_names": {str(k): v for k, v in LEVEL_NAMES.items()},
        "placeholders": PLACEHOLDERS,
        "loop_groups": LOOP_GROUPS,
        "params": out_params,
        "stats": stats,
    }
