"""flight_recorder — 九十三列试验高密度数据流记录器
=====================================================
2026-09-30 T6.3 拆分：本模块自 main_sim.py 机械迁出（_strips_ser /
_ident_stream_write / _ident_stream_close 及记录器模块级状态），逻辑、
数值、默认值、语句顺序零改动。

命名空间适配（唯一的非逐字改动）：原函数体内经 main_sim 模块 globals()
读写的跨函数缓存 _WING_TGT_CACHE / _DA_CACHE / _FOLD_T_CACHE /
_CTRL_MODE_CACHE / _WING_STATE_CACHE（写入方 _drive_wing_joints 与
main() 主循环留守 main_sim），迁出后改经 _G（即 main_sim 宿主命名空间
字典）访问同一批变量，运行时行为不变；仅本模块自读自写的
_IDENT_FLUSH_N / _DETCHECK_FH 保留 globals() 原样。
"""

import os
import sys
import math

from config.sim_config import LOG_DIR

# 宿主命名空间（main_sim 以 __main__ 运行或被 import 时的模块字典）：
# 与留守代码共享的拍动/控制缓存全部住在该命名空间
_G = (sys.modules.get("main_sim") or sys.modules["__main__"]).__dict__

# ── 试验高密度记录器（2026-09-18 新增）──────────────────────────
# 动机：HTTP 轮询 run_ident.py 在仿真高负载时有效采样率只有 ~0.5Hz，
# 拍动振荡（2.5~3.5Hz）严重混叠，舵效/扫频辨识数据失真。
# 此处在看板快照（30Hz，仿真进程内）直接把快照写入磁盘，不经过网络与代理。
_IDENT_STREAM_FH = None
_IDENT_STREAM_WRITER = None


def _strips_ser(snap):
    """逐叶素快照序列化（2026-09-28 完整记录）：snap["strips"] 的
    numpy 字段转可 JSON 化的圆整列表；缺翼记 None。"""
    sp = snap.get("strips") or {}
    out = {}
    for side in ("L", "R"):
        d = sp.get(side)
        if d is None:
            out[side] = None
            continue
        out[side] = {
            "phi": round(float(d["phi"]), 5),
            "alpha": round(float(d["alpha"]), 5),
            "r": [round(float(x), 4) for x in d["r"]],
            "aeff": [round(float(x), 4) for x in d["aeff"]],
            "dF": [[round(float(x), 4) for x in row] for row in d["dF"]]}
    return out


def _ident_stream_write(snap, man_ch):
    """EXPERIMENT_MODE 非空时把每帧快照追加到 logs/ident_stream_<mode>_<ts>.csv。

    2026-09-19 修正：每次试验写独立文件（不再复用 ident_stream.csv），
    防止残留进程争写损坏数据；每行立即 flush，进程被强制终止也不丢失已采数据。
    """
    global _IDENT_STREAM_FH, _IDENT_STREAM_WRITER
    try:
        from config.sim_config import EXPERIMENT_MODE as _em
        import config.sim_config as _cfg_s
        if not _em and not getattr(_cfg_s, "FLIGHT_STREAM_LOG", False):
            return
        import csv as _csv
        if _IDENT_STREAM_WRITER is None:
            import time as _time2
            path = os.path.join(
                LOG_DIR,
                f"ident_stream_{_em or 'flight'}_{_time2.strftime('%m%d_%H%M%S')}.csv")
            _IDENT_STREAM_FH = open(path, "w", newline="", encoding="utf-8")
            _IDENT_STREAM_WRITER = _csv.writer(_IDENT_STREAM_FH)
            # 元数据随档自动落盘（2026-09-28 必填制）：启动声明的
            # TAG/DESC 写入 sidecar，回放台即刻绿标可查；豁免运行标记
            # untagged（红标=不得用于下结论）
            try:
                import json as _json_meta
                with open(path + ".meta.json", "w", encoding="utf-8") as _mf:
                    _json_meta.dump({
                        "tags": os.environ.get("FLAPPER_TAG", "").strip(),
                        "description": os.environ.get("FLAPPER_DESC", "").strip()
                        + ("（豁免运行：未声明标注）"
                           if os.environ.get("FLAPPER_ALLOW_NO_TAG") else ""),
                        "updated": _time2.strftime("%Y-%m-%d %H:%M:%S"),
                    }, _mf, ensure_ascii=False, indent=1)
            except OSError:
                pass
            # 升力两列语义声明（事项 I-030 机器侧）：Fx/Fy/Fz 三列记录气动回调
            # 单次触发的施加值，本配置下回调每物理步触发两次，故 Fz 为每步
            # 合计量的二分之一；Frx/Fry/Frz 三列记录每步合计。判据与评估工具
            # 统一取每步合计口径，即取 Frz 或二倍 Fz，实测两列之比全窗中位数
            # 为二点零零零零，与任务二垂直动量账归因文档互证。
            _IDENT_STREAM_WRITER.writerow(
                ["t", "phase", "freq", "dA", "Fx", "Fy", "Fz",
                 "Mx", "My", "Mz", "TMx", "TMy", "TMz",
                 "z", "roll", "pitch", "wx", "wy", "wz", "vx", "vy",
                 "qw", "qz", "tension",
                 # 全量记录（2026-09-19）：刚体补全/自动驾驶仪/气动内部/关节
                 "x", "y", "vz", "qx", "qy",
                 "elev", "ail", "rud", "pitch_des", "roll_des", "vxd", "veff",
                 "vairx", "vairy", "vairz", "vslip",
                 "Fwx", "Fwy", "Fwz", "Twx", "Twy", "Twz",
                 "Ftx", "Fty", "Ftz", "pin",
                 "flapLt", "flapRt", "twistLt", "twistRt",
                 "flapLa", "flapRa", "twistLa", "twistRa",
                 "effFlapL", "effFlapR", "effTwistL", "effTwistR",
                 "Frx", "Fry", "Frz", "Trx", "Try", "Trz", "clip",
                 "aLmean", "aLmax", "aLs45", "aRmean", "aRmax", "aRs45",
                 "foldT", "foldLa", "foldRa", "mode",
                 "ctlA", "tailA", "tvfx", "tvfz", "vphz",
                 # 回放档案列（2026-09-28 追加；旧记录无此列时回放端取默认）
                 "freqL", "freqR", "gateL", "gateR", "dlag",
                 # 全关节位置快照（JSON 字符串：覆盖十五自由度，含未
                 # 驱动的扫掠/腕/颈；回放与档案用途，逐行随拍）
                 "joints_json",
                 # 完整记录列（2026-09-28 用户令）：逐叶素快照（每翼
                 # 十四站行程位姿+站上力，瞬时子样口径）、全关节驱动力
                 # 矩、关节约束力（探测可用接口，展平对齐 joints_json）
                 "strips_json", "jeff_json", "jforce_json"])
            _IDENT_STREAM_FH.flush()
            try:
                with open(os.path.join(LOG_DIR, "ident_latest.txt"), "w",
                          encoding="utf-8") as _lf:
                    _lf.write(os.path.basename(path))
            except OSError:
                pass
            print(f"[IDENT] 高密度记录 → {path} (30Hz)", flush=True)
        phase = ""
        try:
            import json as _json
            with open(os.path.join(LOG_DIR, "experiment_phase.json"),
                      encoding="utf-8") as _pf:
                phase = _json.load(_pf).get("phase", "")
        except (OSError, ValueError):
            pass
        F = snap.get("F") or [0.0, 0.0, 0.0]
        Tt = snap.get("Tt") or [0.0, 0.0, 0.0]
        T = snap.get("T") or [0.0, 0.0, 0.0]
        pose = snap.get("pose") or [0.0] * 7
        vel = snap.get("vel") or [0.0] * 6
        ap = snap.get("ap") or {}
        vair = snap.get("vair") or [0.0, 0.0, 0.0]
        vslip = snap.get("vslip", 0.0)
        Fw = snap.get("Fw") or [0.0, 0.0, 0.0]
        Tw = snap.get("Tw") or [0.0, 0.0, 0.0]
        Ftl = snap.get("Ft") or [0.0, 0.0, 0.0]
        jt, jp = {}, {}
        _jef = snap.get("jeff") or {}
        _Fraw = snap.get("Fraw") or [0.0, 0.0, 0.0]
        _Traw = snap.get("Traw") or [0.0, 0.0, 0.0]
        _aL = snap.get("aeffL") or [0.0, 0.0, 0.0]
        _aR = snap.get("aeffR") or [0.0, 0.0, 0.0]
        _J = snap.get("joints")
        if _J:
            _jn = list(_J.get("names") or [])
            jp = dict(zip(_jn, _J.get("pos") or []))
            _tv = _G.get("_WING_TGT_CACHE") or []
            if len(_tv) == len(_jn):
                jt = dict(zip(_jn, _tv))
        _IDENT_STREAM_WRITER.writerow([
            round(snap.get("t", 0.0), 4), phase, round(snap.get("freq", 0.0), 4),
            round(_G.get("_DA_CACHE", 0.0), 4),
            round(F[0], 4), round(F[1], 4), round(F[2], 4),
            round(Tt[0], 4), round(Tt[1], 4), round(Tt[2], 4),
            round(T[0], 4), round(T[1], 4), round(T[2], 4),
            # 姿态三列（2026-09-29 转弯线修复）：roll/pitch 列自档案创建
            # 起一直错装四元数 qx/qy 分量（pose[4]/pose[5]），qx 列错装
            # 真 qy（pose[5]）、qy 列恒零丢弃真 qx——历史全部用这些列做
            # 欧拉换算的判读被污染（转弯线 R13 探针对账实锤，真四元数
            # =(qw列,roll列,pitch列,qz列) 可重建历史姿态）。本修复后
            # roll/pitch 列写欧拉角、qx/qy 列写真分量；历史记录按上述
            # 重建口径解读，回放台同步适配。
            round(pose[2], 4),
            round(math.atan2(2 * (pose[3] * pose[4] + pose[5] * pose[6]),
                             1 - 2 * (pose[4] ** 2 + pose[5] ** 2)), 5),
            round(math.asin(max(-1.0, min(1.0,
                2 * (pose[3] * pose[5] - pose[6] * pose[4])))), 5),
            round(vel[3], 4), round(vel[4], 4), round(vel[5], 4),
            round(vel[0], 4), round(vel[1], 4),
            round(pose[3], 5), round(pose[6], 5),
            round((snap.get("tether") or {}).get("tension", 0.0), 3),
            # ── 全量记录追加列 ──
            round(pose[0], 4), round(pose[1], 4), round(vel[2], 4),
            round(pose[4], 5) if len(pose) > 4 else 0.0,
            round(pose[5], 5) if len(pose) > 5 else 0.0,
            round(ap.get("elevator", 0.0), 4), round(ap.get("aileron", 0.0), 4),
            round(ap.get("rudder", 0.0), 4), round(ap.get("pitch_des", 0.0), 4),
            round(ap.get("roll_des", 0.0), 4), round(ap.get("vxd", 0.0), 4),
            round(ap.get("v_eff", 0.0), 4),
            round(vair[0], 4), round(vair[1], 4), round(vair[2], 4),
            round(vslip, 4),
            round(Fw[0], 4), round(Fw[1], 4), round(Fw[2], 4),
            round(Tw[0], 4), round(Tw[1], 4), round(Tw[2], 4),
            round(Ftl[0], 4), round(Ftl[1], 4), round(Ftl[2], 4),
            int(snap.get("pin", 0)),
            round(jt.get("flap_L", 0.0), 4), round(jt.get("flap_R", 0.0), 4),
            round(jt.get("twist_L", 0.0), 4), round(jt.get("twist_R", 0.0), 4),
            round(jp.get("flap_L", 0.0), 4), round(jp.get("flap_R", 0.0), 4),
            round(jp.get("twist_L", 0.0), 4), round(jp.get("twist_R", 0.0), 4),
            round(_jef.get("flap_L", 0.0), 4), round(_jef.get("flap_R", 0.0), 4),
            round(_jef.get("twist_L", 0.0), 4), round(_jef.get("twist_R", 0.0), 4),
            round(_Fraw[0], 3), round(_Fraw[1], 3), round(_Fraw[2], 3),
            round(_Traw[0], 3), round(_Traw[1], 3), round(_Traw[2], 3),
            int(snap.get("clip", 0)),
            round(_aL[0], 4), round(_aL[1], 4), round(_aL[2], 4),
            round(_aR[0], 4), round(_aR[1], 4), round(_aR[2], 4),
            round(_G.get("_FOLD_T_CACHE", 0.0), 4),
            round(jp.get("fold_L", 0.0), 4), round(jp.get("fold_R", 0.0), 4),
            int(_G.get("_CTRL_MODE_CACHE", -1)),
            round(ap.get("alpha_ff", 0.0), 5), round(snap.get("tailA", 0.0), 5),
            round(snap.get("tvfx", 0.0), 4), round(snap.get("tvfz", 0.0), 4),
            round((snap.get("velph") or [0.0, 0.0, 0.0])[2], 4),
            # 回放档案列（2026-09-28）：每翼频率/闸门/滞后差动（缓存
            # 缺失时以 NaN/1/0 落盘）+ 全关节位置 JSON 快照
            round((_G.get("_WING_STATE_CACHE") or [float("nan")] * 5)[0], 4),
            round((_G.get("_WING_STATE_CACHE") or [float("nan")] * 5)[1], 4),
            round((_G.get("_WING_STATE_CACHE") or [1.0] * 5)[2], 4),
            round((_G.get("_WING_STATE_CACHE") or [1.0] * 5)[3], 4),
            round((_G.get("_WING_STATE_CACHE") or [0.0] * 5)[4], 4),
            __import__("json").dumps(
                {k: round(float(v), 5) for k, v in (jp or {}).items()},
                ensure_ascii=False),
            # 完整记录列（2026-09-28）：逐叶素快照（r/aeff/dF 各十四站，
            # dF 体轴系三元组）+ 全关节驱动力矩 + 关节约束力
            __import__("json").dumps(_strips_ser(snap), ensure_ascii=False),
            __import__("json").dumps(
                {k: round(float(v), 4) for k, v in (_jef or {}).items()},
                ensure_ascii=False),
            __import__("json").dumps(
                [round(float(v), 4) for v in (snap.get("jforce") or [])],
                ensure_ascii=False)])
        # 每 20 行写入磁盘一次（每行都写入磁盘会把循环拖慢到 60Hz——第10轮实测；
        # 0.3s 粒度仍满足防坠安全）
        globals()["_IDENT_FLUSH_N"] = globals().get("_IDENT_FLUSH_N", 0) + 1
        if _IDENT_STREAM_FH and globals()["_IDENT_FLUSH_N"] % 20 == 0:
            _IDENT_STREAM_FH.flush()
        # 高精度分岔侧档（2026-09-28 仿真器判决）：主数据流圆整到
        # 4~5 位小数看不见更小的差；FLAPPER_DETCHECK=1 时把位姿以
        # float64 十六进制原值逐行写入独立档，供对照对定位首个分岔
        # 步与差值量级（1e-15 级=浮点执行序；1e-3 级一步跳入=离散
        # 事件）。关闭时不产生任何文件与行为变化。
        if os.environ.get("FLAPPER_DETCHECK") == "1":
            try:
                if globals().get("_DETCHECK_FH") is None:
                    import time as _t3
                    _p = os.path.join(LOG_DIR, "detcheck_%s.csv"
                                      % _t3.strftime("%m%d_%H%M%S"))
                    globals()["_DETCHECK_FH"] = open(_p, "w",
                                                     encoding="utf-8")
                    _DETCHECK_FH.write("t,xh,yh,zh,qwh,qxh,qyh,qzh\n")
                    print(f"[DETCHECK] 高精度侧档 → {_p}", flush=True)
                _ps = snap.get("pose") or [0.0] * 7
                _hx = [float(v).hex() for v in _ps]
                _DETCHECK_FH.write("%.6f,%s\n" % (snap.get("t", 0.0),
                                                   ",".join(_hx)))
            except Exception:
                pass
    except Exception:
        pass


def _ident_stream_close():
    """关闭试验数据流文件句柄（试验完成自动退出时调用）。"""
    global _IDENT_STREAM_FH, _IDENT_STREAM_WRITER
    try:
        if _IDENT_STREAM_FH is not None:
            _IDENT_STREAM_FH.flush()
            _IDENT_STREAM_FH.close()
            print("[IDENT] 数据流已收尾关闭", flush=True)
    except Exception:
        pass
    _IDENT_STREAM_FH = None
    _IDENT_STREAM_WRITER = None
