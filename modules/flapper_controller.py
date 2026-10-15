# -*- coding: utf-8 -*-
"""
flapper_controller.py — 最小循迹控制器（占位刚体阶段）
======================================================

输入：桥接发来的 pos_cmd 参考轨迹（bishop MINCO 采样，30Hz，含位置/速度/加速度/偏航）
      + 本机当前位姿（[x,y,z,qw,qx,qy,qz]）与速度（[vx,vy,vz,wx,wy,wz]）
输出：占位刚体速度指令（v_des/w_des），由 main_sim 直写 PhysX（运动学跟踪）

控制律（刻意最小，气动/姿态环留待 aero_solver 之后）：
  v_des = clip( kp * (p_ref - p) + v_ff , vmax )      # 位置 P 环 + 速度前馈
  w_z   = clip( kp_yaw * wrap(yaw_ref - yaw) , ±wmax ) # 偏航 P 环

文献蓝本：Sci China 2023 分阶段控制（必读3）/ SparrowHawk V.B 双环（必读1）。
最小版只保留"位置环+速度前馈"，TECS 高度环与串级 PID 姿态环在 aero_solver 落地后接入。

误差统计：滚动窗口均值/最大值，每 stats_every 次更新落一行 CSV（logs/track_error.csv）。
"""
import csv
import math
import os
import time

import numpy as np


def _yaw_from_quat(pose) -> float:
    """pose = [x,y,z,qw,qx,qy,qz] → 偏航角（rad）。"""
    w, x, y, z = pose[3], pose[4], pose[5], pose[6]
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _wrap_angle(a: float) -> float:
    """归一化到 (-pi, pi]。"""
    return (a + math.pi) % (2.0 * math.pi) - math.pi


class TrajectoryTracker:
    """最小循迹跟踪器：参考轨迹 + 当前位姿 → 速度指令 + 误差统计。"""

    def __init__(self,
                 kp: float = 1.6,
                 vmax: float = 5.0,
                 kp_yaw: float = 2.0,
                 yaw_rate_max: float = 2.5,
                 csv_path: str = None,
                 stats_every: int = 30):
        self.kp = kp
        self.vmax = vmax
        self.kp_yaw = kp_yaw
        self.yaw_rate_max = yaw_rate_max
        self.stats_every = stats_every

        self.csv_path = csv_path
        self._csv_ready = False

        self.ref = None            # 最新参考（dict: position/velocity/yaw/t）
        self._n_updates = 0
        self._err_window = []      # 滚动误差窗（最近 stats_every*10 个）
        self._window_max = 300

    # ------------------------------------------------------------------
    def set_reference(self, cmd: dict):
        """桥接 pos_cmd JSON → 内部参考。"""
        try:
            self.ref = {
                "position": np.asarray(cmd["position"], dtype=float),
                "velocity": np.asarray(cmd.get("velocity", [0, 0, 0]), dtype=float),
                "yaw": float(cmd.get("yaw", 0.0)),
                "t": float(cmd.get("t", 0.0)),
            }
        except (KeyError, TypeError, ValueError):
            self.ref = None

    def has_reference(self) -> bool:
        return self.ref is not None

    # ------------------------------------------------------------------
    def compute(self, pose, vel) -> dict:
        """pose=[x,y,z,qw,qx,qy,qz], vel=[vx,vy,vz,wx,wy,wz] → 控制输出。"""
        if self.ref is None:
            return {"v_des": np.zeros(3), "w_des": np.zeros(3), "pos_err": 0.0}

        p = np.asarray(pose[0:3], dtype=float)
        p_ref = self.ref["position"]
        v_ff = self.ref["velocity"]

        err_vec = p_ref - p
        pos_err = float(np.linalg.norm(err_vec))

        v_des = self.kp * err_vec + v_ff
        speed = float(np.linalg.norm(v_des))
        if speed > self.vmax:
            v_des = v_des * (self.vmax / speed)

        yaw = _yaw_from_quat(pose)
        yaw_err = _wrap_angle(self.ref["yaw"] - yaw)
        w_z = float(np.clip(self.kp_yaw * yaw_err,
                            -self.yaw_rate_max, self.yaw_rate_max))

        self._record_error(pos_err, p, p_ref)

        return {
            "v_des": v_des,
            "w_des": np.array([0.0, 0.0, w_z]),
            "pos_err": pos_err,
        }

    # ------------------------------------------------------------------
    def _record_error(self, pos_err: float, p, p_ref):
        self._n_updates += 1
        self._err_window.append(pos_err)
        if len(self._err_window) > self._window_max:
            self._err_window.pop(0)

        if self._n_updates % self.stats_every != 0:
            return

        mean_e = float(np.mean(self._err_window))
        max_e = float(np.max(self._err_window))
        line = (f"[TRACK] t={time.strftime('%H:%M:%S')} "
                f"pos_err mean={mean_e:.3f} m, max={max_e:.3f} m "
                f"(win={len(self._err_window)})")
        print(line, flush=True)
        self._write_csv(mean_e, max_e, p, p_ref)

    def _write_csv(self, mean_e, max_e, p, p_ref):
        if not self.csv_path:
            return
        try:
            need_header = (not self._csv_ready) or not os.path.exists(self.csv_path)
            with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if need_header:
                    w.writerow(["wall_time", "mean_err_m", "max_err_m",
                                "px", "py", "pz", "rx", "ry", "rz"])
                    self._csv_ready = True
                w.writerow([time.strftime("%H:%M:%S"), f"{mean_e:.4f}", f"{max_e:.4f}",
                            f"{p[0]:.3f}", f"{p[1]:.3f}", f"{p[2]:.3f}",
                            f"{p_ref[0]:.3f}", f"{p_ref[1]:.3f}", f"{p_ref[2]:.3f}"])
        except Exception:
            pass
