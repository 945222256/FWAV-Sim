# -*- coding: utf-8 -*-
"""F01 审计：时机差动（dlag）归零后两翼 pitch_lag_s 是否保留旧值。

真实入口：modules.wing_actuation._drive_wing_joints（生产源码本体）。
序列：正向 dlag → 0；负向 dlag → 0；键缺失；退出自动模式（ap_out=None）；
手动模式。每步记录两翼 pitch_lag_s、写入次数与回放缓存 dlag 列。
对照组：amp_cmd（幅值差动）与集体拍频同序列下零值是否被兑现。
"""
import harness_common as H
from modules.wing_actuation import _drive_wing_joints


def lag_pair(cb):
    return [float(cb.model.wing_left.pitch_lag_s),
            float(cb.model.wing_right.pitch_lag_s)]


def main():
    H.seed_host_namespace(globals())
    cap = H.attach_capture()
    art = H.ArtStub(H.JOINT_NAMES)
    wk = H.make_wk()
    cb = H.make_aero_cb()
    import config.sim_config as cfg
    lag0 = float(cfg.FlapKinematicsConfig.PITCH_LAG_S)

    steps = []

    def step(k, ap=None, man=None, note=""):
        _drive_wing_joints(None, art, wk, man, cb, ap, step_count=k)
        rec = {"step": k, "note": note,
               "ap_dlag": None if ap is None else ap.get("dlag", "<键缺失>"),
               "pitch_lag_s": lag_pair(cb),
               "wing_state_cache_dlag": (globals()["_WING_STATE_CACHE"] or [None] * 5)[4],
               "joint_writes": sum(1 for c in art.calls
                                   if c[0] == "set_joint_position_targets")}
        steps.append(rec)
        return rec

    r_base = step(1, H.ap_out(), note="基线 dlag=0（首步斜坡内）")
    base_pair = r_base["pitch_lag_s"]

    # 正向 → 零
    step(2, H.ap_out(dlag=0.010), note="dlag=+0.010")
    step(3, H.ap_out(dlag=0.010), note="dlag=+0.010（重复）")
    r_pos0 = step(4, H.ap_out(dlag=0.0), note="dlag=0（撤销正向）")

    # 负向 → 零
    step(5, H.ap_out(dlag=-0.020), note="dlag=-0.020")
    r_neg0 = step(6, H.ap_out(dlag=0.0), note="dlag=0（撤销负向）")

    # 键缺失（等价零）
    ap_nokey = H.ap_out()
    del ap_nokey["dlag"]
    r_nokey = step(7, ap_nokey, note="dlag 键缺失")

    # 退出自动模式：ap_out=None、无手动通道（DP 频率 None → 全缺省分支）
    r_exit = step(8, None, None, note="退出自动模式 ap_out=None")

    # 手动模式
    r_man = step(9, None, {"throttle": 0.2, "elevator": 0.0, "aileron": 0.0,
                           "rudder": 0.0}, note="手动模式（dlag 通道不存在）")

    # 对照组：幅值差动 amp_cmd 的零值兑现情况
    step(10, H.ap_out(dA=0.05), note="对照 dA=+0.05")
    r_amp0 = step(11, H.ap_out(dA=0.0), note="对照 dA=0（撤销）")
    amp_after = [float(cb.model.wing_left.amplitude), float(cb.model.wing_right.amplitude)]

    # 对照组：dfreq 差动频率的零值兑现情况
    step(12, H.ap_out(freq=2.2, dfreq=0.05), note="对照 dfreq=+0.05")
    step(13, H.ap_out(freq=2.2, dfreq=0.0), note="对照 dfreq=0（撤销）")
    fq_after = [float(cb.model.wing_left.frequency), float(cb.model.wing_right.frequency)]

    result = {
        "probe": "F01 时机差动归零",
        "entry": "modules.wing_actuation._drive_wing_joints",
        "config_PITCH_LAG_S": lag0,
        "baseline_pair_after_first_step": base_pair,
        "steps": steps,
        "controls": {
            "amp_cmd_after_dA_zero": amp_after,
            "amp_cmd_symmetric": abs(amp_after[0] - amp_after[1]) < 1e-12,
            "wing_freq_after_dfreq_zero": fq_after,
            "wing_freq_equal_collective": abs(fq_after[0] - fq_after[1]) < 1e-12,
        },
        "verdict_facts": {
            "positive_then_zero_retained":
                r_pos0["pitch_lag_s"] == steps[2]["pitch_lag_s"],
            "negative_then_zero_retained":
                r_neg0["pitch_lag_s"] == steps[4]["pitch_lag_s"],
            "key_missing_retained":
                r_nokey["pitch_lag_s"] == steps[4]["pitch_lag_s"],
            "exit_auto_retained":
                r_exit["pitch_lag_s"] == steps[4]["pitch_lag_s"],
            "manual_mode_retained":
                r_man["pitch_lag_s"] == steps[4]["pitch_lag_s"],
            "cache_records_zero_while_model_retains_offset":
                r_pos0["wing_state_cache_dlag"] == 0.0
                and r_pos0["pitch_lag_s"] != [lag0, lag0],
        },
        "logs": cap.dump(),
    }
    H.save_result("f01_dlag_zero.json", result)
    print("保留判定:", json.dumps(result["verdict_facts"], ensure_ascii=False))


if __name__ == "__main__":
    import json
    H.run_probe(main)
