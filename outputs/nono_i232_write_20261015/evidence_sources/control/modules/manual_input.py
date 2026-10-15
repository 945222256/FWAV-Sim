# -*- coding: utf-8 -*-
"""
manual_input.py — 手动输入：Windows 摇杆（winmm）优先，键盘兜底
================================================================
四通道输出 [-1,1]：throttle / elevator / aileron / rudder
  摇杆：左杆Y=油门 左杆X=方向 右杆Y=升降 右杆X=副翼（带死区）
  键盘：W/S 油门（保持），↑↓ 升降，←→ 副翼，Q/E 方向（松开回中）
"""
import ctypes
import time

import config.sim_config as cfg


class _JOYINFOEX(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint), ("dwFlags", ctypes.c_uint),
                ("dwXpos", ctypes.c_uint), ("dwYpos", ctypes.c_uint),
                ("dwZpos", ctypes.c_uint), ("dwRpos", ctypes.c_uint),
                ("dwUpos", ctypes.c_uint), ("dwVpos", ctypes.c_uint),
                ("dwButtons", ctypes.c_uint), ("dwButtonNumber", ctypes.c_uint),
                ("dwPOV", ctypes.c_uint), ("dwReserved1", ctypes.c_uint),
                ("dwReserved2", ctypes.c_uint)]


_KEY = {"W": 0x57, "S": 0x53, "Q": 0x51, "E": 0x45,
        "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27}


def _pressed(vk):
    return ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000


class ManualInput:
    def __init__(self, deadzone=0.08):
        self.deadzone = deadzone
        self.source = "none"
        self._joy = None
        try:
            info = _JOYINFOEX(dwSize=ctypes.sizeof(_JOYINFOEX), dwFlags=0x1FF)
            if ctypes.windll.winmm.joyGetPosEx(0, ctypes.byref(info)) == 0:
                self._joy = info
                self.source = "joystick"
        except Exception:
            pass
        if self._joy is None:
            self.source = "keyboard"
        self.throttle = 0.0      # 键盘油门居中起步（≈2.5Hz 悬停工况，避免开局自由落体）
        self._spring = {"elevator": 0.0, "aileron": 0.0, "rudder": 0.0}
        self._t_last = time.time()

    def _axis(self, raw):
        v = (raw - 32768) / 32768.0
        if abs(v) < self.deadzone:
            return 0.0
        return v

    def _read_joystick(self):
        rc = ctypes.windll.winmm.joyGetPosEx(0, ctypes.byref(self._joy))
        if rc != 0:
            return None
        j = self._joy
        return {"throttle": -self._axis(j.dwYpos),   # 前推=+
                "rudder": self._axis(j.dwXpos),
                "elevator": -self._axis(j.dwRpos),
                "aileron": self._axis(j.dwZpos)}

    def _read_keyboard(self):
        dt = min(time.time() - self._t_last, 0.1)
        self._t_last = time.time()
        r = cfg.MANUAL_KEY_RATE * dt

        if _pressed(_KEY["W"]):
            self.throttle = min(1.0, self.throttle + r)
        if _pressed(_KEY["S"]):
            self.throttle = max(-1.0, self.throttle - r)
        # 油门保持；其余通道松开回中
        for ch, up_key, dn_key in (
                ("elevator", "UP", "DOWN"), ("aileron", "RIGHT", "LEFT"),
                ("rudder", "Q", "E")):
            if _pressed(_KEY[up_key]):
                self._spring[ch] = min(1.0, self._spring[ch] + r)
            elif _pressed(_KEY[dn_key]):
                self._spring[ch] = max(-1.0, self._spring[ch] - r)
            else:
                self._spring[ch] *= max(0.0, 1.0 - 6.0 * dt)
        return {"throttle": self.throttle,
                "elevator": self._spring["elevator"],
                "aileron": self._spring["aileron"],
                "rudder": self._spring["rudder"]}

    def read(self):
        """返回 {throttle, elevator, aileron, rudder}，[-1,1]。摇杆拔掉自动切键盘。"""
        if self.source == "joystick":
            ch = self._read_joystick()
            if ch is not None:
                return ch
            self.source = "keyboard"
        return self._read_keyboard()
