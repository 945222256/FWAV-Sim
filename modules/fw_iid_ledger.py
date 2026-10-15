"""FW-IID 冲量台账记录器与逐位比较器（扑翼冲量积分差分测量方案阶段零）。

方案来源：《扑翼冲量积分差分测量方案规划》（2026-09-29）第六节阶段零
交付物与第五节测量原理。本模块提供三部分：

其一，LedgerRecorder：主循环每物理步求解后记录全通道位型（双精度浮点
原值，二进制落盘）。文件布局：魔数八字节 + 无符号三十二位整数头长 +
头 JSON + 逐步记录体（每步一个双精度向量：第零元素为步号，其后按
CHAN_LAYOUT 顺序排列五十五个通道元素）+ 尾标记与尾部 JSON（步数与
缺失统计）。

其二，compare 命令行：对两份台账做完整性资格检查后逐位比较。完整一致
的判定要求两份资格有效、步数相同且体部逐位相同；长度不同、空记录、
两份共同截短一律不得判完整一致（事项 I-184 修正：旧版截断到公共段，
长度不等但公共段相同时误返逐位相同）。退出码：零为完整一致，一为不
一致或不得判完整一致（附原因），二为档案或格式错误（含资格无效），
三为资格依据缺失待核。

其三，qualify 命令行与函数（事项 I-184）：按采集清单做完整性资格检查，
覆盖预期步数、必需通道、步号连续与重复、采样间隔、非有限数、通道布局、
运行条件与配方声明的事件规则；结果为 I-183 规范状态对象
（doro.experiment_contract/1.0：status、reason、source_tool、
source_version、generated_at、input_refs，data_qualification 枚举）。
旧档缺完整性依据（无尾部统计、无运行条件记录等）返回 unknown 待核，
不作 invalid。饱和、触地等事件仅按清单声明的规则报告，只有违反本次
适用条件（规则带 disqualify_on）才判不合格。

纪律：默认零行为（main_sim 仅在 FLAPPER_LEDGER 等于一时的挂入点不变）；
记录器内部一切异常自吞并限次打印，绝不打断仿真；比较器只读档案不改
档案；规范输出中非有限读数写 null 并附原因，原始文件不改。
"""

import datetime
import hashlib
import json
import os
import struct
import sys
import time

import numpy as np

# 通道布局：名称与元素数。顺序即台账列顺序，改动布局必须同步升格
# fmt_version 并在比较器做版本拦截。
CHAN_LAYOUT = [
    ("bp", 7),    # 机体位姿 [x,y,z,qw,qx,qy,qz]
    ("bv", 6),    # 机体速度 [vx,vy,vz,wx,wy,wz]
    ("jp", 9),    # 九关节位置
    ("jv", 9),    # 九关节速度
    ("je", 9),    # 九关节实测力矩
    ("tgt", 9),   # 九关节位置目标（本物理步求解所用，上帧写入）
    ("Fa", 3),    # 气动施加力（世界系）
    ("Ta", 3),    # 气动施加力矩（世界系）
]
MAGIC = b"FWIIDLG1"
FMT_VERSION = 1
CHECK_VERSION = 2          # I-184 返修版资格检查器（独立于档案格式版本）
TAIL_MARK = b"#TAIL#"

# 固定采集清单默认值（I-184 返修版）。必需通道的值为允许整通道缺失
# 步数上限，默认全部为零——生产记录器首物理步目标未写的既成行为不构成
# 预先批准的例外；首步窗口等例外须由清单 approved_misses 显式批准（含
# 允许步位），且档案须有发生位置证据（记录器自返修起在尾部写入缺失与
# 欠长的逐步位置；旧档只有累计数、不能定位时按待核处理）。
DEFAULT_MANIFEST = {
    "step_start": 1,
    "required_channels": {name: 0 for name, _ in CHAN_LAYOUT},
}


def _chan_slices():
    """返回通道名到（起始列，长度）的映射，列号含步号列偏移一。"""
    out = {}
    i = 1
    for name, n in CHAN_LAYOUT:
        out[name] = (i, n)
        i += n
    return out


class LedgerRecorder:
    """冲量台账记录器：每物理步一次 record，缓冲落盘，close 收尾。"""

    def __init__(self, path, joint_names, phys_dt, meta=None):
        self.path = str(path)
        self.joint_names = [str(x) for x in (joint_names or [])]
        self.phys_dt = float(phys_dt)
        self.meta = dict(meta or {})
        self.n_floats = sum(n for _, n in CHAN_LAYOUT)
        self.n_steps = 0
        self.miss_counts = {name: 0 for name, _ in CHAN_LAYOUT}
        # I-184 返修：欠长补零与整通道缺失的逐步位置证据（写入尾部，
        # 供资格检查按预先批准规则核对发生位置；旧档无这些键时按待核）。
        self.short_counts = {name: 0 for name, _ in CHAN_LAYOUT}
        self.miss_positions = {name: [] for name, _ in CHAN_LAYOUT}
        self.short_positions = {name: [] for name, _ in CHAN_LAYOUT}
        header = {
            "fmt_version": FMT_VERSION,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "phys_dt": self.phys_dt,
            "channels": [[n, c] for n, c in CHAN_LAYOUT],
            "joint_names": self.joint_names,
            "joint_channels": ["jp", "jv", "je", "tgt"],
        }
        header.update(self.meta)
        self._header_json = json.dumps(header, ensure_ascii=False).encode("utf-8")
        self._fh = open(self.path, "wb")
        self._fh.write(MAGIC)
        self._fh.write(struct.pack("<I", len(self._header_json)))
        self._fh.write(self._header_json)
        self._fh.flush()
        self._buf = []
        self._miss_warned = set()
        self._closed = False

    def record(self, step, fields):
        """记录一个物理步。fields 为通道名到数值序列的映射：整通道缺失
        以零填充并在 miss_counts 计数且记 miss_positions；序列欠长以零
        补齐并在 short_counts 计数且记 short_positions（I-184 返修：补零
        不是实测零，缺量须留位置证据）；任何异常只丢本行不打断仿真。"""
        try:
            vec = np.zeros(1 + self.n_floats, dtype="<f8")
            vec[0] = float(step)
            for name, n in CHAN_LAYOUT:
                i0 = None
                for cname, (cs, cn) in _chan_slices().items():
                    if cname == name:
                        i0, n0 = cs, cn
                        break
                vals = fields.get(name)
                if vals is None:
                    self.miss_counts[name] += 1
                    self.miss_positions[name].append(int(step))
                    if name not in self._miss_warned:
                        self._miss_warned.add(name)
                        print(f"[FW-LEDGER] 通道 {name} 缺失（零填充）",
                              flush=True)
                    continue
                arr = np.asarray(vals, dtype=float).reshape(-1)
                k = min(len(arr), n)
                vec[i0:i0 + k] = arr[:k]
                if len(arr) < n:
                    self.short_counts[name] += 1
                    self.short_positions[name].append(int(step))
                    if name not in self._miss_warned:
                        self._miss_warned.add(name)
                        print(f"[FW-LEDGER] 通道 {name} 欠长（补零至 "
                              f"{n} 元素，实给 {len(arr)}）", flush=True)
            self._buf.append(vec)
            self.n_steps += 1
            if len(self._buf) >= 240:
                self._flush_buf()
        except Exception as exc:  # 记录失败绝不打断仿真
            try:
                print(f"[FW-LEDGER] record 异常丢行: {exc!r}", flush=True)
            except Exception:
                pass

    def _flush_buf(self):
        if not self._buf:
            return
        block = np.stack(self._buf)
        self._fh.write(block.astype("<f8").tobytes())
        self._fh.flush()
        self._buf = []

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._flush_buf()
            # 尾部补记步数与缺失统计（I-184 返修：另含欠长计数与两类
            # 缺失的逐步位置证据；旧读端忽略未知键，格式版本不变）
            tail = json.dumps({
                "n_steps": self.n_steps,
                "miss_counts": self.miss_counts,
                "short_counts": self.short_counts,
                "miss_positions": self.miss_positions,
                "short_positions": self.short_positions,
            }, ensure_ascii=False).encode("utf-8")
            self._fh.write(b"#TAIL#" + tail)
            self._fh.close()
            print(f"[FW-LEDGER] 台账收尾：{self.n_steps} 步 → {self.path}",
                  flush=True)
        except Exception as exc:
            try:
                print(f"[FW-LEDGER] close 异常: {exc!r}", flush=True)
            except Exception:
                pass
        # I-210 仅观察侧档：二进制收尾之后调用，默认关闭不导入、不查对象。
        if os.environ.get("FLAPPER_I210_EVIDENCE") == "1":
            try:
                from modules import i210_evidence
                self._i210_evidence_close_result = i210_evidence.close(self)
                if self._i210_evidence_close_result["status"] in ("error", "unavailable"):
                    print("[I210-EVIDENCE] close 未形成完整侧档: %s" %
                          self._i210_evidence_close_result, flush=True)
            except Exception as exc:
                try:
                    print(f"[I210-EVIDENCE] close 挂点异常: {exc!r}", flush=True)
                except Exception:
                    pass


def load(path):
    """读台账：返回 (header, 数组[步, 1+n_floats])。尾部标记行被剥离。"""
    with open(path, "rb") as f:
        body = f.read()
    if body[:8] != MAGIC:
        raise ValueError(f"非 FW-IID 台账文件（魔数不符）: {path}")
    (hlen,) = struct.unpack("<I", body[8:12])
    header = json.loads(body[12:12 + hlen].decode("utf-8"))
    if header.get("fmt_version") != FMT_VERSION:
        raise ValueError(f"台账版本不符: {header.get('fmt_version')}")
    rest = body[12 + hlen:]
    tail_at = rest.find(b"#TAIL#")
    if tail_at >= 0:
        rest = rest[:tail_at]
    n_floats = sum(n for _, n in header["channels"])
    per = 1 + n_floats
    if len(rest) % (per * 8) != 0:
        raise ValueError(f"台账体长度不对齐: {len(rest)} 字节")
    arr = np.frombuffer(rest, dtype="<f8").reshape(-1, per).copy()
    return header, arr


def _bits32(a, b):
    """三十二位等效位差：把两双精度值圆整到三十二位浮点后按位型整数
    取差绝对值（引擎张量原生为三十二位，该口径即注入口径）。"""
    import struct as _s

    def b32(x):
        return _s.unpack("<I", _s.pack("<f", float(x)))[0]
    return abs(b32(a) - b32(b))


def _now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _null_if_nonfinite(x):
    """I-183 规范：非有限读数在规范输出中写 null，原始文件不改。"""
    try:
        v = float(x)
    except Exception:
        return None
    return v if np.isfinite(v) else None


def _anatomy(path):
    """读档案原始字节，返回体部与尾部的完整解剖信息。

    返回 (raw, header, body_start, body_end, tail, per)：tail 为尾部
    JSON 字典或 None（无尾部标记）；per 为每步元素数（步号加通道）。
    魔数、版本或体部对齐不符时抛 ValueError。
    """
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:8] != MAGIC:
        raise ValueError(f"非 FW-IID 台账文件（魔数不符）: {path}")
    (hlen,) = struct.unpack("<I", raw[8:12])
    header = json.loads(raw[12:12 + hlen].decode("utf-8"))
    if header.get("fmt_version") != FMT_VERSION:
        raise ValueError(f"台账版本不符: {header.get('fmt_version')}")
    body_start = 12 + hlen
    tail_at = raw.find(TAIL_MARK, body_start)
    body_end = len(raw) if tail_at < 0 else tail_at
    tail = None
    if tail_at >= 0:
        tail = json.loads(raw[tail_at + len(TAIL_MARK):].decode("utf-8"))
    n_floats = sum(n for _, n in header["channels"])
    per = 1 + n_floats
    if (body_end - body_start) % (per * 8) != 0:
        raise ValueError(f"台账体长度不对齐: {body_end - body_start} 字节")
    return raw, header, body_start, body_end, tail, per


def _input_refs(path, raw, body_start, body_end, tail):
    """来源与范围哈希（I-184：输入输出来源与范围哈希留存）。"""
    return {
        "path": os.path.abspath(path),
        "size_bytes": len(raw),
        "sha256_full": hashlib.sha256(raw).hexdigest(),
        "body_byte_range": [body_start, body_end],
        "sha256_body": hashlib.sha256(raw[body_start:body_end]).hexdigest(),
        "tail_present": tail is not None,
    }


def qualify(path, manifest=None, json_out=None):
    """完整性资格检查（事项 I-184 返修版）。返回 I-183 规范状态对象。

    manifest（采集清单）可选键：expected_steps（采集前固定的预期步数，
    完整范围的唯一依据——不得用实际行数或尾部自报数反推）、phys_dt 与
    phys_dt_tol（采样间隔及容差）、step_start（预期首步号）、
    required_channels（通道名到允许缺失步数上限，默认全零）、
    approved_misses（预先批准的例外：通道名到 {"max": 步数上限,
    "at_steps": [允许步号]}，须有档案内发生位置证据方可用）、
    run_conditions（头 env 键到期望值）、event_rules（配方事件规则列表，
    每条含 name、channel、component、op、threshold、after_step、
    disqualify_on；仅 disqualify_on 的规则违反才判不合格，其余只报告）。
    缺完整性依据（无预期步数、无尾部统计、计数键缺失、旧档只有累计数
    不能定位缺失位置、头缺运行条件记录）一律返回 unknown 待核，不放行
    完整一致。饱和、触地等事件不作为无条件不合格依据。结果保存运行
    身份（run_id、tag、desc）与预期实际范围。
    """
    man = dict(DEFAULT_MANIFEST)
    if manifest:
        man.update(manifest)
    result = {
        "source_tool": "fw_iid_ledger.qualify",
        "source_version": "check%d/fmt%d" % (CHECK_VERSION, FMT_VERSION),
        "generated_at": _now_iso(),
        "manifest_applied": {k: man[k] for k in man
                             if manifest and k in manifest},
    }
    notes = []
    try:
        raw, header, body_start, body_end, tail, per = _anatomy(path)
    except Exception as exc:
        try:
            with open(path, "rb") as f:
                raw = f.read()
            refs = {"path": os.path.abspath(path), "size_bytes": len(raw),
                    "sha256_full": hashlib.sha256(raw).hexdigest(),
                    "body_byte_range": None, "sha256_body": None,
                    "tail_present": None}
        except Exception:
            refs = {"path": os.path.abspath(path), "size_bytes": None,
                    "sha256_full": None, "body_byte_range": None,
                    "sha256_body": None, "tail_present": None}
        result.update({"status": "invalid",
                       "reason": f"档案读取失败: {exc}",
                       "input_refs": refs, "checks": [], "notes": notes})
        if json_out:
            with open(json_out, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=1)
        return result

    arr = np.frombuffer(raw[body_start:body_end], dtype="<f8") \
        .reshape(-1, per).copy()
    n_rows = len(arr)
    result["input_refs"] = _input_refs(path, raw, body_start, body_end, tail)
    result["steps"] = n_rows
    result["phys_dt"] = header.get("phys_dt")
    result["tag"] = header.get("tag")
    # I-184 返修：保存档案内已有运行身份与预期实际范围（不能仅靠同名
    # 标签或哈希替代运行标识）
    result["run_identity"] = {
        "run_id": header.get("run_id"),
        "tag": header.get("tag"),
        "desc": header.get("desc"),
    }
    checks, fails, unknowns = [], [], []

    def add(name, status, detail):
        checks.append({"check": name, "status": status, "detail": detail})
        if status == "fail":
            fails.append(f"{name}: {detail}")
        elif status == "unknown":
            unknowns.append(f"{name}: {detail}")

    # 一 布局：头内通道表须与现行 CHAN_LAYOUT 一致
    want = [[n, c] for n, c in CHAN_LAYOUT]
    add("channel_layout", "pass" if header.get("channels") == want else "fail",
        "一致" if header.get("channels") == want
        else f"布局不符: 档案 {header.get('channels')} 对现行 {want}")

    # 二 尾部统计与体部行数互证（无尾部即缺完整性依据）
    if tail is None:
        add("tail_stats", "unknown", "缺尾部步数统计，完整性依据缺失（待核）")
        miss_counts = None
    else:
        t_n = tail.get("n_steps")
        if t_n != n_rows:
            add("tail_stats", "fail",
                f"尾部步数不符: 体部 {n_rows} 行对尾部 {t_n}")
        else:
            add("tail_stats", "pass", f"尾部步数 {t_n} 与体部一致")
        miss_counts = tail.get("miss_counts")

    # 三 空记录
    if n_rows == 0:
        add("nonempty", "fail", "空记录（零步）")
    else:
        add("nonempty", "pass", f"{n_rows} 步")

    # 四 步号：整数、起始、唯一、严格顺序四项同查（I-184 返修：非相邻
    # 重复与负差倒退一并拒绝）
    if n_rows > 0:
        scol = arr[:, 0]
        if not np.all(scol == np.round(scol)):
            bad = int(np.flatnonzero(scol != np.round(scol))[0])
            add("step_numbers", "fail", f"步号非整数（首见行 {bad}）")
        else:
            istep = scol.astype(np.int64)
            start_want = int(man.get("step_start", 1))
            if int(istep[0]) != start_want:
                add("step_numbers", "fail",
                    f"首步号 {int(istep[0])} 对预期 {start_want}")
            elif len(np.unique(istep)) != len(istep):
                _u, _inv = np.unique(istep, return_inverse=True)
                _mask = np.bincount(_inv)[_inv] > 1
                first_dup = int(np.flatnonzero(_mask)[0])
                add("step_numbers", "fail",
                    f"重复步号 {int(istep[first_dup])}"
                    f"（首个重复发生在行 {first_dup}）")
            else:
                d = np.diff(istep)
                gap = np.flatnonzero(d > 1)
                neg = np.flatnonzero(d < 0)
                if len(neg):
                    i = int(neg[0])
                    add("step_numbers", "fail",
                        f"步号倒退（负差）：步 {int(istep[i])} 后退至 "
                        f"{int(istep[i + 1])}（行 {i}）")
                elif len(gap):
                    i = int(gap[0])
                    add("step_numbers", "fail",
                        f"缺步：步 {int(istep[i])} 后跳至 "
                        f"{int(istep[i + 1])}")
                else:
                    add("step_numbers", "pass",
                        f"步号 {int(istep[0])} 至 {int(istep[-1])} "
                        f"整数、连续、唯一、严格递增")
        result["step_first"] = _null_if_nonfinite(scol[0]) if n_rows else None
        result["step_last"] = _null_if_nonfinite(scol[-1]) if n_rows else None

    # 五 非有限数（逐通道统计，规范输出中数值写 null）
    nonfinite = {"total": 0, "by_channel": {}, "first_step": None}
    if n_rows > 0:
        bad_total = 0
        for name, (cs, cn) in _chan_slices().items():
            m = ~np.isfinite(arr[:, cs:cs + cn])
            k = int(m.sum())
            if k:
                nonfinite["by_channel"][name] = k
                bad_total += k
                if nonfinite["first_step"] is None:
                    fv = float(arr[int(np.flatnonzero(m.any(axis=1))[0]), 0])
                    nonfinite["first_step"] = (int(fv) if fv.is_integer()
                                               else _null_if_nonfinite(fv))
        nonfinite["total"] = bad_total
        add("finite_values", "pass" if bad_total == 0 else "fail",
            "全部有限" if bad_total == 0
            else f"非有限数 {bad_total} 个（首见步 {nonfinite['first_step']}，"
                 f"分通道 {nonfinite['by_channel']}）")
    result["nonfinite"] = nonfinite

    # 六 完整范围（I-184 返修：唯一依据是采集前清单的预期步数；不得用
    # 实际行数或尾部自报数反推。缺依据判待核，不放行完整一致）
    if manifest and manifest.get("expected_steps") is not None:
        exp = int(manifest["expected_steps"])
        if n_rows == exp:
            add("complete_range", "pass", f"实际 {n_rows} 步对预期 {exp} 步")
        elif n_rows < exp:
            add("complete_range", "fail",
                f"截短：实际 {n_rows} 步对预期 {exp} 步（缺 {exp - n_rows} 步）")
        else:
            add("complete_range", "fail",
                f"超出预期：实际 {n_rows} 步对预期 {exp} 步")
        result["range"] = {"expected_steps": exp, "actual_steps": n_rows,
                           "basis": "manifest"}
    else:
        add("complete_range", "unknown",
            "缺完整范围依据（采集前清单未声明预期步数），不判完整一致（待核）")
        result["range"] = {"expected_steps": None, "actual_steps": n_rows,
                           "basis": "none"}

    # 七 采样间隔（配方级）
    if manifest and manifest.get("phys_dt") is not None:
        tol = float(manifest.get("phys_dt_tol", 1e-9))
        got = float(header.get("phys_dt") or 0.0)
        if abs(got - float(manifest["phys_dt"])) <= tol:
            add("phys_dt", "pass", f"档案 {got} 对清单 {man['phys_dt']}")
        else:
            add("phys_dt", "fail", f"步长不符: 档案 {got} 对清单 {man['phys_dt']}")

    # 八 必需通道（I-184 返修：计数键缺失为信息不足判待核，不默认零；
    # 缺失发生位置须有证据，预先批准例外按步位核对；欠长补零不是实测零）
    req = man.get("required_channels") or {}
    approved = (manifest or {}).get("approved_misses") or {}
    miss_positions = (tail or {}).get("miss_positions") \
        if isinstance(tail, dict) else None
    short_counts = (tail or {}).get("short_counts") \
        if isinstance(tail, dict) else None
    short_positions = (tail or {}).get("short_positions") \
        if isinstance(tail, dict) else None
    if miss_counts is None:
        if req:
            add("required_channels", "unknown",
                "缺通道缺失统计，无法核对必需通道（待核）")
    else:
        bad, unk = [], []
        for name in req:   # 计数式允许值不生效：例外只走 approved_misses 加位置证据
            if name not in miss_counts:
                unk.append(f"{name} 计数键缺失（信息不足，不默认零次）")
                continue
            k = int(miss_counts.get(name, 0))
            if k <= 0:
                continue
            rule = approved.get(name)
            pos = (miss_positions or {}).get(name)
            if not isinstance(pos, list) or len(pos) != k:
                # 旧档只有累计数、位置不可定位：有批准规则也不能核对步位
                unk.append(f"{name} 缺 {k} 步但档案无发生位置证据"
                           "（旧档仅累计数，待核）")
                continue
            if rule:
                allow_steps = [int(x) for x in rule.get("at_steps", [])]
                rule_max = int(rule.get("max", 0))
                off = [int(x) for x in pos if int(x) not in allow_steps]
                if k > rule_max or off:
                    bad.append(f"{name} 缺 {k} 步（位置 {pos}）超出预先"
                               f"批准例外（允许至多 {rule_max} 步、步位 "
                               f"{allow_steps}）")
                else:
                    notes.append(f"{name} 缺 {k} 步在预先批准例外内"
                                 f"（位置 {pos}）")
            else:
                bad.append(f"{name} 缺 {k} 步（位置 {pos}），无预先批准例外")
        # 欠长补零（新记录器证据；旧档无此键时欠长不可核对，判待核）
        if req and short_counts is None:
            unk.append("档案无欠长补零统计（旧格式尾部），欠长不可核对（待核）")
        elif isinstance(short_counts, dict):
            for name in req:
                if name not in short_counts:
                    unk.append(f"{name} 欠长计数键缺失（信息不足，不默认零）")
                    continue
                sk = int(short_counts.get(name, 0))
                if sk > 0:
                    sp = (short_positions or {}).get(name)
                    bad.append(f"{name} 欠长补零 {sk} 步（补零不是实测零，"
                               f"位置 {sp}）")
        if bad:
            add("required_channels", "fail", "；".join(bad))
        elif unk:
            add("required_channels", "unknown", "；".join(unk))
        else:
            add("required_channels", "pass",
                "必需通道完整（无缺失、无欠长补零）")
    result["miss_counts"] = miss_counts
    result["short_counts"] = short_counts

    # 九 运行条件（头 env 对清单期望；头缺 env 记录则待核）
    if manifest and manifest.get("run_conditions"):
        want_rc = manifest["run_conditions"]
        env = header.get("env")
        if not isinstance(env, dict):
            add("run_conditions", "unknown", "头内缺运行条件记录（待核）")
        else:
            bad = [f"{k}={env.get(k)!r} 对期望 {v!r}"
                   for k, v in want_rc.items() if env.get(k) != v]
            add("run_conditions", "fail" if bad else "pass",
                "；".join(bad) if bad else f"{len(want_rc)} 项运行条件一致")
    result["env"] = header.get("env")

    # 十 配方事件规则（仅声明规则被报告；仅 disqualify_on 违反判不合格）
    events = []
    if manifest and manifest.get("event_rules"):
        slices = _chan_slices()
        for rule in manifest["event_rules"]:
            name = rule.get("name", "?")
            ch = rule.get("channel")
            if ch not in slices:
                add(f"event:{name}", "fail", f"规则引用未知通道 {ch}")
                continue
            cs, cn = slices[ch]
            comp = int(rule.get("component", 0))
            if not (0 <= comp < cn):
                add(f"event:{name}", "fail", f"规则分量越界 {comp}")
                continue
            col = cs + comp
            op = rule.get("op", "<")
            thr = float(rule.get("threshold", 0.0))
            after = int(rule.get("after_step", 0))
            v = arr[:, col] if n_rows else np.zeros(0)
            m = (v < thr) if op == "<" else (v > thr)
            if n_rows:
                m &= (arr[:, 0] >= after)
            k = int(m.sum())
            if k:
                fv = float(arr[int(np.flatnonzero(m)[0]), 0])
                first = int(fv) if fv.is_integer() else _null_if_nonfinite(fv)
            else:
                first = None
            events.append({
                "name": name, "channel": f"{ch}[{comp}]", "op": op,
                "threshold": thr, "after_step": after,
                "n_violating_steps": k, "first_step": first,
                "disqualify_on": bool(rule.get("disqualify_on", False)),
            })
            if k and rule.get("disqualify_on", False):
                add(f"event:{name}", "fail",
                    f"违反适用条件：{k} 步（首见步 {first}）")
            else:
                add(f"event:{name}", "pass",
                    "无违反" if k == 0 else
                    f"{k} 步命中规则（报告性质，未声明不合格，首见步 {first}）")
    result["events"] = events

    result["checks"] = checks
    result["notes"] = notes
    if fails:
        result["status"] = "invalid"
        result["reason"] = "；".join(fails)
    elif unknowns:
        result["status"] = "unknown"
        result["reason"] = "；".join(unknowns)
    else:
        result["status"] = "valid"
        result["reason"] = "全部检查通过" + (
            "（配方级未声明项见 notes）" if notes else "")
    if json_out:
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
    return result


def compare(a_path, b_path, json_out=None, period_s=None, max_list=60,
            manifest=None):
    """完整性资格检查加逐位比较两份台账（事项 I-184 修正版）。

    完整一致（退出码零）要求：两份资格检查均 valid、步数相同且体部
    逐位相同，且清单声明预期步数时两份均达到预期。长度不同、空记录、
    两份共同截短一律不得判完整一致（退出码一并附原因；公共段差异仍
    照常定位首差步与首差通道）。任一资格 invalid 返回二；资格依据缺失
    （unknown）且无法排除差异时返回三（待核）。报告的 identical 字段
    为修正后语义（完整一致）；common_part_identical 保留旧口径供对照。
    """
    qa = qualify(a_path, manifest)
    qb = qualify(b_path, manifest)
    if qa["status"] == "invalid" or qb["status"] == "invalid":
        print(f"[FW-CMP] 资格检查不合格，拒绝比较：")
        for tag, q in (("A", qa), ("B", qb)):
            if q["status"] == "invalid":
                print(f"[FW-CMP]   {tag} {q['input_refs']['path']}"
                      f"：{q['reason']}")
        report = {"a": os.path.abspath(a_path), "b": os.path.abspath(b_path),
                  "verdict": "archive_error",
                  "qualification_a": qa, "qualification_b": qb}
        if json_out:
            with open(json_out, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=1)
        return 2
    try:
        ha, A = load(a_path)
        hb, B = load(b_path)
    except Exception as exc:
        print(f"[FW-CMP] 档案错误: {exc!r}")
        return 2
    if ha.get("channels") != hb.get("channels") \
            or ha.get("joint_names") != hb.get("joint_names"):
        print("[FW-CMP] 两份台账通道布局或关节名不一致，拒绝比较")
        return 2
    T = min(len(A), len(B))
    steps_a = A[:T, 0]
    steps_b = B[:T, 0]
    if T > 0 and not np.array_equal(steps_a, steps_b):
        bad = int(np.flatnonzero(steps_a != steps_b)[0])
        print(f"[FW-CMP] 步号列错位（首个不齐步 {bad}），档案损坏")
        return 2
    jn = ha.get("joint_names") or [f"j{i}" for i in range(9)]
    slices = _chan_slices()

    def chan_of(col):
        for name, (cs, cn) in slices.items():
            if cs <= col < cs + cn:
                sub = col - cs
                label = (f"{jn[sub]}" if name in ("jp", "jv", "je", "tgt")
                         and sub < len(jn) else f"[{sub}]")
                return name, label
        return "?", "?"

    Au = A[:T].view(np.uint64)
    Bu = B[:T].view(np.uint64)
    neq = Au != Bu
    rows = np.flatnonzero(neq.any(axis=1))
    common_identical = bool(len(rows) == 0)
    full_identical = bool(len(A) == len(B) and common_identical
                          and np.array_equal(A.view(np.uint64),
                                             B.view(np.uint64)))
    expected = int(manifest["expected_steps"]) \
        if (manifest and manifest.get("expected_steps") is not None) else None
    any_unknown = qa["status"] == "unknown" or qb["status"] == "unknown"

    # 完整性判定（I-184 核心）：长度、预期范围、资格依据逐项对照
    reasons = []
    if len(A) != len(B):
        reasons.append(f"长度不同：A {len(A)} 步对 B {len(B)} 步，"
                       "不得判完整一致")
    if expected is not None and len(A) < expected and len(B) < expected:
        reasons.append(f"两份共同截短：各 {len(A)}/{len(B)} 步对预期 "
                       f"{expected} 步，须对照完整范围")
    elif expected is not None and (len(A) < expected or len(B) < expected):
        reasons.append(f"单份截短：A {len(A)} 步、B {len(B)} 步对预期 "
                       f"{expected} 步")

    report = {
        "a": os.path.abspath(a_path), "b": os.path.abspath(b_path),
        "phys_dt": ha.get("phys_dt"), "steps_compared": int(T),
        "steps_a": int(len(A)), "steps_b": int(len(B)),
        "expected_steps": expected,
        "identical": full_identical,
        "common_part_identical": common_identical,
        "qualification_a": qa, "qualification_b": qb,
    }
    print(f"[FW-CMP] A 步数 {len(A)}  B 步数 {len(B)}  对齐比较 {T} 步")
    print(f"[FW-CMP] 资格：A {qa['status']}（{qa['reason']}）"
          f"  B {qb['status']}（{qb['reason']}）")

    if full_identical and not reasons:
        if any_unknown:
            print("[FW-CMP] 判定：待核（资格依据缺失，不判完整一致）")
            report["verdict"] = "unknown"
            if json_out:
                with open(json_out, "w", encoding="utf-8") as f:
                    json.dump(report, f, ensure_ascii=False, indent=1)
            return 3
        print("[FW-CMP] 判定：完整一致（IDENTICAL，体部逐位相同）")
        report["verdict"] = "identical"
        if json_out:
            with open(json_out, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=1)
        return 0

    if reasons:
        verdict = "not_complete"
        if common_identical:
            print("[FW-CMP] 公共段逐位相同，但因下列原因不得判完整一致：")
        for r in reasons:
            print(f"[FW-CMP]   - {r}")
    else:
        verdict = "different"
    if not common_identical:
        s = int(rows[0])
        # 步号口径（阶段零校验修正）：全部对外报告一律用步号列的值。
        steps_col = A[:T, 0]
        s_star = int(steps_col[s])
        cols = np.flatnonzero(neq[s])
        col0 = int(cols[0])
        cname, clabel = chan_of(col0)
        va, vb = float(A[s, col0]), float(B[s, col0])
        bits64 = abs(int(A[s, col0].view(np.uint64))
                     - int(B[s, col0].view(np.uint64)))
        report.update({
            "s_star": s_star,
            "t_star": s_star * float(ha.get("phys_dt") or 0.0),
            "first_channel": f"{cname}.{clabel}", "first_col": col0,
            "first_val_a": _null_if_nonfinite(va),
            "first_val_b": _null_if_nonfinite(vb),
            "val_diff": _null_if_nonfinite(va - vb),
            "first_bits64": bits64,
            "first_bits32": _bits32(va, vb),
            "n_diff_steps": int(len(rows)),
            "last_diff_step": int(steps_col[rows[-1]]),
        })
        # 同步首差步内的全部差通道
        same_step = []
        for c in cols:
            cn, cl = chan_of(int(c))
            x, y = float(A[s, c]), float(B[s, c])
            same_step.append({"channel": f"{cn}.{cl}",
                              "val_diff": _null_if_nonfinite(x - y),
                              "bits32": _bits32(x, y)})
        report["first_step_channels"] = same_step
        # 每通道统计
        per_chan = {}
        for name, (cs, cn) in slices.items():
            m = neq[:, cs:cs + cn].any(axis=1)
            if m.any():
                r = np.flatnonzero(m)
                per_chan[name] = {
                    "n_steps": int(len(r)),
                    "first_step": int(steps_col[r[0]]),
                    "last_step": int(steps_col[r[-1]]),
                }
        report["per_channel"] = per_chan
        # 差步分布（十秒桶）
        bucket = int(round(10.0 / float(ha.get("phys_dt") or 1.0)))
        hist = {}
        for r in rows:
            key = int(steps_col[r]) // bucket
            hist[str(key)] = hist.get(str(key), 0) + 1
        report["diff_hist_10s"] = hist
        report["diff_steps_head"] = [int(steps_col[x]) for x in rows[:max_list]]
        print(f"[FW-CMP] 首差步 s*={s_star}"
              f"（t={s_star * float(ha.get('phys_dt') or 0):.3f}s）"
              f"  首差通道 c*={cname}.{clabel}"
              f"  32位位差 {report['first_bits32']}"
              f"  64位位差 {bits64}  差步 {len(rows)}/{T}")
    # 周期积分差分（方案 5.2，保留既有功能）：窗口=扑翼周期，力与力矩类
    if period_s and T > 0:
        w = max(1, int(round(float(period_s)
                             / float(ha.get("phys_dt") or 1e-3))))
        wins = []
        for name in ("je", "Fa", "Ta"):
            cs, cn = slices[name]
            ia = A[:T, cs:cs + cn]
            ib = B[:T, cs:cs + cn]
            k = T // w
            if k == 0:
                continue
            da = ia[:k * w].reshape(k, w, cn).sum(axis=1)
            db = ib[:k * w].reshape(k, w, cn).sum(axis=1)
            d = da - db
            nz = np.flatnonzero((d != 0).any(axis=1))
            wins.append({
                "channel": name, "window_steps": w, "n_windows": k,
                "first_nonzero_window": int(nz[0]) if len(nz) else None,
                "max_abs_sum_diff": float(np.abs(d).max()),
            })
        report["period_integral"] = wins
        for wi in wins:
            print(f"[FW-CMP] 周期积分 {wi['channel']}: 窗长{wi['window_steps']}步"
                  f" 共{wi['n_windows']}窗 首个非零差分窗="
                  f"{wi['first_nonzero_window']}"
                  f" 最大窗积分差={wi['max_abs_sum_diff']:.6e}")

    report["verdict"] = verdict
    report["reasons"] = reasons
    print(f"[FW-CMP] 判定：{'存在差异（DIFFERENT）' if verdict == 'different' else '不得判完整一致（NOT COMPLETE）'}")
    if json_out:
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        print(f"[FW-CMP] 机器可读档案 → {json_out}")
    return 1


def _load_manifest(path):
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _main(argv):
    if len(argv) >= 2 and argv[0] == "qualify":
        man = None
        jo = None
        rest = argv[2:]
        i = 0
        while i < len(rest):
            if rest[i] == "--manifest":
                man = _load_manifest(rest[i + 1])
                i += 2
            elif rest[i] == "--json":
                jo = rest[i + 1]
                i += 2
            else:
                i += 1
        q = qualify(argv[1], manifest=man, json_out=jo)
        print(f"[FW-QUAL] {q['input_refs']['path']}"
              f"：{q['status']}（{q['reason']}）")
        return {"valid": 0, "invalid": 2, "unknown": 3}.get(q["status"], 2)
    if len(argv) >= 3 and argv[0] == "compare":
        a, b = argv[1], argv[2]
        jo = None
        period = None
        man = None
        rest = argv[3:]
        i = 0
        while i < len(rest):
            if rest[i] == "--json":
                jo = rest[i + 1]
                i += 2
            elif rest[i] == "--period-s":
                period = float(rest[i + 1])
                i += 2
            elif rest[i] == "--manifest":
                man = _load_manifest(rest[i + 1])
                i += 2
            else:
                i += 1
        return compare(a, b, json_out=jo, period_s=period, manifest=man)
    print("用法: python modules/fw_iid_ledger.py compare A.bin B.bin "
          "[--json out.json] [--period-s 0.556] [--manifest m.json]\n"
          "      python modules/fw_iid_ledger.py qualify FILE.bin "
          "[--manifest m.json] [--json out.json]\n"
          "退出码：compare 0=完整一致 1=差异或不得判完整一致 2=档案或资格"
          "错误 3=待核；qualify 0=valid 2=invalid 3=unknown")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
