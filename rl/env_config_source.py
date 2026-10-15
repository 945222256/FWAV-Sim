# -*- coding: utf-8 -*-
"""
env_config_source.py — 训练环境构型参数的唯一读取入口（I-073 脚手架）
======================================================================
契约：训练环境的构型参数从参数登记库（params_registry）读现行值，
本模块是唯一入口。缺键立刻抛 KeyError 并附键名，不提供任何静默
默认值——构造级保证，对应"构造级保证拒收装门"的既定规则。
正式切换属 I-073 批准后施工；本模块当前只被脚手架环境引用。
"""
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import params_registry.registry as registry

# 键与语义的对照表（与预登记文件第二节一致，改动须走预登记流程）
KEY_MAP = {
    "mass_kg": "vehicle.mass.total",
    "span_m": "aero.geometry.span",
    "root_chord_m": "aero.geometry.root_chord",
    "tip_chord_m": "aero.geometry.tip_chord",
    "ixx_composite": "vehicle.inertia.composite.body_frame",
    "amplitude_deg": "wave.kinematics.amplitude",
    "cn_fit_amp": "aero.wing.cn_fit_amp",
    "ct_fit_amp": "aero.wing.ct_fit_amp",
    "wingspan_total_m": "vehicle.wingspan.total",
}


def _parse(raw):
    """登记库的 value 字段按 JSON 字符串存储，这里解回原类型。

    解析失败说明登记内容不是合法 JSON 数值，同样按缺键级错误处理，
    不许静默拿字符串顶数。
    """
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        raise KeyError(
            f"登记库 value 不是合法 JSON：{raw!r}，"
            f"训练环境拒绝使用，请先修登记。")


def current_config():
    """读登记库现行构型。缺键抛 KeyError，附键名与用途。"""
    cfg = {}
    for name, key in KEY_MAP.items():
        try:
            cfg[name] = _parse(registry.get(key))
        except KeyError as e:
            raise KeyError(
                f"参数登记库读不到键 {key}（用途 {name}）：{e}")
        except Exception as e:
            raise KeyError(
                f"参数登记库读不到键 {key}（用途 {name}），"
                f"训练环境拒绝用默认值顶替，请先补登记。原始错误：{e}")
    return cfg


def snapshot_line(cfg):
    """一行式快照，供环境构造时打印与数据集工具对账。"""
    return ("config_snapshot: mass={mass_kg}kg span={span_m}m "
            "root={root_chord_m} tip={tip_chord_m} "
            "A0={amplitude_deg}deg cn={cn_fit_amp} ct={ct_fit_amp} "
            "ixx0={ixx}".format(ixx=cfg["ixx_composite"][0], **cfg))


if __name__ == "__main__":
    cfg = current_config()
    print(snapshot_line(cfg))
