# -*- coding: utf-8 -*-
"""力律分量分解探针：复刻 _wing_forces 数学，按四个分量分别累计周期平均力。
目的：找出巡航速度下荒谬力（V=5 时 Fx=−11N、Fz=18.7N≈2.7×W）的出处。
分量：norm(法向，|C_N|·cos→z)、tang(切向，沿 v_rel)、rot(旋转项)、added(附加质量)。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from modules.aero_solver import FlapperAeroModel, quat_to_rotation_matrix, euler_to_quat

m = FlapperAeroModel()
m.set_wing_command(frequency=1.9)

def decompose(v_air_world, pitch=0.177):
    quat = euler_to_quat(0.0, pitch, 0.0)
    R = quat_to_rotation_matrix(quat)
    v_body = R.T @ np.asarray(v_air_world, float)
    w0 = np.zeros(3)
    ts = np.arange(0.0, 16 / 1.9, 1.0 / (1.9 * 100))
    comp = {k: np.zeros(3) for k in ("norm", "tang", "rot", "added")}
    n = len(ts)
    for t in ts:
        for sign, wing in ((+1, m.wing_left), (-1, m.wing_right)):
            r_c = sign * m._r_centers
            ws = wing.evaluate(t, 0.0 if sign > 0 else m.phase_offset_lr)
            phi, alpha = ws["phi"], ws["alpha"]
            phi_dot, phi_ddot = ws["phi_dot"], ws["phi_ddot"]
            n_st = len(r_c)
            r_body = np.zeros((n_st, 3)); r_body[:, 1] = r_c; r_body += m.root_offset
            v_flap = np.zeros((n_st, 3))
            v_flap[:, 0] = phi_dot * np.abs(r_c) * np.cos(phi)
            v_flap[:, 1] = phi_dot * r_c * np.sin(phi)
            v_rel = -(v_body + v_flap)
            vmag = np.linalg.norm(v_rel, axis=1)
            scale = np.where(vmag > m.max_wing_vel, m.max_wing_vel / np.maximum(vmag, 1e-9), 1.0)
            v_rel = v_rel * scale[:, None]
            vmag = np.linalg.norm(v_rel, axis=1)
            vh = v_rel[:, 0]; vv = v_rel[:, 2]
            inflow = np.arctan2(np.clip(vv, -1.5, 1.5), np.maximum(np.abs(vh), 1e-9))
            aeff = alpha + inflow
            q = 0.5 * m.rho * vmag ** 2
            CN = m._C_N(aeff); CT = m._C_T(aeff)
            dr = m._dr
            dN = q * np.abs(CN) * m._chords * dr
            comp["norm"][2] += np.sum(dN * np.cos(aeff))
            vdir = v_rel / np.maximum(vmag[:, None], 1e-9)
            dT = q * CT * m._chords * dr
            comp["tang"] += np.sum(dT[:, None] * vdir, axis=0)
            comp["rot"][2] += np.sum(m.c_rot * m.rho * m._chords * vmag
                                     * ws["alpha_dot"] * dr)
            v_added = m._strip_areas * m.added_mass_coeff
            a_f = phi_ddot * np.abs(r_c)
            comp["added"][0] += np.sum(-m.rho * v_added * a_f * np.cos(phi))
            comp["added"][1] += np.sum(-m.rho * v_added * phi_ddot * r_c * np.sin(phi))
    for k in comp:
        comp[k] /= n
    return comp

print("工况（机体坐标：+x 前飞）      norm_x   norm_z   tang_x   tang_z   "
      "rot_z   added_x  | 合计 Fx    Fz")
for label, v in [("悬停 v=0", (0, 0, 0)),
                 ("前飞 2", (2, 0, 0)), ("前飞 5", (5, 0, 0)), ("前飞 10", (10, 0, 0)),
                 ("倒飞 5", (-5, 0, 0)), ("侧飞 5", (0, 5, 0))]:
    c = decompose(np.array(v, float))
    Fx = c["tang"][0] + c["added"][0]
    Fz = c["norm"][2] + c["tang"][2] + c["rot"][2]
    print("%-12s %+8.2f %+8.2f %+8.2f %+8.2f %+8.2f %+8.2f | %+8.2f %+8.2f"
          % (label, 0, c["norm"][2], c["tang"][0], c["tang"][2], c["rot"][2],
             c["added"][0], Fx, Fz))
