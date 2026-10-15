# -*- coding: utf-8 -*-
"""F02 审计：关键同步失败后驱动是否仍继续写入动作。

真实入口：modules.wing_actuation._drive_wing_joints（生产源码本体）。
注入点与生产调用点一一对应：
  A 拍频同步失败  —— aero_cb.model.set_wing_command 抛异常（wing_actuation:93）
  B 幅值发布失败  —— aero_cb.model.amp_cmd 元组赋值抛异常（wing_actuation:145）
  C 停拍发布失败  —— aero_cb.model.sd_gate_cmd 元组赋值抛异常（wing_actuation:153）
  D 尾舵发布失败  —— aero_cb.tail_body.set_command 抛异常（wing_actuation:259）
  E 气动侧尾翼计算失败继续运行规则 —— AeroPhysicsCallback._call_inner 3b 段
    （aero_solver.py:417-431，尾翼 compute 抛异常后无机翼中断地继续）
每个场景记录：真实调用顺序、已发生写入、返回值、日志、状态有效性。
"""
import harness_common as H
from modules.wing_actuation import _drive_wing_joints


def fresh():
    H.seed_host_namespace(globals())
    art = H.ArtStub(H.JOINT_NAMES)
    wk = H.make_wk()
    cb = H.make_aero_cb()
    tail = H.TailProxy(cb.tail_body)
    cb.tail_body = tail
    return art, wk, cb, tail


def call_seq(art, tail):
    seq = []
    for c in art.calls:
        seq.append("art." + c[0])
    for c in tail.calls:
        seq.append("tail." + c[0])
    return seq


def scenario_a():
    """A 拍频同步失败。"""
    art, wk, cb, tail = fresh()
    cap = H.attach_capture()
    real = cb.model.set_wing_command

    def boom(**kw):
        raise RuntimeError("注入：拍频同步 set_wing_command 失败")
    cb.model.set_wing_command = boom
    ret = _drive_wing_joints(None, art, wk, None, cb, H.ap_out(freq=2.4),
                             step_count=1)
    cb.model.set_wing_command = real
    return {
        "scenario": "A 拍频同步失败（set_wing_command 抛异常）",
        "returned": repr(ret),
        "raised_to_caller": False,
        "call_sequence": call_seq(art, tail),
        "joint_target_writes": sum(1 for s in call_seq(art, tail)
                                   if s.startswith("art.set_joint")),
        "tail_writes": len(tail.calls),
        "model_freq_after": float(cb.model.wing_left.frequency),
        "wk_freq_cmd": 2.4,
        "freq_sync_lost": abs(float(cb.model.wing_left.frequency) - 2.4) > 1e-9,
        "logs": cap.dump(),
    }


def scenario_b(attr):
    """B/C 元组发布失败（amp_cmd / sd_gate_cmd 赋值抛异常）。"""
    art, wk, cb, tail = fresh()
    cap = H.attach_capture()
    real_model = cb.model
    cb.model = H.ModelAttrProxy(real_model, [attr])
    try:
        ret = _drive_wing_joints(None, art, wk, None, cb,
                                 H.ap_out(dA=0.05, gate_L=0.0, gate_R=1.0),
                                 step_count=1)
    finally:
        cb.model = real_model
    published = getattr(real_model, attr, "<未发布>")
    return {
        "scenario": "发布失败（%s 元组赋值抛异常）" % attr,
        "returned": repr(ret),
        "raised_to_caller": False,
        "call_sequence": call_seq(art, tail),
        "joint_target_writes": sum(1 for s in call_seq(art, tail)
                                   if s.startswith("art.set_joint")),
        "tail_writes": len(tail.calls),
        "model_%s_after" % attr: str(published),
        "logs": cap.dump(),
    }


def scenario_d():
    """D 尾舵发布失败（tail_body.set_command 抛异常）。"""
    art, wk, cb, tail = fresh()
    cap = H.attach_capture()
    tail.inject_set_command_error(RuntimeError("注入：尾舵 set_command 失败"))
    ret = _drive_wing_joints(None, art, wk, None, cb, H.ap_out(elevator=0.03),
                             step_count=1)
    return {
        "scenario": "D 尾舵发布失败（tail_body.set_command 抛异常）",
        "returned": repr(ret),
        "raised_to_caller": False,
        "call_sequence": call_seq(art, tail),
        "joint_target_writes": sum(1 for s in call_seq(art, tail)
                                   if s.startswith("art.set_joint")),
        "tail_writes_attempted": len(tail.calls),
        "tail_writes_succeeded": 0,
        "logs": cap.dump(),
    }


def scenario_e():
    """E 气动侧尾翼计算失败：回调是否无机翼中断地继续运行。"""
    import numpy as np
    view = H.ViewStub()
    cb = H.make_aero_cb(view)
    cap = H.attach_capture()
    real = cb.tail_body.compute

    def boom(*a, **k):
        raise RuntimeError("注入：尾翼 compute 失败")
    cb.tail_body.compute = boom
    raised = None
    try:
        cb(1.0 / 120.0)
    except Exception as e:
        raised = repr(e)
    f_applied = view.applied[-1] if view.applied else None
    wing_f = None if cb.model.current_F_world is None \
        else np.asarray(cb.model.current_F_world).tolist()
    cb.tail_body.compute = real
    return {
        "scenario": "E 气动回调内尾翼 compute 失败（aero_solver 3b 段）",
        "raised_out_of_callback": raised,
        "forces_still_applied": f_applied is not None,
        "applied_force": f_applied,
        "model_current_F_world_nonzero": bool(wing_f is not None
                                              and any(abs(x) > 1e-9 for x in wing_f)),
        "last_tail_F_absent_or_zero": not getattr(cb, "last_tail_F", np.zeros(3)).any(),
        "logs": cap.dump(),
    }


def main():
    result = {
        "probe": "F02 关键同步失败后继续写入",
        "entry": "modules.wing_actuation._drive_wing_joints / "
                 "modules.aero_solver.AeroPhysicsCallback.__call__",
        "A": scenario_a(),
        "B_amp": scenario_b("amp_cmd"),
        "C_gate": scenario_b("sd_gate_cmd"),
        "D": scenario_d(),
        "E": scenario_e(),
    }
    for k in ("A", "B_amp", "C_gate", "D"):
        r = result[k]
        print(k, r["scenario"], "| 返回", r["returned"], "| 关节写入",
              r["joint_target_writes"], "| 尾舵写入", r.get("tail_writes",
              r.get("tail_writes_succeeded")), "| 日志",
              len(r["logs"]))
    print("E", result["E"]["scenario"], "| 回调抛出", result["E"]["raised_out_of_callback"],
          "| 力照常施加", result["E"]["forces_still_applied"])
    H.save_result("f02_sync_failure.json", result)


if __name__ == "__main__":
    H.run_probe(main)
