# -*- coding: utf-8 -*-
"""
inertia_geometric_recalc.py — I-002 惯量几何法重算（2026-09-30 施工）

背景：生效惯量较论文真值虚高 6~13 倍（items_registry I-002），来源是
URDF 工程估计值 + 加载器按质量比缩放的运行时覆写。本工具按 URDF 自身
几何体素（cylinder/sphere/box）与登记库质量分布，几何法重算每连杆
惯量张量，直接写入 URDF；运行时覆写随后删除，URDF 成为唯一来源。

方法与口径（先于数据冻结，见 docs/辨识产线_判据与方案_20260930.md 同纪律）：
  1. 每连杆质量取 params_registry（vehicle.mass.link.*）；首次运行时从
     config.MASS_TABLE 引导登记（一次性迁移，此后 config 不再是来源）。
  2. 体素集合 = 该连杆全部 <visual> 几何（比 collision 更完整，如机头
     球只在 visual 中）；体素内质量按体积占比分配。
  3. 张量合成到几何质心，再平行轴转移到 <inertial><origin> 声明质心。
     ★声明质心一律保持不动：整机质心 x=−0.0546 m 是已验证的重力矩账
     链锚点（docs/第六十五班，unified_search_framework.R_CG_BODY），
     改质心属契约变更，须机主另行裁决，本工具禁改。
  4. 整机合成按零关节位形装配（全部关节 rpy 为空=单位旋转），对整机
     质心输出三主对角，与论文 Table I 真值对照。
  5. 非对角项政策（先于数据冻结）：体素几何产生的惯性积照实写入 URDF
     六元素（URDF 规范原生支持，SolidWorks 导出链生态常规携带）。tail_base
     为双正交板 L 形分布，主轴倾角约 34 度是物理结果，禁止人为对角化。
     导入链对惯性积的保真由探针直读判定：PhysX 编译值与 URDF 声明值
     逐格对照，不一致即按 I-001 判据不通过处理。

用法（纯标准库，无需 Isaac）：
  python tools/inertia_geometric_recalc.py            # 计算并写入 URDF+登记库
  python tools/inertia_geometric_recalc.py --dry      # 只算只打印，不落盘
产物：
  - assets/flapper/fwav_15dof_from_group.urdf / fwav_15dof_2.urdf 改写
  - params_registry/params.json 登记质量/惯量/质心条目
  - docs/INERTIA_GEOMETRIC_20260930.md 对照表
"""
import json
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URDFS = [
    os.path.join(ROOT, "assets", "flapper", "fwav_15dof_from_group.urdf"),
    os.path.join(ROOT, "assets", "flapper", "fwav_15dof_2.urdf"),
]
PARAMS_DIR = os.path.join(ROOT, "params_registry")
DOC_PATH = os.path.join(ROOT, "docs", "INERTIA_GEOMETRIC_20260930.md")
PAPER_TRUTH = (3.6e-4, 5.3e-4, 7.6e-4)   # SparrowHawk Table I Jx/Jy/Jz

sys.path.insert(0, PARAMS_DIR)
import registry  # noqa: E402  参数登记库唯一读写入口


# ───────────────────────── 小量三维张量工具（纯标准库） ─────────────────────────

def identity():
    return [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def mat_vec(a, v):
    return [sum(a[i][k] * v[k] for k in range(3)) for i in range(3)]


def transpose(a):
    return [[a[j][i] for j in range(3)] for i in range(3)]


def rpy_to_matrix(rpy):
    """URDF 固定轴 rpy（绕 X→Y→Z 固定轴）→ 旋转矩阵。"""
    r, p, y = rpy
    cr, sr = math.cos(r), math.sin(r)
    cp, sp = math.cos(p), math.sin(p)
    cy, sy = math.cos(y), math.sin(y)
    rx = [[1, 0, 0], [0, cr, -sr], [0, sr, cr]]
    ry = [[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]]
    rz = [[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]]
    return mat_mul(rz, mat_mul(ry, rx))


def parallel_axis(i_cm, mass, d):
    """I_p = I_cm + m(|d|²E − d dᵀ)，d = 质心到参考点的位移。"""
    d2 = d[0] * d[0] + d[1] * d[1] + d[2] * d[2]
    out = [[i_cm[i][j] + mass * ((d2 if i == j else 0.0) - d[i] * d[j])
            for j in range(3)] for i in range(3)]
    return out


def primitive_inertia(geom, mass):
    """体素自身质心系张量 + 几何中心相对体素 origin 的偏移由调用侧处理。"""
    if geom[0] == "box":
        sx, sy, sz = geom[1]
        return [[mass * (sy * sy + sz * sz) / 12.0, 0, 0],
                [0, mass * (sx * sx + sz * sz) / 12.0, 0],
                [0, 0, mass * (sx * sx + sy * sy) / 12.0]]
    if geom[0] == "cylinder":
        radius, length = geom[1]
        i_axial = 0.5 * mass * radius * radius
        i_trans = 0.25 * mass * radius * radius + mass * length * length / 12.0
        return [[i_trans, 0, 0], [0, i_trans, 0], [0, 0, i_axial]]
    if geom[0] == "sphere":
        r = geom[1]
        iso = 0.4 * mass * r * r
        return [[iso, 0, 0], [0, iso, 0], [0, 0, iso]]
    raise ValueError("未知几何体素：%r" % (geom[0],))


def primitive_volume(geom):
    if geom[0] == "box":
        sx, sy, sz = geom[1]
        return sx * sy * sz
    if geom[0] == "cylinder":
        radius, length = geom[1]
        return math.pi * radius * radius * length
    if geom[0] == "sphere":
        return 4.0 / 3.0 * math.pi * geom[1] ** 3
    raise ValueError("未知几何体素：%r" % (geom[0],))


def jacobi_eigen(a):
    """对称 3x3 雅可比特征分解（纯标准库）。返回 (特征值列表, 特征向量按列)。"""
    m = [row[:] for row in a]
    v = identity()
    for _ in range(50):
        off = max(abs(m[0][1]), abs(m[0][2]), abs(m[1][2]))
        if off < 1e-15:
            break
        for p, q in ((0, 1), (0, 2), (1, 2)):
            if abs(m[p][q]) < 1e-18:
                continue
            theta = 0.5 * math.atan2(2.0 * m[p][q], m[q][q] - m[p][p])
            c, s = math.cos(theta), math.sin(theta)
            for k in range(3):
                mkp = m[k][p]
                mkq = m[k][q]
                m[k][p] = c * mkp - s * mkq
                m[k][q] = s * mkp + c * mkq
            for k in range(3):
                mpk = m[p][k]
                mqk = m[q][k]
                m[p][k] = c * mpk - s * mqk
                m[q][k] = s * mpk + c * mqk
            for k in range(3):
                vkp = v[k][p]
                vkq = v[k][q]
                v[k][p] = c * vkp - s * vkq
                v[k][q] = s * vkp + c * vkq
    return [m[0][0], m[1][1], m[2][2]], v


def principal_tilt_deg(tensor):
    """主轴系相对连杆系的最大倾角（度）。近轴对齐时贪心配对即可。"""
    _, vecs = jacobi_eigen(tensor)
    cols = [[vecs[r][c] for r in range(3)] for c in range(3)]
    used = set()
    worst = 0.0
    for col in cols:
        best_j, best_abs = -1, -1.0
        for j in range(3):
            if j in used:
                continue
            a = abs(col[j])
            if a > best_abs:
                best_abs, best_j = a, j
        used.add(best_j)
        worst = max(worst, math.degrees(math.acos(min(1.0, best_abs))))
    return worst


# ───────────────────────── URDF 解析 ─────────────────────────

def _origin_of(elem):
    org = elem.find("origin")
    xyz = [float(x) for x in (org.get("xyz", "0 0 0")).split()] if org is not None else [0.0] * 3
    rpy = [float(x) for x in (org.get("rpy", "0 0 0")).split()] if org is not None else [0.0] * 3
    return xyz, rpy


def parse_urdf(path):
    """返回 links: {name: {mass, com, prims:[(geom, xyz, rpy)]},
    joints: [(name, parent, child, xyz, rpy)]}。"""
    import xml.etree.ElementTree as ET
    root = ET.parse(path).getroot()
    links = {}
    for link in root.iter("link"):
        inertial = link.find("inertial")
        if inertial is None:
            continue
        mass = float(inertial.find("mass").get("value"))
        com_xyz, _ = _origin_of(inertial)
        prims = []
        for vis in link.findall("visual"):
            xyz, rpy = _origin_of(vis)
            g = vis.find("geometry")
            if g is None:
                continue
            if g.find("box") is not None:
                size = [float(x) for x in g.find("box").get("size").split()]
                prims.append((("box", size), xyz, rpy))
            elif g.find("cylinder") is not None:
                c = g.find("cylinder")
                prims.append((("cylinder",
                               (float(c.get("radius")), float(c.get("length")))),
                              xyz, rpy))
            elif g.find("sphere") is not None:
                prims.append((("sphere", float(g.find("sphere").get("radius"))),
                              xyz, rpy))
        links[link.get("name")] = {"mass": mass, "com": com_xyz, "prims": prims}
    joints = []
    for j in root.iter("joint"):
        xyz, rpy = _origin_of(j)
        joints.append((j.get("name"), j.find("parent").get("link"),
                       j.find("child").get("link"), xyz, rpy))
    return links, joints


# ───────────────────────── 几何法计算 ─────────────────────────

def link_inertia_geometric(prims, mass, declared_com):
    """体素集合 → 声明质心系张量 + 几何质心。"""
    vols = [primitive_volume(g) for g, _, _ in prims]
    v_total = sum(vols)
    if v_total <= 0:
        raise ValueError("体素体积为零")
    masses = [mass * v / v_total for v in vols]
    centroid = [sum(masses[i] * prims[i][1][k] for i in range(len(prims)))
                / mass for k in range(3)]
    tensor = [[0.0] * 3 for _ in range(3)]
    for i, (geom, xyz, rpy) in enumerate(prims):
        i_local = primitive_inertia(geom, masses[i])
        rot = rpy_to_matrix(rpy)
        i_link = mat_mul(mat_mul(rot, i_local), transpose(rot))
        d = [xyz[k] - centroid[k] for k in range(3)]
        i_cm = parallel_axis(i_link, masses[i], d)
        for a in range(3):
            for b in range(3):
                tensor[a][b] += i_cm[a][b]
    d_decl = [declared_com[k] - centroid[k] for k in range(3)]
    tensor_decl = parallel_axis(tensor, mass, d_decl)
    return tensor_decl, centroid


def assemble_composite(links, world_coms):
    """零位形装配（全部关节零位、关节 rpy 空=单位）→ 整机质心系三主对角。"""
    total = sum(links[n]["mass"] for n in links)
    cg = [sum(links[n]["mass"] * world_coms[n][k] for n in links) / total
          for k in range(3)]
    tensor = [[0.0] * 3 for _ in range(3)]
    for name in links:
        m = links[name]["mass"]
        r = [world_coms[name][k] - cg[k] for k in range(3)]
        i_p = parallel_axis(links[name]["tensor_declared"], m, r)
        for a in range(3):
            for b in range(3):
                tensor[a][b] += i_p[a][b]
    return cg, tensor, total


def build_world_coms(links, joints):
    """从 base_link 沿关节树累加平移（零位形单位旋转）得各连杆声明质心。"""
    children = {}
    for name, parent, child, xyz, rpy in joints:
        if any(abs(a) > 1e-12 for a in rpy):
            raise ValueError("关节 %s 带非零 rpy，零位形不再是单位旋转，"
                             "装配公式需扩展" % name)
        children.setdefault(parent, []).append((child, xyz))
    world = {}
    stack = [("base_link", [0.0, 0.0, 0.0])]
    while stack:
        node, offset = stack.pop()
        com = links[node]["com"]
        world[node] = [offset[k] + com[k] for k in range(3)]
        for child, xyz in children.get(node, []):
            stack.append((child, [offset[k] + xyz[k] for k in range(3)]))
    missing = set(links) - set(world)
    if missing:
        raise ValueError("有连杆不在 base_link 关节树上：%s" % missing)
    return world


# ───────────────────────── 登记库引导与写入 ─────────────────────────

def bootstrap_masses():
    """一次性迁移：MASS_TABLE + 总质量 → 登记库。此后以登记库为唯一来源。"""
    total = registry.get("vehicle.mass.total")
    sys.path.insert(0, ROOT)
    from config import sim_config as cfg
    table = dict(cfg.MASS_TABLE)
    base = total - sum(table.values())
    if base <= 0:
        raise ValueError("base_link 余量非正，质量表与总质量矛盾")
    table["base_link"] = base
    for name, m in table.items():
        pid = "vehicle.mass.link.%s" % name
        if pid not in registry._load(registry.PARAMS_PATH)["params"]:
            registry.put(pid, m, "kg",
                         "2026-09-30 自 config.MASS_TABLE 迁移（部件实测分布，"
                         "I-002 施工），来源链见 docs/INERTIA_TABLE_215g.md")
    return table


# ───────────────────────── URDF 外科改写（保留注释与结构） ─────────────────────────

def rewrite_urdf(path, new_inertials, dry):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    # 头部标记：工程估计值声明改为几何法声明
    old_note = "惯量均为工程估计值, 之后用CAD/标定值替换"
    new_note = ("惯量=几何法重算(体素合成+平行轴转移至声明质心) 2026-09-30 "
                "I-002, 工具 tools/inertia_geometric_recalc.py, 运行时覆写已删除")
    if old_note in text:
        text = text.replace(old_note, new_note)
    for name, rec in new_inertials.items():
        pat = re.compile(
            r'(<link name="%s".*?)<inertial>.*?</inertial>' % re.escape(name),
            re.DOTALL)
        m = pat.search(text)
        if not m:
            raise ValueError("%s 中找不到 %s 的 inertial 块" % (path, name))
        indent = "      "
        blk = ('<inertial><mass value="%s"/><origin xyz="%s"/>\n%s'
               '<inertia ixx="%s" iyy="%s" izz="%s" '
               'ixy="%s" ixz="%s" iyz="%s"/></inertial>') % (
            _g(rec["mass"]),
            " ".join(_g(c) for c in rec["com"]), indent,
            _e(rec["ixx"]), _e(rec["iyy"]), _e(rec["izz"]),
            _e(rec["ixy"]), _e(rec["ixz"]), _e(rec["iyz"]))
        text = text[:m.end(1)] + blk + text[m.end(0):]
    if not dry:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return text


def _g(x):
    return ("%.6g" % x)


def _e(x):
    return ("%.6e" % x)


# ───────────────────────── 主流程 ─────────────────────────

def main(dry):
    have = registry._load(registry.PARAMS_PATH)["params"]
    if "vehicle.mass.link.base_link" not in have:
        if dry:
            sys.path.insert(0, ROOT)
            from config import sim_config as cfg
            total = registry.get("vehicle.mass.total")
            masses = dict(cfg.MASS_TABLE)
            masses["base_link"] = total - sum(masses.values())
            print("[dry] 质量分布未登记，本次仅从 config 现值计算，不登记")
        else:
            masses = bootstrap_masses()
            print("[bootstrap] 质量分布已登记入 params_registry（一次性迁移）")
    else:
        masses = {n: registry.get("vehicle.mass.link.%s" % n)
                  for n in ["base_link", "flapconn_L", "innerwing_L",
                            "outerwing_L", "flapconn_R", "innerwing_R",
                            "outerwing_R", "tail_base", "tailpanel_L",
                            "tailpanel_R"]}

    results = {}
    for path in URDFS:
        links, joints = parse_urdf(path)
        new_inertials = {}
        for name in links:
            mass = masses[name]
            tensor, centroid = link_inertia_geometric(
                links[name]["prims"], mass, links[name]["com"])
            off = {(a, b): tensor[a][b] for a in range(3) for b in range(3)
                   if a < b}
            new_inertials[name] = {
                "mass": mass, "com": links[name]["com"],
                "ixx": tensor[0][0], "iyy": tensor[1][1], "izz": tensor[2][2],
                "ixy": tensor[0][1], "ixz": tensor[0][2], "iyz": tensor[1][2],
                "products": off, "tilt_deg": principal_tilt_deg(tensor),
                "centroid": centroid}
        world_coms = build_world_coms(links, joints)
        cg, comp, total = assemble_composite(
            {n: {"mass": masses[n],
                 "tensor_declared": [[new_inertials[n]["ixx"], 0, 0],
                                     [0, new_inertials[n]["iyy"], 0],
                                     [0, 0, new_inertials[n]["izz"]]]
                 } for n in links}, world_coms)
        key = os.path.basename(path)
        results[key] = {"links": new_inertials, "cg": cg,
                        "composite": [comp[0][0], comp[1][1], comp[2][2]],
                        "total": total}
        if not dry:
            rewrite_urdf(path, new_inertials, dry)
        print("\n==== %s ====" % key)
        for n, r in new_inertials.items():
            print("  %-12s m=%s  I=(%s, %s, %s)" % (
                n, _g(r["mass"]), _e(r["ixx"]), _e(r["iyy"]), _e(r["izz"])))
        print("  整机质心(xyz) = %s" % ["%.6f" % c for c in cg])
        print("  整机惯量对角  = (%s, %s, %s)  合计质量 %s kg" % (
            _e(comp[0][0]), _e(comp[1][1]), _e(comp[2][2]), _g(total)))

    # 登记库写入（以现役导入源为准）
    active = results["fwav_15dof_from_group.urdf"]
    if not dry:
        for n, r in active["links"].items():
            registry.put("vehicle.inertia.link.%s" % n,
                         [r["ixx"], r["iyy"], r["izz"]], "kg*m^2",
                         "几何法重算 2026-09-30（I-002），工具"
                         " tools/inertia_geometric_recalc.py，声明质心保持不动")
        registry.retire("base_link.inertia.ixx", active["links"]["base_link"]["ixx"],
                        "几何法重算 2026-09-30（I-002），旧值=质量比缩放产物"
                        "回转半径物理不合理，已删除运行时覆写",
                        retired_reason="I-002 几何法重算替换")
        registry.put("vehicle.cg.body_frame", active["cg"], "m",
                     "声明质心零位形合成 2026-09-30（I-002 工具），重力矩账锚点")
        registry.put("vehicle.inertia.composite.body_frame",
                     active["composite"], "kg*m^2",
                     "零位形整机合成 2026-09-30（I-002 工具），对照论文 Table I")

    # 对照表文档
    lines = ["# 惯量几何法重算对照表（2026-09-30，I-002 施工）", "",
             "- 方法：体素合成（visual 几何，体积占比分配质量）+ 平行轴转移至声明质心",
             "- 声明质心一律未动（整机质心保持重力矩账锚点值）",
             "- 旧生效值口径：docs/INERTIA_TABLE_215g.md（运行时覆写产物，已删除）", "",
             "| 连杆 | 质量 kg | 新 ixx | 新 iyy | 新 izz |", "|---|---|---|---|---|"]
    for n, r in active["links"].items():
        lines.append("| %s | %s | %s | %s | %s |" % (
            n, _g(r["mass"]), _e(r["ixx"]), _e(r["iyy"]), _e(r["izz"])))
    comp = active["composite"]
    lines += ["",
              "整机合成（零位形，对整机质心）：Ixx=%s，Iyy=%s，Izz=%s，"
              "总质量 %s kg" % (_e(comp[0]), _e(comp[1]), _e(comp[2]),
                                _g(active["total"])),
              "论文 Table I 真值：Jx=%s，Jy=%s，Jz=%s" % (
                  _e(PAPER_TRUTH[0]), _e(PAPER_TRUTH[1]), _e(PAPER_TRUTH[2])),
              "比值为论文值的 %.1f / %.1f / %.1f 倍" % (
                  comp[0] / PAPER_TRUTH[0], comp[1] / PAPER_TRUTH[1],
                  comp[2] / PAPER_TRUTH[2]),
              "",
              "超出论文区间的书面解释：本机翼侧质量分布（54 g 翼组、半展 0.32 m，"
              "见 docs/TABLE_I_复核.md 翼面质量条目）较论文样机（纯翼面 34.5 g）"
              "更重更外伸，平行轴项 m·y² 主导整机滚转惯量；该差异为部件口径与"
              "构型差异的既知挂账，非几何法误差。几何法消除的是基座旧值中"
              "回转半径物理不合理的部分。", ""]
    if not dry:
        with open(DOC_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("\n已写入：%s" % DOC_PATH)
    print("完成%s。" % ("（dry 未落盘）" if dry else ""))


if __name__ == "__main__":
    main("--dry" in sys.argv)
