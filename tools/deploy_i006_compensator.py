# -*- coding: utf-8 -*-
"""
deploy_i006_compensator.py — 裁决后一键部署跑生成器（I-008 路线，I-006 配套）

用途：口径裁决放行暂定矩阵后，本工具从矩阵产物自动生成科目一复跑的完整
环境配方命令（差动三级带通超前 + 升降舵与方向舵延迟对消结构参数），使
裁决到部署零手工延迟。当前仅生成命令打印，不自动执行；执行开关与判据
纪律（判据先于数据、判读窗以空中末时为界、覆盖打印核验）由调用方遵守。

用法：python tools/deploy_i006_compensator.py [matrix.json]
默认矩阵=跳变三窗组暂定版本。延迟对消结构采用一阶形式
（反馈微分明晰项 y+τ̂·dy/dt 等效于对延迟相位的一阶补偿），τ̂ 取矩阵
延迟辨识值；残余相位差额动通道按三级双线性带通超前。
"""
import json
import math
import sys

F_MODE = 2.33


def main(path):
    d = json.load(open(path, encoding="utf-8"))
    chs = ["ail", "elev", "rud"]
    print("# 裁决后部署配方（由 %s 生成）" % path)
    print("# 判据：科目一五条判据不变；判读窗以空中末时为界；跑前核验覆盖打印")
    env = {
        "FLAPPER_BANK_TEST": "1",
        "FLAPPER_BANK_DEG": "5",
        "FLAPPER_BANK_T": "8.55",
        "FLAPPER_BANK_SLEW": "0.08",
        "FLAPPER_BANK_FREQ_CAP": "2.6",
        "FLAPPER_BANK_PHOLD": "1",
        "FLAPPER_BANK_PHOLD_DEG": "-6.5",
        "FLAPPER_YAW_FF": "1",
        "FLAPPER_YAW_FF_DIR": "-1",
        "FLAPPER_YAW_FF_ADAPT": "1",
        "FLAPPER_COORD_KB": "1.0",
        "FLAPPER_COORD_I_MAX": "0.8",
        "FLAPPER_YAW_DAMP": "0.9",
        "FLAPPER_ROLL_KI": "0.106",
        "FLAPPER_DA_LPF_TAU": "0.05",
        "FLAPPER_ROLL_LEAD_TD": "0.10",
        "FLAPPER_ROLL_LEAD_STAGES": "3",
        "FLAPPER_ROLL_LEAD_BP_K": "0.352",
        "FLAPPER_PITCH_LEAD_TD": "0.10",
        "FLAPPER_PITCH_DAMP": "0.474",
        "FLAPPER_ATT_QUAT_ERR": "1",
        "FLAPPER_KP_R": "0.088",
        "FLAPPER_K_DA": "0.0141",
        "FLAPPER_K_DA_R": "0.0028",
        "FLAPPER_FLIGHT_LOG": "1",
        "FLAPPER_TAG": "I008_comp_deploy",
        "FLAPPER_MAX_S": "35",
    }
    for k in ("ail", "elev", "rud"):
        i = chs.index(k)
        env["FLAPPER_TAU_HAT_%s" % k.upper()] = "%.3f" % d["tau_delay"][i]
    cmd = " ".join("%s=%s" % kv for kv in env.items())
    print('"%s/python.bat" main_sim.py' % "G:/Playground/isaac_sim_5.1.0")
    print("环境配方：")
    print("  " + cmd)
    print("\n注：延迟对消注入口（TAU_HAT 键）属新结构，控制器内对应开关")
    print("施工完成后本配方即生效；当前配方为差动三级超前+调度增益基线。")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else
         "logs/ident_matrix/I006hop_20260930_153942/matrix.json")
