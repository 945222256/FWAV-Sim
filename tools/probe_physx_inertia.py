# -*- coding: utf-8 -*-
"""
probe_physx_inertia.py — PhysX 编译真值惯量直读（修订第八条）

目的：验证直读方法与关节体接口的可用性，并产出三列对照表：
  列1  URDF 声明值      —— assets/flapper/fwav_15dof_from_group.urdf（导入源）
  列2  MASS_TABLE 覆写后值 —— 加载器 _unify_mass 写入 USD MassAPI 的值
  列3  PhysX 编译真值    —— world.reset() 后 RigidPrimView.get_masses()/get_inertias()

用法： g:\\Playground\\isaac_sim_5.1.0\\python.bat tools\\probe_physx_inertia.py <tag>
产物：  logs/physx_inertia_<tag>.csv + docs/INERTIA_TABLE_<tag>.md
注意：  kit 吞 stdout，文件是唯一可靠通道（experiment.py 同款约定）。
"""
import os
import re
import sys
import csv
import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import sim_config as cfg

_app_cfg = dict(cfg.SIM_APP_CONFIG)
_app_cfg["headless"] = True

from isaacsim import SimulationApp  # noqa: E402  必须最先启动
simulation_app = SimulationApp(_app_cfg)

import numpy as np  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402
from pxr import UsdPhysics  # noqa: E402
from isaacsim.core.api import World  # noqa: E402
from isaacsim.core.prims import RigidPrim as RigidPrimView  # noqa: E402
import modules.flapper_loader as flapper_loader  # noqa: E402


def urdf_declared(path):
    """列1：URDF 导入源文件的逐连杆质量与惯量声明值（含惯性积）。"""
    tree = ET.parse(path)
    out = {}
    for link in tree.getroot().iter("link"):
        inertial = link.find("inertial")
        if inertial is None:
            continue
        m = float(inertial.find("mass").get("value"))
        i = inertial.find("inertia")
        out[link.get("name")] = (m, float(i.get("ixx")),
                                 float(i.get("iyy")), float(i.get("izz")),
                                 float(i.get("ixy", 0)), float(i.get("ixz", 0)),
                                 float(i.get("iyz", 0)))
    return out


def main(tag):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    world = World(stage_units_in_meters=1.0,
                  physics_dt=cfg.PHYSICS_DT, rendering_dt=cfg.PHYSICS_DT)
    flapper_loader.load_flapper(world)
    loader = flapper_loader.get_loader()
    stage = loader.stage
    model_path = f"{cfg.FLAPPER_PRIM_PATH}/Model"

    # ── 连杆清单 + 列2（USD MassAPI 覆写后值）──
    links = []   # (path, name)
    usd_vals = {}
    for prim in stage.Traverse():
        p = str(prim.GetPath())
        if not p.startswith(model_path):
            continue
        if not prim.HasAPI(UsdPhysics.MassAPI):
            continue
        name = p.rstrip("/").rsplit("/", 1)[-1]
        api = UsdPhysics.MassAPI(prim)
        m = api.GetMassAttr().Get() or 0.0
        di = api.GetDiagonalInertiaAttr().Get()
        if di is not None:
            di = (float(di[0]), float(di[1]), float(di[2]))
        usd_vals[name] = (float(m), di)
        links.append((p, name))

    # ── 列3（PhysX 编译真值）──
    view = RigidPrimView(prim_paths_expr=[p for p, _ in links],
                         name="inertia_probe", reset_xform_properties=False)
    world.scene.add(view)
    world.reset()
    masses = np.asarray(view.get_masses(), dtype=float).ravel()
    inertias = np.asarray(view.get_inertias(), dtype=float).reshape(len(links), 9)
    view_paths = [str(p) for p in view.prim_paths]

    # 惯量张量行主序：[0]=Ixx [1]=Ixy [2]=Ixz [4]=Iyy [5]=Iyz [8]=Izz
    by_path = {p: (float(masses[i]), float(inertias[i][0]),
                   float(inertias[i][4]), float(inertias[i][8]),
                   float(inertias[i][1]), float(inertias[i][2]),
                   float(inertias[i][5]))
               for i, p in enumerate(view_paths)}

    # ── 三列对照 ──
    urdf = urdf_declared(cfg.FLAPPER_URDF_PATH)
    rows = []
    for p, name in links:
        px = by_path.get(p, (float("nan"),) * 10)
        u = urdf.get(name)
        rows.append({
            "link": name,
            "urdf_mass": u[0] if u else "", "urdf_ixx": u[1] if u else "",
            "urdf_iyy": u[2] if u else "", "urdf_izz": u[3] if u else "",
            "urdf_ixy": u[4] if u else "", "urdf_ixz": u[5] if u else "",
            "urdf_iyz": u[6] if u else "",
            "usd_mass": usd_vals[name][0],
            "usd_ixx": usd_vals[name][1][0] if usd_vals[name][1] else "",
            "usd_iyy": usd_vals[name][1][1] if usd_vals[name][1] else "",
            "usd_izz": usd_vals[name][1][2] if usd_vals[name][1] else "",
            "physx_mass": px[0], "physx_ixx": px[1],
            "physx_iyy": px[2], "physx_izz": px[3],
            "physx_ixy": px[4], "physx_ixz": px[5], "physx_iyz": px[6],
        })

    sum_urdf = sum(r["urdf_mass"] for r in rows if r["urdf_mass"] != "")
    sum_usd = sum(r["usd_mass"] for r in rows)
    sum_px = sum(r["physx_mass"] for r in rows)

    # ── 落盘 csv ──
    os.makedirs(cfg.LOG_DIR, exist_ok=True)
    csv_path = os.path.join(cfg.LOG_DIR, f"physx_inertia_{tag}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ── 落盘 md 对照表 ──
    md = [f"# PhysX 真实惯量直读对照表（{tag}，{ts}）", "",
          f"- URDF 导入源：`{cfg.FLAPPER_URDF_PATH}`",
          f"- USD 资产：`{cfg.FLAPPER_URDF_USD}`",
          f"- 配置总质量 cfg.MASS = {cfg.MASS} kg",
          f"- 直读接口：`RigidPrimView.get_masses()/get_inertias()`"
          f"（world.reset() 后，PhysX 编译真值）", "",
          "| 连杆 | URDF质量 | USD覆写质量 | PhysX质量 | URDF ixx/iyy/izz |"
          " USD覆写 ixx/iyy/izz | PhysX ixx/iyy/izz |",
          "|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append("| {link} | {u0} | {s0} | {p0} | {u1}/{u2}/{u3} |"
                  " {s1}/{s2}/{s3} | {p1}/{p2}/{p3} |".format(
                      link=r["link"], u0=r["urdf_mass"], s0=f"{r['usd_mass']:.6g}",
                      p0=f"{r['physx_mass']:.6g}",
                      u1=r["urdf_ixx"], u2=r["urdf_iyy"], u3=r["urdf_izz"],
                      s1=_f6(r["usd_ixx"]), s2=_f6(r["usd_iyy"]), s3=_f6(r["usd_izz"]),
                      p1=_f6(r["physx_ixx"]), p2=_f6(r["physx_iyy"]),
                      p3=_f6(r["physx_izz"])))
    # 惯性积对照（URDF 六元素张量 vs PhysX 编译值；逐格判据覆盖本节）
    md += ["", "## 惯性积对照（非零项）", "",
           "| 连杆 | URDF ixy/ixz/iyz | PhysX ixy/ixz/iyz |", "|---|---|---|"]
    for r in rows:
        _nz = lambda k: abs(float(r[k])) > 1e-12 if r[k] != "" else False
        if any(_nz(k) for k in ("urdf_ixy", "urdf_ixz", "urdf_iyz",
                                "physx_ixy", "physx_ixz", "physx_iyz")):
            md.append("| %s | %s | %s |" % (
                r["link"],
                "/".join(_f6(r[k]) for k in ("urdf_ixy", "urdf_ixz", "urdf_iyz")),
                "/".join(_f6(r[k]) for k in ("physx_ixy", "physx_ixz", "physx_iyz"))))
    md += ["", f"- 合计：URDF {sum_urdf:.4f} kg / USD覆写 {sum_usd:.4f} kg /"
           f" PhysX {sum_px:.4f} kg（配置 {cfg.MASS} kg）",
           f"- 原始数据：`{csv_path}`", ""]
    md_path = os.path.join(PROJECT_ROOT, "docs", f"INERTIA_TABLE_{tag}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    # stdout 兜底（kit 可能吞）
    print("\n".join(md))
    with open(os.path.join(cfg.LOG_DIR, f"physx_inertia_{tag}_{ts}.txt"),
              "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    world.reset()
    simulation_app.close()


def _f6(x):
    if x == "" or x is None:
        return "—"
    try:
        return f"{float(x):.3e}"
    except (TypeError, ValueError):
        return str(x)


if __name__ == "__main__":
    _tag = sys.argv[1] if len(sys.argv) > 1 else "probe"
    main(_tag)
