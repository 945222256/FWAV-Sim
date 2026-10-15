"""telemetry_bridge — 遥测与示波器外发桥
=========================================
2026-09-30 T6.3 拆分：_send_telemetry / _send_joint_scope / _send_ctrl_scope
自 main_sim.py 机械迁出，逻辑、数值、默认值、语句顺序零改动。

命名空间适配（唯一的非逐字改动）：
  1. _send_joint_scope 原经 main_sim 模块全局 _WING_TGT_CACHE 读取
     _drive_wing_joints 的最近目标缓存；迁出后改经 _G（main_sim 宿主
     命名空间字典）读取同一变量，运行时行为不变（原单行条件表达式
     拆为 get + None 判断，语义含空列表情形完全等价）。
  2. 计数器 _DRIVE_ERR_N（仅 _send_ctrl_scope 的异常计数使用）随迁为
     本模块全局，初值与原 main_sim 模块级定义一致（0）。
"""

import os
import sys
import logging

import numpy as np

from config.sim_config import LOG_DIR

logger = logging.getLogger("main_sim")

# 宿主命名空间（main_sim 以 __main__ 运行或被 import 时的模块字典）：
# _WING_TGT_CACHE 等共享缓存住在该命名空间
_G = (sys.modules.get("main_sim") or sys.modules["__main__"]).__dict__

_DRIVE_ERR_N = 0


def _send_ctrl_scope(wk, sender, man_ch, tgt_cache, art, t_sim):
    """手动行程发送：{t, ch(四通道), out(频率/尾舵)} → 127.0.0.1:CTRL_SCOPE_PORT。"""
    global _WING_TGT_CACHE
    try:
        names = list(art.joint_names or [])
        out = dict(zip(names, tgt_cache or []))
        sender.send({
            "t": float(t_sim),
            "ch": man_ch or {"throttle": 0.0, "elevator": 0.0,
                             "aileron": 0.0, "rudder": 0.0},
            "freq": float(wk.frequency) if wk is not None else 0.0,
            "tail_L": out.get("tail_L", 0.0),
            "tail_R": out.get("tail_R", 0.0),
            "tail_yaw": out.get("tail_yaw", 0.0),
        })
    except Exception as e:
        global _DRIVE_ERR_N
        _DRIVE_ERR_N += 1
        if _DRIVE_ERR_N <= 3 or _DRIVE_ERR_N % 300 == 0:
            import traceback
            with open(os.path.join(LOG_DIR, "drive_diag.txt"), "a", encoding="utf-8") as f:
                f.write(f"EXC#{_DRIVE_ERR_N}: {e}\n{traceback.format_exc()}\n")
                f.flush()


def _send_joint_scope(art, sender, t_sim):
    """关节波形发送：{t, names, pos, tgt} → 127.0.0.1:JOINT_SCOPE_PORT（30Hz）。

    tgt 来自最近一次 _drive_wing_joints 的目标（内存缓存）；pos 读 articulation
    实际关节角。示波器端：tools/joint_scope.py。
    """
    try:
        names = list(art.joint_names or [])
        if not names:
            return
        pos = art.get_joint_positions()
        pos = [float(x) for x in np.asarray(pos).flatten()]
        tgt = _G.get("_WING_TGT_CACHE")
        if tgt is None:
            tgt = [0.0] * len(names)
        sender.send({
            "t": float(t_sim),
            "names": names,
            "pos": pos[:len(names)],
            "tgt": tgt[:len(names)],
        })
    except Exception as e:
        logger.debug("关节波形发送异常: %s", e)


def _send_telemetry(loader, sender, t_sim=None):
    """从 loader 读取位姿/速度，通过 UDP 发给控制器进程（JSON）。"""
    if loader is None or loader.rigid_prim is None:
        return
    try:
        pose = loader.get_pose()
        vel = loader.get_velocity()
        if pose is None or vel is None:
            return
        # JSON 格式（usv_network.UDPClient.send 接受 dict）
        sender.send({
            "pose": [float(x) for x in pose],     # [x,y,z,qw,qx,qy,qz]
            "vel":  [float(x) for x in vel],      # [vx,vy,vz,wx,wy,wz]
            "t":    float(t_sim if t_sim is not None
                          else loader.world.current_time),
        })
    except Exception as e:
        logger.debug("遥测发送异常: %s", e)
