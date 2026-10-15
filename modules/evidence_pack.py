# -*- coding: utf-8 -*-
"""evidence_pack.py — 统一证据包（事项 I-186，L1 队接续补齐版）。

判据（冻结）：真实实验的实际条件、记录量含义、数据资格及科学判据结果
均能从证据包恢复并逐项追溯，缺失和未执行判据不会成为通过，旧配置接入
及回退检查通过。

L1 接续补齐要点（Doro 当前核验结果 2026-10-14，三组缺口）：
一、整科完整性：必需判据集合由科目声明决定（判据先于数据），不能由结
果文件自选子集。bench_subject 声明要求该科目全部判据条目齐备，缺任一
条即补 missing 条目并阻断；条目重复判证据歧义阻断；单判据声明
（bench_criterion）把批准范围收窄为该条，结论明确记录范围，不代称整科
通过，范围外条目如实记不适用不参与判定。
二、生产格式贯通：当前分数生产函数（bench_runner write_scores_json）的
逐条嵌套结果（subjects[].criteria[]，含 measured 与 threshold 字典）由
本包按批准测量语义登记册的绑定（bindings 或 threshold_key 加指标到实测
键适配）直接展开消费，带本次实验/运行身份与实际分析输入哈希核对；分析
输入中的仓库标准定义文件按当前内容核哈希，其余须属于本次运行原始输入。
三、必要条件与版本：批准配方的必要条件节（来源、条件键、必需量清单）
随起始采集留档，条件键逐一核对注入，必需量对照实际记录量映射核对，空
清单不豁免无条件必要项；运行环境记录子进程内实测的解释器版本与仿真运
行环境包版本读数，启动命令不再充当版本。

返修三版要点（Doro 新稿复核与返修决定 2026-10-13，六反例）：
一、判据结果核对事项、实验、运行及实际分析输入内容哈希：结果文件须携
带 experiment_id/run_id 与 analyzed_inputs（文件加哈希），并与本次原始
输入相符；漏来源或错配阻断，结果文件自身哈希只证明文件未变。
二、科学字段齐备有限且与批准规则一致：值、单位、阈值、比较规则、判据
版本缺一不可；非有限值记 null 判未知；值与阈值矛盾于通过字样判 fail；
结论保留原检查入口，不由证据包另选算法。
三、实验阶段与翼拍相位分开：phase 列经 flight_recorder 生产代码核实为
实验阶段（experiment_phase.json）；翼拍相位只认经核实生产来源列（当前
无），缺失如实限制拍内与周期对齐结论，不构造替代值。
四、实际生效条件取自注入本次覆盖的子进程快照（配置模块导入时消费
FLAPPER_PHYS_DT 等环境变量），父进程默认不得冒充实际配置；必要缺项保
守标缺失或未知，仅有采集文件不宣布适用或有效。
成对发布与混代检出（返修二保留）：包卡同 publish_id，双临时文件先后改
名，第二失败回滚第一；核验重查全部输入内容哈希与卡片对齐。

用法：
  python modules/evidence_pack.py capture <运行目录> <配置JSON路径>
  python modules/evidence_pack.py build <运行目录>
  python modules/evidence_pack.py verify <运行目录>/evidence_pack.json
"""
import csv
import datetime
import hashlib
import json
import math
import os
import subprocess
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_VERSION = 4
SPEC_REL = "logs/hq_intake/doro_tools_review_20261012/I-183_接口规格.json"
SPEC_PATH = os.path.join(ROOT, SPEC_REL)
TOOL = "modules/evidence_pack.py"
PACK_NAME = "evidence_pack.json"
CARD_NAME = "result_card.json"
CONDITIONS_NAME = "run_conditions.json"
PATCH_NAME = "source_uncommitted.patch"

SEMANTICS_REL = "config/standard_measure_semantics.json"

STATES = {
    "execution": ["not_started", "running", "completed", "failed",
                  "timed_out", "unknown"],
    "data_qualification": ["valid", "invalid", "unknown", "missing",
                           "stale", "not_applicable"],
    "applicability": ["applicable", "not_applicable", "unknown", "missing",
                      "stale"],
    "criterion": ["pass", "fail", "not_executed", "unknown", "missing",
                  "stale", "not_applicable"],
    "control_use": ["qualified", "diagnostic_only", "unknown", "missing",
                    "stale", "not_applicable"],
    "experiment_verdict": ["pass", "fail", "unknown"],
}

CMP_FUNCS = {
    "<=": lambda v, t: v <= t, "<": lambda v, t: v < t,
    ">=": lambda v, t: v >= t, ">": lambda v, t: v > t,
    "==": lambda v, t: v == t, "!=": lambda v, t: v != t,
}

# 翼拍相位经核实的生产来源列。当前数据流无：phase 列经 flight_recorder
# 生产代码与隔离 CSV 双重核实为实验阶段（读 experiment_phase.json），
# vphz 未经生产来源核实不得猜作拍相位。
FLAP_PHASE_COLUMNS = ()

COLUMN_GROUPS = {
    "t": "time", "phase": "experiment_phase",
    "vphz": "unverified_source",
    "freq": "command", "dA": "command", "elev": "command",
    "ail": "command", "rud": "command", "pitch_des": "command",
    "roll_des": "command", "vxd": "command", "veff": "command",
    "mode": "command", "ctlA": "command", "tailA": "command",
    "freqL": "command", "freqR": "command", "gateL": "command",
    "gateR": "command", "dlag": "command",
    "Fx": "aerodynamic", "Fy": "aerodynamic", "Fz": "aerodynamic",
    "Mx": "aerodynamic", "My": "aerodynamic", "Mz": "aerodynamic",
    "TMx": "aerodynamic", "TMy": "aerodynamic", "TMz": "aerodynamic",
    "vairx": "aerodynamic", "vairy": "aerodynamic", "vairz": "aerodynamic",
    "vslip": "aerodynamic", "clip": "aerodynamic",
    "Fwx": "aerodynamic", "Fwy": "aerodynamic", "Fwz": "aerodynamic",
    "Twx": "aerodynamic", "Twy": "aerodynamic", "Twz": "aerodynamic",
    "Ftx": "aerodynamic", "Fty": "aerodynamic", "Ftz": "aerodynamic",
    "Frx": "aerodynamic", "Fry": "aerodynamic", "Frz": "aerodynamic",
    "Trx": "aerodynamic", "Try": "aerodynamic", "Trz": "aerodynamic",
    "tvfx": "aerodynamic", "tvfz": "aerodynamic",
    "aLmean": "aerodynamic", "aLmax": "aerodynamic", "aLs45": "aerodynamic",
    "aRmean": "aerodynamic", "aRmax": "aerodynamic", "aRs45": "aerodynamic",
    "x": "body", "y": "body", "z": "body", "vx": "body", "vy": "body",
    "vz": "body", "wx": "body", "wy": "body", "wz": "body",
    "qw": "body", "qx": "body", "qy": "body", "qz": "body",
    "roll": "attitude", "pitch": "attitude",
    "flapLt": "joint", "flapRt": "joint", "twistLt": "joint",
    "twistRt": "joint", "flapLa": "joint", "flapRa": "joint",
    "twistLa": "joint", "twistRa": "joint", "effFlapL": "joint",
    "effFlapR": "joint", "effTwistL": "joint", "effTwistR": "joint",
    "foldT": "joint", "foldLa": "joint", "foldRa": "joint",
    "joints_json": "joint", "strips_json": "joint", "jeff_json": "joint",
    "jforce_json": "joint",
    "pin": "dof_constraints", "tension": "dof_constraints",
}

SNAP_CODE = (
    "import json, os, sys, platform\n"
    "sys.path.insert(0, %r)\n"
    "import config.sim_config as c\n"
    "exec(%r)  # 生产环境桥纯配置原语句（AST 提取自 build_env_bridge）\n"
    "out = {'classes': {}}\n"
    "def take(name, keys):\n"
    "    cls = getattr(c, name, None)\n"
    "    out['classes'][name] = ({k: getattr(cls, k, None) for k in keys}\n"
    "                           if cls is not None else\n"
    "                           {'error': 'config class missing'})\n"
    "out['aero_model'] = getattr(c, 'AERO_MODEL', None)\n"
    "out['phys_dt'] = getattr(c, 'PHYSICS_DT', None)\n"
    "out['mass_kg'] = getattr(c, 'MASS', None)\n"
    "out['launch_pitch_rad'] = getattr(c, 'LAUNCH_PITCH', None)\n"
    "out['dp_target_z_m'] = getattr(c, 'DP_TARGET_Z', None)\n"
    "take('FlapKinematicsConfig', ['FREQUENCY', 'AMPLITUDE', 'BIAS',\n"
    "     'MEAN', 'SPLIT', 'PITCH_LAG_S', 'PITCH_MODE', 'ALPHA_SET',\n"
    "     'PHASE_OFFSET_LR'])\n"
    "fc = getattr(c, 'FlightControllerConfig', None)\n"
    "out['controller_switches'] = ({k: getattr(fc, k) for k in dir(fc)\n"
    "    if k.isupper() and isinstance(getattr(fc, k), bool)}\n"
    "    if fc is not None else {'error': 'config class missing'})\n"
    "take('TailAeroConfig', ['AREA', 'PANEL_Y'])\n"
    "take('WingGeometryConfig', ['SPAN', 'ROOT_CHORD', 'TIP_CHORD',\n"
    "     'N_STRIPS'])\n"
    "out['errors'] = [n for n, v in out['classes'].items()\n"
    "                 if isinstance(v, dict) and 'error' in v]\n"
    "# 运行环境版本（事项 I-186）：子进程内实测解释器版本与仿真运行环境包\n"
    "# 版本读数；未安装的包记 None 加原因，不以启动命令充当版本。\n"
    "def _pkgver(name):\n"
    "    try:\n"
    "        import importlib.metadata as _im\n"
    "        return {'package': name, 'version': _im.version(name)}\n"
    "    except Exception:\n"
    "        try:\n"
    "            _m = __import__(name)\n"
    "            _v = str(getattr(_m, '__version__', '') or '')\n"
    "            if _v:\n"
    "                return {'package': name, 'version': _v}\n"
    "            return {'package': name, 'version': None,\n"
    "                    'reason': '已导入但未暴露版本号'}\n"
    "        except Exception as _e:\n"
    "            return {'package': name, 'version': None,\n"
    "                    'reason': '未安装或不可导入（' + type(_e).__name__"
    " + '）'}\n"
    "out['runtime_versions'] = {\n"
    "    'interpreter': {\n"
    "        'python_implementation': platform.python_implementation(),\n"
    "        'python_version': platform.python_version(),\n"
    "        'sys_version': sys.version,\n"
    "        'executable': sys.executable,\n"
    "        'machine': platform.machine(),\n"
    "    },\n"
    "    # 探测清单：isaac 模式运行环境 isaacsim；python 模式后端数学依赖\n"
    "    # numpy（生产后端与气动求解均导入）。读数而非声明。\n"
    "    'sim_runtimes': [_pkgver('isaacsim'), _pkgver('numpy')],\n"
    "}\n"
    "print(json.dumps(out))\n"
)

# 必要生效参数（返修四组二：null 或非有限即条件缺陷，阻断资格与适用性；
# 模型明确不用某量时由清单 not_applicable_params 附理由豁免）
REQUIRED_PARAMS = [
    ("FlapKinematicsConfig", "FREQUENCY"),
    ("FlapKinematicsConfig", "AMPLITUDE"),
    ("TailAeroConfig", "AREA"),
    ("TailAeroConfig", "PANEL_Y"),
    ("WingGeometryConfig", "SPAN"),
]


def _bridge_pure_statements():
    """从生产桥 build_env_bridge 的语法树提取纯配置原语句。

    只保留不引用 world 与 mav_view 的顶层语句（环境读取、配置导入与
    属性覆写、打印、数值转换），不复制其映射、不构建物理对象——与
    Doro runtime_bridge 探针同法。桥源码变更时提取随之变更，不另写
    环境映射表。
    """
    import ast
    path = os.path.join(ROOT, "modules", "sim_env_bridge.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "build_env_bridge")

    def pure(node):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id in ("world", "mav_view"):
                return False
        return True
    body = [st for st in fn.body if not isinstance(
        st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Return))]
    # 不动点排除：被排除语句所赋的局部名（如 aero_callback）若被后续
    # 语句引用，该语句一并排除——只保留真正独立于运行对象的纯配置语句。
    excluded = {i for i, st in enumerate(body) if not pure(st)}

    def loads(node):
        return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)
                and isinstance(n.ctx, ast.Load)}

    def stores(node):
        out = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)
               and isinstance(n.ctx, ast.Store)}
        for sub in ast.walk(node):
            if isinstance(sub, ast.ExceptHandler) and sub.name:
                out.add(sub.name)
            if isinstance(sub, ast.Import):
                for a in sub.names:
                    out.add(a.asname or a.name.split(".")[0])
            elif isinstance(sub, ast.ImportFrom):
                for a in sub.names:
                    out.add(a.asname or a.name)
        return out

    CALL_WHITELIST = {"float", "int", "str", "print", "min", "max",
                      "len", "abs", "range", "enumerate", "setattr",
                      "getattr", "round"}
    ATTR_CALL_WHITELIST = {"get", "strip", "items", "keys", "values",
                           "split", "format"}

    def calls_ok(node):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                f = sub.func
                if isinstance(f, ast.Name) and f.id in CALL_WHITELIST:
                    continue
                if isinstance(f, ast.Attribute) \
                        and f.attr in ATTR_CALL_WHITELIST:
                    continue
                return False
        return True

    def import_ok(node):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Import):
                if any(not a.name.startswith("config")
                       for a in sub.names):
                    return False
            elif isinstance(sub, ast.ImportFrom):
                if not (sub.module or "").startswith("config"):
                    return False
        return True
    # 顺序数据流加调用与导入白名单：只保留独立于运行对象的纯配置语句
    # （环境读取、配置导入与属性覆写、数值转换、打印）；网络与跟踪器
    # 等对象构造、Isaac 侧导入一律排除，其后续使用者随之排除。
    defined = {"os", "json", "sys", "float", "int", "str", "print",
               "min", "max", "len", "abs", "range", "enumerate",
               "setattr", "getattr", "round",
               "Exception", "BaseException", "ValueError", "TypeError",
               "KeyError", "RuntimeError", "ImportError"}
    kept = []
    for st in body:
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant):
            continue  # 文档串与纯字符串语句跳过
        ld, st_stores = loads(st), stores(st)
        if "world" in ld or "mav_view" in ld:
            continue
        if not (ld - st_stores) <= defined:
            continue
        if not calls_ok(st) or not import_ok(st):
            continue
        kept.append(st)
        defined |= st_stores
    return "\n".join(ast.unparse(st) for st in kept)


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _status(kind, status, reason, extra=None):
    assert status in STATES[kind], (kind, status)
    obj = {"status": status, "reason": reason, "source_tool": TOOL,
           "source_version": "pack%d" % SCHEMA_VERSION,
           "generated_at": _now(), "input_refs": []}
    if extra:
        obj.update(extra)
    return obj


def _git(*args):
    try:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                           text=True, timeout=30, encoding="utf-8",
                           errors="replace")
        return r.stdout or ""
    except Exception:
        return ""


def _effective_config_snapshot(child_env=None):
    """生效配置快照（返修四组二）。

    child_env 非空时在注入该环境的子进程内导入 config.sim_config 并执行
    生产桥 build_env_bridge 的纯配置原语句（AST 提取），得到含桥覆写在
    内的最终生效值；缺必要参数（null 或非有限）记 required_missing；
    失败回退父进程并标 snapshot_mode，父进程默认不得冒充实际配置。
    """
    if child_env is not None:
        try:
            r = subprocess.run(
                [sys.executable, "-c",
                 SNAP_CODE % (ROOT, _bridge_pure_statements())],
                capture_output=True, text=True, timeout=60,
                env=child_env, encoding="utf-8", errors="replace")
            doc = json.loads((r.stdout or "").strip().splitlines()[-1])
            doc["snapshot_mode"] = "child_effective"
            miss = []
            for cls, key in REQUIRED_PARAMS:
                v = (doc.get("classes", {}).get(cls) or {}).get(key)
                if v is None or (isinstance(v, float) and not math.isfinite(v)):
                    miss.append("%s.%s=%r" % (cls, key, v))
            doc["required_missing"] = miss
            return doc
        except Exception as exc:
            doc = _effective_config_snapshot(None)
            doc["snapshot_mode"] = "parent_default_fallback"
            doc["fallback_reason"] = "子进程生效快照失败：%r" % exc
            return doc
    sys.path.insert(0, ROOT)
    import config.sim_config as c
    import platform as _plat
    out = {"classes": {}, "snapshot_mode": "parent_default"}

    def take(cls_name, keys):
        cls = getattr(c, cls_name, None)
        if cls is None:
            out["classes"][cls_name] = {"error": "配置类不存在"}
            return
        vals = {}
        for k in keys:
            v = getattr(cls, k, None)
            vals[k] = v.tolist() if hasattr(v, "tolist") else v
        out["classes"][cls_name] = vals
    out["aero_model"] = getattr(c, "AERO_MODEL", None)
    out["phys_dt"] = getattr(c, "PHYSICS_DT", None)
    out["mass_kg"] = getattr(c, "MASS", None)
    out["launch_pitch_rad"] = getattr(c, "LAUNCH_PITCH", None)
    out["dp_target_z_m"] = getattr(c, "DP_TARGET_Z", None)
    take("FlapKinematicsConfig",
         ["FREQUENCY", "AMPLITUDE", "BIAS", "MEAN", "SPLIT", "PITCH_LAG_S",
          "PITCH_MODE", "ALPHA_SET", "PHASE_OFFSET_LR"])
    fc = getattr(c, "FlightControllerConfig", None)
    if fc is not None:
        out["controller_switches"] = {
            k: getattr(fc, k) for k in dir(fc)
            if k.isupper() and isinstance(getattr(fc, k), bool)}
    else:
        out["controller_switches"] = {"error": "配置类不存在"}
    take("TailAeroConfig", ["AREA", "PANEL_Y"])
    take("WingGeometryConfig", ["SPAN", "ROOT_CHORD", "TIP_CHORD",
                                "N_STRIPS"])
    out["errors"] = [n for n, v in out["classes"].items()
                     if isinstance(v, dict) and "error" in v]
    # 父进程回退同样给出版本读数（标记为父进程实测，供缺项检查区分）
    def _pkgver(name):
        try:
            import importlib.metadata as im
            return {"package": name, "version": im.version(name)}
        except Exception:
            try:
                m = __import__(name)
                v = str(getattr(m, "__version__", "") or "")
                if v:
                    return {"package": name, "version": v}
                return {"package": name, "version": None,
                        "reason": "已导入但未暴露版本号"}
            except Exception as e:
                return {"package": name, "version": None,
                        "reason": "未安装或不可导入（%s）"
                                  % type(e).__name__}
    out["runtime_versions"] = {
        "interpreter": {
            "python_implementation": _plat.python_implementation(),
            "python_version": _plat.python_version(),
            "sys_version": sys.version,
            "executable": sys.executable,
            "machine": _plat.machine(),
        },
        "sim_runtimes": [_pkgver("isaacsim"), _pkgver("numpy")],
    }
    return out


def _child_env_for(config):
    """实际运行器注入（返修四组二：不另写组装器）。

    直接调用 experiment_pipeline.runner 的环境组装——与真实运行完全同一
    来源（含六把标准锁：确定性、原值锁、物理步后回调、物理处理器选择、
    障碍种子与数量）。
    """
    from modules.experiment_pipeline.runner import _build_child_env
    return _build_child_env(config)


def _snapshot_env_for(config):
    """按实际执行模式选择采集环境（事项 I-186 返修 v2）。

    isaac 模式的执行子进程环境为 _build_child_env（标准锁加参数节覆盖）；
    python 模式的链步实际运行于 _python_child_env（按隔离规则清空
    FLAPPER_ 前缀）。起始快照必须与实际执行子进程同一组装，不能用另一
    种环境证明本次条件；返回（环境字典，组装说明）。
    """
    from modules.experiment_pipeline import runner as _R
    mode = ((config or {}).get("scene") or {}).get("mode")
    if mode == "isaac":
        return (_R._build_child_env(config),
                "isaac_child（标准锁加参数节覆盖，与 isaac 执行子进程同"
                "一组装）")
    return (_R._python_child_env(),
            "python_step_iso（python 链步隔离规则：清空 FLAPPER_ 前缀，"
            "只注入编码变量；声明的参数节覆盖不注入 python 步）")


def capture_conditions(archive_dir, config):
    """运行启动前采集真正生效条件与可恢复源码差异（返修三版）。"""
    archive_dir = os.path.abspath(archive_dir)
    os.makedirs(archive_dir, exist_ok=True)
    head = _git("rev-parse", "HEAD").strip()
    st = _git("status", "--porcelain")
    diff = _git("diff")
    # 返修四组四：源码恢复材料覆盖暂存改动与实际运行需要的未跟踪源码，
    # 不把文件名清单称恢复材料（内容入档，超大或超量如实截断标注）。
    staged_diff = _git("diff", "--cached")
    untracked = [l for l in _git("ls-files", "--others",
                                 "--exclude-standard").splitlines() if l]
    SKIP_PREFIX = ("logs/", "android_shell/", "eagle_shell/", "outputs/",
                   "advisors/", "board_ui/node_modules/")
    untracked_src, truncated = [], []
    for f in untracked:
        if f.startswith(SKIP_PREFIX) or os.sep in f and f.split(os.sep)[0] \
                in SKIP_PREFIX:
            continue
        if not f.endswith((".py", ".json", ".toml")):
            continue
        try:
            if os.path.getsize(os.path.join(ROOT, f)) > 200_000:
                truncated.append("%s（过大跳过内容）" % f)
                continue
            with open(os.path.join(ROOT, f), encoding="utf-8",
                      errors="replace") as fh:
                untracked_src.append("===== 未跟踪源文件 %s =====\n%s"
                                     % (f, fh.read()))
        except Exception:
            truncated.append("%s（读取失败）" % f)
        if len(untracked_src) >= 40:
            truncated.extend(untracked[len(untracked_src):])
            break
    patch_path = None
    if st.strip() or staged_diff.strip() or untracked_src:
        patch_path = os.path.join(archive_dir, PATCH_NAME)
        with open(patch_path, "w", encoding="utf-8") as f:
            f.write(st + "\n=== 未暂存差异 ===\n" + diff
                    + "\n=== 已暂存差异 ===\n" + staged_diff
                    + "\n=== 未跟踪源码内容 ===\n"
                    + "\n".join(untracked_src)
                    + ("\n=== 截断说明 ===\n" + "\n".join(truncated)
                       if truncated else ""))
    child_env, env_basis = _snapshot_env_for(config)
    applied = {k: v for k, v in child_env.items()
               if k.startswith("FLAPPER_")}
    declared_ov = dict((config or {}).get("overrides") or {})
    not_applied = sorted(k for k in declared_ov if k not in applied)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "phase": "run_start",
        "captured_at": _now(),
        "tool": TOOL,
        "scene_mode": (config or {}).get("scene", {}).get("mode"),
        "env_assembly_basis": env_basis,
        "applied_overrides": applied,
        "declared_overrides_not_applied": not_applied,
        "declared_not_applied_note": (
            "参数节声明但按执行模式未注入任何子进程（python 步隔离）；"
            "必要条件检查以实际注入为准，声明未应用不构成生效条件"
            if not_applied else None),
        "source_version": {
            "source_revision": head or None,
            "dirty": bool(st.strip()),
            "uncommitted_files": [l.strip() for l in st.splitlines()
                                  if l.strip()],
            "uncommitted_patch": PATCH_NAME if patch_path else None,
            "uncommitted_patch_sha256":
                _sha256_file(patch_path) if patch_path else None,
        },
        "effective_config": _effective_config_snapshot(child_env),
        "declared": {
            "scene": (config or {}).get("scene"),
            "overrides": (config or {}).get("overrides"),
            "criteria": (config or {}).get("criteria"),
            "locks": (config or {}).get("locks"),
            "tags": (config or {}).get("tags"),
            # 事项 I-186 接续：必要条件节（来源、条件键、必需量清单）与
            # 事项、基准声明随起始采集留档，缺项检查与构包逐项核对。
            "item": (config or {}).get("item"),
            "baseline": (config or {}).get("baseline"),
            "necessary_conditions": (config or {}).get(
                "necessary_conditions"),
        },
    }
    tmp = os.path.join(archive_dir, CONDITIONS_NAME) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(archive_dir, CONDITIONS_NAME))
    return doc


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _identity_from_record(record):
    tags = (record or {}).get("tags") or {}
    rid = record.get("run_id") or tags.get("run_id")

    def field(value, reason):
        return {"value": value,
                "reason": None if value is not None else reason}
    return {
        "experiment_id": field(record.get("experiment_id")
                               or record.get("experiment"),
                               "运行记录未记实验标识"),
        "item_id": field(record.get("item"), "运行记录未记事项编号"),
        "baseline_id": field(record.get("baseline_id"),
                             "本次非基准对比运行，不适用"),
        "run_id": field(rid or tags.get("name"), "运行记录未记运行标识"),
        "repeat_group_id": field(record.get("repeat_group_id"),
                                 "本次不属于重复组，不适用"),
    }


def _bind_ledgers(run_dir, record, identity):
    """台账身份绑定：头内 run_id/tag 与本次运行身份不符即外来，不用。"""
    sys.path.insert(0, ROOT)
    from modules import fw_iid_ledger as L
    own, foreign = [], []
    run_key = ((identity.get("run_id") or {}).get("value")
               or ((record or {}).get("tags") or {}).get("name") or "")
    for f in sorted(os.listdir(run_dir)):
        if not (f.startswith("fw_ledger_") and f.endswith(".bin")):
            continue
        p = os.path.join(run_dir, f)
        try:
            h, _a = L.load(p)
        except Exception:
            foreign.append({"file": f, "reason": "台账读取失败"})
            continue
        ledger_rid = h.get("run_id") or h.get("tag") or ""
        if run_key and ledger_rid and ledger_rid != run_key:
            foreign.append({"file": f, "ledger_run_id": ledger_rid,
                            "reason": "台账运行标识 %r 与本次运行 %r 不符"
                                      % (ledger_rid, run_key)})
        elif not run_key or not ledger_rid:
            foreign.append({"file": f, "reason": "台账或运行记录缺运行标"
                                                 "识，无法绑定，待核"})
        else:
            own.append({"file": f, "path": p,
                        "sha256": _sha256_file(p),
                        "header_run_id": ledger_rid})
    return own, foreign


def _qualification(run_dir, own_ledgers, conditions):
    """完整固定清单传 I-184：预期步数、步长、运行条件；非子进程生效
    快照或快照缺类不判有效（必要条件缺失保守处理）。"""
    if not own_ledgers:
        listing = os.listdir(run_dir) if os.path.isdir(run_dir) else []
        return _status("data_qualification",
                       "missing" if not listing else "unknown",
                       "无可绑定的本次运行原值台账，数据资格缺失或待核")
    ec = (conditions or {}).get("effective_config") or {}
    if ec.get("snapshot_mode") != "child_effective":
        return _status("data_qualification", "unknown",
                       "起始快照非子进程生效值（mode=%s），必要条件缺失"
                       "不判有效" % ec.get("snapshot_mode"))
    if _conditions_deficiencies(conditions):
        return _status("data_qualification", "unknown",
                       "起始快照必要条件缺失（%s），不判有效"
                       % "；".join(_conditions_deficiencies(conditions)))
    # 返修 v2：资格清单取声明且实际应用的参数（python 步隔离模式下声明
    # 覆盖不注入任何子进程，不以未应用的声明充当生效条件；isaac 模式的
    # 实际注入含标准锁与标注变量，资格清单只对实验声明的参数节核对，
    # 不把锁与标注当作实验参数比对台账）。
    declared_ov = ((conditions or {}).get("declared") or {}).get(
        "overrides") or {}
    applied_ov = conditions.get("applied_overrides") or {}
    ov = {k: v for k, v in declared_ov.items() if k in applied_ov}
    man = {}
    # 预期步数为数据完整性期望（非环境生效声明）：取场景声明时长与生效
    # 物理步长；采集步实际收到的时长命令词见于运行记录步骤 argv 见证，
    # 不依赖未应用的环境变量。
    scene_dur = ((conditions.get("declared") or {}).get("scene") or {}
                 ).get("duration_s")
    if isinstance(scene_dur, (int, float)) and not isinstance(scene_dur, bool) \
            and ec.get("phys_dt"):
        man["expected_steps"] = int(round(
            float(scene_dur) / float(ec["phys_dt"])))
        man["expected_steps_source"] = (
            "场景声明时长 duration_s 与生效物理步长（采集步 argv 见证一"
            "致，不依赖未应用的环境变量）")
    if ec.get("phys_dt"):
        man["phys_dt"] = ec["phys_dt"]
    if ov:
        man["run_conditions"] = {k: v for k, v in ov.items()
                                 if k.startswith("FLAPPER_")}
    sys.path.insert(0, ROOT)
    from modules import fw_iid_ledger as L
    worst = None
    for ent in own_ledgers:
        q = L.qualify(ent["path"], manifest=man or None)
        obj = _status("data_qualification", q["status"],
                      "%s：%s" % (ent["file"], q["reason"]),
                      {"qualification": q,
                       "input_refs": [{"path": ent["path"],
                                       "sha256": ent["sha256"]}]})
        if worst is None or (obj["status"] in ("invalid", "unknown")
                             and worst["status"] == "valid"):
            worst = obj
    return worst


# 返修五：逐量测量口径（单位、坐标、采样时点、目标/实测/模型属性与
# 生产来源）。引用有版本哈希的明确逐量定义（I-183 冻结字段表）；未列
# 入的列如实标 unknown，不虚构。
COLUMN_META = {
    "t": ("s", "仿真物理时钟", "每物理步", "实测", "主循环物理时钟"),
    "freq": ("Hz", "标量", "控制步后", "目标", "控制器输出字典"),
    "dA": ("rad", "翼展方向", "控制步后", "目标", "控制器输出字典"),
    "elev": ("rad", "尾翼铰链", "控制步后", "目标", "控制器输出字典"),
    "ail": ("rad", "尾翼铰链", "控制步后", "目标", "控制器输出字典"),
    "rud": ("rad", "尾翼铰链", "控制步后", "目标", "控制器输出字典"),
    "dfreq": ("Hz", "标量", "控制步后", "目标", "控制器输出字典"),
    "dlag": ("s", "翼弦方向", "控制步后", "目标", "控制器输出字典"),
    "gateL": ("无量纲", "左翼", "拍边界", "目标", "控制器输出字典"),
    "gateR": ("无量纲", "右翼", "拍边界", "目标", "控制器输出字典"),
    "x": ("m", "world 位置", "物理求解后", "实测", "机体状态"),
    "y": ("m", "world 位置", "物理求解后", "实测", "机体状态"),
    "z": ("m", "world 位置", "物理求解后", "实测", "机体状态"),
    "vx": ("m/s", "world 速度", "物理求解后", "实测", "机体速度"),
    "vy": ("m/s", "world 速度", "物理求解后", "实测", "机体速度"),
    "vz": ("m/s", "world 速度", "物理求解后", "实测", "机体速度"),
    "wx": ("rad/s", "world 角速度", "物理求解后", "实测", "机体速度"),
    "wy": ("rad/s", "world 角速度", "物理求解后", "实测", "机体速度"),
    "wz": ("rad/s", "world 角速度", "物理求解后", "实测", "机体速度"),
    "qw": ("无量纲", "body_to_world 四元数 wxyz", "物理求解后",
           "实测", "机体姿态"),
    "qx": ("无量纲", "body_to_world 四元数 wxyz", "物理求解后",
           "实测", "机体姿态"),
    "qy": ("无量纲", "body_to_world 四元数 wxyz", "物理求解后",
           "实测", "机体姿态"),
    "qz": ("无量纲", "body_to_world 四元数 wxyz", "物理求解后",
           "实测", "机体姿态"),
    "roll": ("rad", "world 欧拉姿态", "物理求解后", "实测",
             "由同一 pose 计算"),
    "pitch": ("rad", "world 欧拉姿态", "物理求解后", "实测",
              "由同一 pose 计算"),
    "flapLt": ("rad", "关节角", "驱动步", "目标", "wing_actuation 目标"),
    "flapRt": ("rad", "关节角（镜像符号）", "驱动步", "目标",
               "wing_actuation 目标"),
    "flapLa": ("rad", "关节角", "物理求解后", "实测", "关节位置"),
    "flapRa": ("rad", "关节角（镜像符号）", "物理求解后", "实测",
               "关节位置"),
    "Fwx": ("N", "世界系气动力", "气动力求值后", "模型",
            "三维后端机翼力求值"),
    "Fwy": ("N", "世界系气动力", "气动力求值后", "模型",
             "三维后端机翼力求值"),
    "Fwz": ("N", "世界系气动力", "气动力求值后", "模型",
             "三维后端机翼力求值"),
    # 合计列逐量定义（事项 I-186 返修 v2）：转写自 I-183 冻结字段表气动
    # 组 Fa/Ta 与 Frx/Fry/Frz 条目（number[3]、N、world、已施加的本物理
    # 步合计、authority 为 last_F_world/last_T_world）。
    "Frz": ("N", "世界系竖直合力（每步合计）", "已施加的本物理步合计",
            "模型", "原值台账 Fa 通道合计（I-183 气动组）"),
    "Frx": ("N", "世界系前向合力（每步合计）", "已施加的本物理步合计",
            "模型", "原值台账 Fa 通道合计（I-183 气动组）"),
    "Fry": ("N", "世界系侧向合力（每步合计）", "已施加的本物理步合计",
            "模型", "原值台账 Fa 通道合计（I-183 气动组）"),
    "Twx": ("N·m", "世界系气动力矩", "气动力求值后", "模型",
            "三维后端机翼力求值"),
    "Twy": ("N·m", "世界系气动力矩", "气动力求值后", "模型",
             "三维后端机翼力求值"),
    "Twz": ("N·m", "世界系气动力矩", "气动力求值后", "模型",
             "三维后端机翼力求值"),
    "Fx": ("N", "世界系总气动力", "气动力求值后", "模型",
           "气动施加力求值"),
    "Fy": ("N", "世界系总气动力", "气动力求值后", "模型",
           "气动施加力求值"),
    "Fz": ("N", "世界系总气动力", "气动力求值后", "模型",
           "气动施加力求值"),
    "Mx": ("N·m", "世界系总力矩", "气动力求值后", "模型",
           "气动施加力矩求值"),
    "My": ("N·m", "世界系总力矩", "气动力求值后", "模型",
           "气动施加力矩求值"),
    "Mz": ("N·m", "世界系总力矩", "气动力求值后", "模型",
           "气动施加力矩求值"),
    "pin": ("无量纲", "自由度约束标记", "物理求解后", "实测",
            "物理约束状态"),
    "vslip": ("rad", "侧滑角", "物理求解后", "实测", "由世界速度计算"),
}

# 原值台账通道逐量定义（合法原值单用时的完整说明；引用 I-183 冻结
# 字段表为版本化逐量定义来源）
LEDGER_CHANNEL_META = {
    "bp": ("m 与无量纲", "位置 world；四元数 body_to_world wxyz",
           "每物理步求解后", "实测", "主循环机器人状态（I-183 body 组）"),
    "bv": ("m/s 与 rad/s", "原值及当前状态源 world", "每物理步求解后",
           "实测", "机器人速度（I-183 body 组）"),
    "jp": ("rad", "九关节角", "每物理步求解后", "实测", "关节位置"),
    "jv": ("rad/s", "九关节角速度", "每物理步求解后", "实测", "关节速度"),
    "je": ("N·m", "九关节实测力矩", "每物理步求解后", "实测", "关节力矩"),
    "tgt": ("rad", "九关节位置目标", "驱动步写入", "目标",
            "上帧写入的目标（I-183 joint 组）"),
    "Fa": ("N", "世界系气动施加力", "气动力求值后", "模型", "气动回调"),
    "Ta": ("N·m", "世界系气动施加力矩", "气动力求值后", "模型", "气动回调"),
}


def _quantity_mapping(run_dir, own_ledgers=None):
    """本次实际记录量与规范字段映射（返修五：逐量口径与台账单用）。"""
    common = {
        "definition_source": {"spec": SPEC_REL,
                              "spec_sha256": _sha256_file(SPEC_PATH)},
    }
    streams = sorted(f for f in os.listdir(run_dir)
                     if f.startswith("ident_stream") and f.endswith(".csv"))
    if not streams:
        # 无 CSV 时若存在本次绑定台账，按台账通道给出完整逐量定义
        if own_ledgers:
            maps = []
            for ch, (unit, frame, when, attr, src) in \
                    LEDGER_CHANNEL_META.items():
                maps.append({"channel": ch, "unit": unit, "frame": frame,
                             "sample_time": when, "attribute": attr,
                             "source": src, "status": "present"})
            return dict(common, status="present",
                        basis="ledger_only",
                        reason="无 CSV 数据流；本次绑定原值台账提供完整"
                               "逐量定义（合法原值单用）",
                        experiment_phase_status="missing",
                        flap_phase_status="missing",
                        flap_phase_note="台账无实验阶段与拍相位列，拍内与"
                                        "周期对齐结论受限",
                        mappings=maps,
                        input_refs=[{"path": e["path"],
                                     "sha256": e["sha256"]}
                                    for e in own_ledgers])
        return dict(common, status="missing",
                    reason="运行目录无数据流 CSV 且无可绑定台账，记录量"
                           "缺失（按配方必要性限制正式结论）",
                    experiment_phase_status="missing",
                    flap_phase_status="missing",
                    flap_phase_note="无数据流，拍相位缺失，拍内与周期对齐"
                                   "结论受限",
                    mappings=[])
    path = os.path.join(run_dir, streams[0])
    with open(path, encoding="utf-8") as f:
        cols = next(csv.reader(f))
    present = set(cols)
    mappings = []
    for col in cols:
        meta = COLUMN_META.get(col)
        mappings.append({
            "column": col,
            "field_group": COLUMN_GROUPS.get(col, "unmapped"),
            "status": "present",
            "unit": meta[0] if meta else "unknown",
            "frame": meta[1] if meta else "unknown",
            "sample_time": meta[2] if meta else "unknown",
            "attribute": meta[3] if meta else "unknown",
            "source": meta[4] if meta else "unknown",
        })
    flap_src = [c for c in FLAP_PHASE_COLUMNS if c in present]
    merged = dict(common)
    merged.update({
        "status": "present",
        "basis": "csv_stream",
        "stream": streams[0],
        "stream_sha256": _sha256_file(path),
        "n_columns": len(cols),
        "n_unmapped": sum(1 for m in mappings
                          if str(m["field_group"]).startswith("unmapped")
                          or str(m["field_group"]).startswith("未映射")),
        "experiment_phase_status":
            "present" if "phase" in present else "missing",
        "flap_phase_status": "present" if flap_src else "missing",
        "flap_phase_sources": flap_src,
        "flap_phase_note": (
            "经核实生产来源齐备" if flap_src else
            "本次数据流无经核实的翼拍相位生产来源列（phase 列为实验阶段，"
            "来自 experiment_phase.json），拍内与周期对齐结论受限，不构造"
            "替代值"),
        "mappings": mappings,
        "input_refs": [{"path": path, "sha256": _sha256_file(path)}],
    })
    return merged


def _fin(v):
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def _conditions_deficiencies(conditions):
    """返修五：构包与核验直接验证必要条件字段本身。

    不把采集时写下的 errors/required_missing 清单当充分证明——对旧或
    外部条件文件同样适用：整表缺失、字段不存在、null、非法类型、非有
    限数均返回缺项清单，用于限制数据资格与适用性。复现六锁单列
    （locks），不代替物理锁轴。
    """
    if not isinstance(conditions, dict):
        return ["条件文件缺失或非对象"]
    ec = conditions.get("effective_config")
    if not isinstance(ec, dict):
        return ["生效配置整表缺失"]
    out = []
    classes = ec.get("classes")
    if not isinstance(classes, dict) or not classes:
        out.append("有效参数表整表缺失")
        classes = {}
    for cls, key in REQUIRED_PARAMS:
        tbl = classes.get(cls)
        if not isinstance(tbl, dict):
            out.append("必要参数表缺失：%s" % cls)
            continue
        if key not in tbl:
            out.append("必要参数字段缺失：%s.%s" % (cls, key))
            continue
        v = tbl.get(key)
        if v is None:
            out.append("必要参数为空：%s.%s" % (cls, key))
        elif isinstance(v, bool) or not isinstance(v, (int, float)):
            out.append("必要参数类型非法：%s.%s=%r" % (cls, key, v))
        elif isinstance(v, float) and not math.isfinite(v):
            out.append("必要参数非有限：%s.%s=%r" % (cls, key, v))
    if not isinstance(ec.get("aero_model"), str) \
            or not ec.get("aero_model").strip():
        out.append("气动模型（构型与模型来源）缺失或非法")
    if not isinstance(ec.get("phys_dt"), (int, float)) \
            or isinstance(ec.get("phys_dt"), bool) \
            or not math.isfinite(float(ec.get("phys_dt") or 0)) \
            or float(ec.get("phys_dt") or 0) <= 0:
        out.append("物理步长缺失或非法")
    sw = ec.get("controller_switches")
    if not isinstance(sw, dict) or not sw or "error" in sw:
        out.append("控制器状态（布尔开关清单）缺失或非法")
    sv = conditions.get("source_version")
    if not isinstance(sv, dict) or not sv.get("source_revision"):
        out.append("源码版本（可恢复来源）缺失")
    decl = conditions.get("declared") or {}
    if not isinstance(decl.get("scene"), dict) \
            or not (decl["scene"].get("argv") or []):
        out.append("场景声明（启动命令 argv）缺失；注意启动命令只是声明，"
                   "不充当运行环境版本")
    # 运行环境版本（事项 I-186 接续）：须有子进程内实测的解释器版本读数
    # 与仿真运行环境包版本读数；启动命令不当版本，未安装的包记 Null 加
    # 原因属如实读数，读数结构缺失才判缺项。
    rv = ec.get("runtime_versions")
    if not isinstance(rv, dict) \
            or not isinstance(rv.get("interpreter"), dict) \
            or not str(rv["interpreter"].get("python_version") or "").strip():
        out.append("运行环境版本缺失：无解释器实际版本读数（启动命令不是"
                   "版本）")
    else:
        srs = rv.get("sim_runtimes")
        if not isinstance(srs, list) or not srs:
            out.append("仿真运行环境版本读数缺失（探测清单为空）")
        else:
            for ent in srs:
                if not isinstance(ent, dict) or not ent.get("package"):
                    out.append("仿真运行环境读数条目结构非法：%r" % (ent,))
                elif ent.get("version") is None \
                        and not str(ent.get("reason") or "").strip():
                    out.append("仿真运行环境 %s 既无版本读数也无缺失原因"
                               % ent.get("package"))
    # 初始状态、来流、物理自由度约束：未声明按默认留痕（不硬阻断——
    # 按批准配方必要性，necessary_conditions 声明的键缺失才阻断）
    # 必要条件节（事项 I-186 接续）：批准配方声明的条件键须逐一出现在实
    # 际注入（applied_overrides）；清单只加不减，空清单不豁免以上无条件
    # 必要项。
    nec = decl.get("necessary_conditions")
    if nec is not None:
        if not isinstance(nec, dict):
            out.append("必要条件节声明非法（非对象）")
        elif nec.get("conditions") is not None \
                and not isinstance(nec.get("conditions"), list):
            out.append("必要条件节键 conditions 必须为清单")
    return out


def _resolve_standard(std_src):
    """返修六：解析并核对批准标准来源（file#subject[/criterion_id]）。

    返回 (ok, info)：核文件存在与摘要、科目存在、（若指定）判据条目
    存在、版本（version 或 frozen_at）。不把非空字符串当已绑定。
    """
    info = {"reference": std_src, "file": None, "sha256": None,
            "subject": None, "criterion_id": None, "version": None,
            "problems": []}
    if not (isinstance(std_src, str) and "#" in std_src):
        info["problems"].append("标准来源缺 file#subject 结构：%r" % std_src)
        return False, info
    file_part, rest = std_src.split("#", 1)
    subject, _, crit_id = rest.partition("/")
    path = os.path.join(ROOT, file_part)
    if not os.path.isfile(path):
        info["problems"].append("标准文件不存在：%s" % file_part)
        return False, info
    info["file"] = file_part
    info["sha256"] = _sha256_file(path)
    info["subject"] = subject
    info["criterion_id"] = crit_id or None
    try:
        doc = _load_json(path)
    except Exception as exc:
        info["problems"].append("标准文件读取失败：%r" % exc)
        return False, info
    info["version"] = doc.get("version") or doc.get("frozen_at")         or str(doc.get("schema_version"))
    subs = doc.get("subjects") or {}
    sub = subs.get(subject)
    if not isinstance(sub, dict):
        info["problems"].append("标准文件无科目：%s" % subject)
        return False, info
    if info["criterion_id"]:
        hit = [c for c in (sub.get("criteria") or [])
               if c.get("id") == info["criterion_id"]]
        if not hit:
            info["problems"].append("科目 %s 无判据条目 %s"
                                     % (subject, info["criterion_id"]))
            return False, info
        info["threshold_object"] = hit[0].get("threshold") or {}
    else:
        info["threshold_object"] = None
    return True, info


# 生产格式实测键适配（事项 I-186 接续）：登记册单阈值键条目的指标名到
# bench_runner 悬停判定器 measured 字典键的对应；来源为生产代码
# modules/backend/bench_runner.py judge_hover 的实测字典键，非字段名猜
# 测。多被测量条目（bindings）自带 measured_key，不经本表。
PRODUCER_MEASURED_KEYS = {
    "survive_duration": "survive_s",
    "lift_to_weight_ratio": "lift_ratio",
}

CRITERIA_FILE_REL = "config/bench_criteria.json"


def _subject_criteria_ids(subject):
    """读冻结判据文件某科目的判据条目编号清单；不可读返回 None。"""
    try:
        doc = _load_json(os.path.join(ROOT, CRITERIA_FILE_REL))
    except Exception:
        return None
    sub = ((doc.get("subjects") or {}).get(subject)
           if isinstance(doc, dict) else None)
    if not isinstance(sub, dict) or not isinstance(sub.get("criteria"), list):
        return None
    return [c.get("id") for c in sub["criteria"]
            if isinstance(c, dict) and c.get("id")]


def _semantics_bindings(sem):
    """登记册条目展开为被测量绑定列表。

    单阈值键条目经生产实测键适配（indicator 到 measured 键）；多被测量
    条目直接用 bindings（每项自带 measured_key 与 threshold_key）。无可用
    绑定返回 None，调用方按待审处理，不猜键名。
    """
    if not isinstance(sem, dict):
        return None
    if isinstance(sem.get("bindings"), list) and sem["bindings"]:
        out = []
        for b in sem["bindings"]:
            if not isinstance(b, dict) or not b.get("measured_key") \
                    or not b.get("threshold_key") or not b.get("direction"):
                return None
            out.append(b)
        return out
    if sem.get("threshold_key") and sem.get("direction"):
        mk = PRODUCER_MEASURED_KEYS.get(sem.get("indicator"))
        if not mk:
            return None
        return [{"quantity": sem.get("indicator"), "measured_key": mk,
                 "threshold_key": sem.get("threshold_key"),
                 "direction": sem.get("direction"),
                 "unit": sem.get("unit"),
                 "unit_equivalents": sem.get("unit_equivalents") or []}]
    return None


def _producer_aspects(sub, nested, crit_file, frozen):
    """把生产格式逐条嵌套结果按批准测量语义展开为逐被测量切面。

    生产格式为 bench_runner write_scores_json 的 subjects[].criteria[]：
    每条含 id、status、measured 数值字典与 threshold 数值字典。切面的比
    较方向、阈值键、单位一律取自登记册批准绑定（转写自标准 text 原文），
    不取结果文件自报；登记册缺条目或缺可映射绑定时给待审切面，不虚构。
    """
    subject = sub.get("key")
    aspects = []
    for c in nested:
        if not isinstance(c, dict):
            aspects.append({"criterion_id": None, "quantity": None,
                            "result_format": "producer_nested",
                            "verdict_word": None,
                            "aspect_problem": "生产结果判据条目结构非法："
                                              "%r" % (c,)})
            continue
        cid = c.get("id") if isinstance(c.get("id"), str) else None
        base = {"criterion_id": cid, "quantity": None,
                "result_format": "producer_nested",
                "verdict_word": c.get("status"),
                "criterion_version": frozen}
        if not cid:
            aspects.append(dict(base, aspect_problem="生产结果判据条目缺"
                                "编号（id），无法绑定批准标准"))
            continue
        # 错条目先核对科目判据集合：科目内不存在的编号直接给出准确原因，
        # 不进入登记册语义步骤。
        subject_ids = _subject_criteria_ids(subject)
        if subject_ids is not None and cid not in subject_ids:
            aspects.append(dict(base, aspect_problem=(
                "科目 %s 无判据条目 %s（错条目）：结果文件条目不在批准科"
                "目的判据清单内" % (subject, cid))))
            continue
        sem_ref = "%s#%s/%s" % (crit_file, subject, cid)
        base["standard_source"] = sem_ref
        try:
            sem_doc = _load_json(os.path.join(ROOT, SEMANTICS_REL))
            sem = (sem_doc.get("entries") or {}).get(sem_ref)
        except Exception:
            sem = None
        binds = _semantics_bindings(sem) if sem is not None else None
        measured = c.get("measured") \
            if isinstance(c.get("measured"), dict) else {}
        thr = c.get("threshold") \
            if isinstance(c.get("threshold"), dict) else {}
        if sem is None:
            aspects.append(dict(base, aspect_problem=(
                "生产条目 %s 缺批准结构化测量定义（登记册无条目 %s），待审"
                "不得通过" % (cid, sem_ref))))
            continue
        if binds is None:
            aspects.append(dict(base, aspect_problem=(
                "批准条目 %s 的结构化定义无法映射到生产实测键（指标 %r 无"
                "生产实测键适配，也无 bindings），待审不得通过"
                % (cid, sem.get("indicator")))))
            continue
        for b in binds:
            aspects.append(dict(
                base, quantity=b.get("quantity"),
                value=measured.get(b.get("measured_key")),
                threshold=thr.get(b.get("threshold_key")),
                comparison=b.get("direction"),
                unit=b.get("unit"),
                unit_equivalents=b.get("unit_equivalents") or [],
                standard_threshold_key=b.get("threshold_key"),
                measured_key=b.get("measured_key")))
    return aspects


def _flat_aspect(sub, crit_file):
    """科目层单标量结果切面（自检与既有格式）：数值字段取结果文件自报，
    保留原始类型供类型检查；判据编号自标准来源引用解析。"""
    std_src = sub.get("standard_source")
    if not (isinstance(std_src, str) and std_src.strip()):
        std_src = crit_file
    cid = None
    if isinstance(std_src, str) and "#" in std_src:
        cid = std_src.split("#", 1)[1].partition("/")[2] or None
    return {"criterion_id": cid, "quantity": None,
            "result_format": "subject_scalar",
            "value": sub.get("value"), "unit": sub.get("unit"),
            "threshold": sub.get("threshold"),
            "comparison": sub.get("comparison"),
            "criterion_version": sub.get("criterion_version"),
            "verdict_word": sub.get("verdict"),
            "standard_source": std_src,
            "standard_threshold_key": sub.get("standard_threshold_key")}


def _evaluate_criterion(entry, a):
    """单条判据的完整校验链（两种结果格式共用）。

    链序：非有限值、字段齐备与类型、批准标准来源绑定、版本一致、阈值键
    与数值绑定、测量语义（方向、单位等价）、值阈值与结论字样矛盾复核。
    结论保留原检查入口字样，本包只做独立复核与阻断。
    """
    value_raw = a.get("value")
    if isinstance(value_raw, float) and not math.isfinite(value_raw):
        st = _status("criterion", "unknown",
                     "实测值为非有限数（%r），规范输出记 null，不得正式通"
                     "过" % value_raw)
        st.update(entry)
        return st
    verdict = a.get("verdict_word")
    std_src = a.get("standard_source")
    entry["standard_source"] = std_src
    missing = [k for k in ("value", "unit", "threshold", "comparison",
                           "criterion_version")
               if entry.get(k) in (None, "")]
    # 单位与版本须为非空字符串——空集合、字典等非法类型按缺失处理。
    for k in ("unit", "criterion_version"):
        v = a.get(k)
        if not (isinstance(v, str) and v.strip()):
            if k not in missing:
                missing.append("%s（类型非法 %r）" % (k, v))
    if missing:
        st = _status("criterion", "unknown",
                     "科学字段缺失（%s），必要字段缺失不得通过"
                     % "、".join(missing))
        st.update(entry)
        return st
    ok_std, std = _resolve_standard(std_src)
    entry["standard"] = std
    if not ok_std:
        st = _status("criterion", "unknown",
                     "批准标准来源未绑定（%s）不得正式通过"
                     % "；".join(std["problems"]))
        st.update(entry)
        return st
    # 版本一致：结果的判据版本须等于标准文件的冻结版本
    if str(entry["criterion_version"]) != str(std["version"]):
        st = _status("criterion", "unknown",
                     "判据版本 %r 与标准版本 %r 不符"
                     % (entry["criterion_version"], std["version"]))
        st.update(entry)
        return st
    # 阈值数字绑定：条目须绑定所用标准阈值键，且数值与标准一致
    thr_key = a.get("standard_threshold_key")
    thr_obj = std.get("threshold_object")
    if not (isinstance(thr_key, str) and thr_key.strip()):
        st = _status("criterion", "unknown",
                     "未声明所用标准阈值键（standard_threshold_key），数字"
                     "判据未绑定，不得正式通过")
        st.update(entry)
        return st
    if not isinstance(thr_obj, dict) or thr_key not in thr_obj:
        st = _status("criterion", "unknown",
                     "标准阈值键 %r 不在科目判据条目中" % thr_key)
        st.update(entry)
        return st
    std_val = thr_obj[thr_key]
    if not _fin(std_val) or float(std_val) != float(entry["threshold"]):
        st = _status("criterion", "unknown",
                     "阈值 %s 与标准 %s（%s=%s）不符，不得另选阈值"
                     % (entry["threshold"], std["file"], thr_key, std_val))
        st.update(entry)
        return st
    entry["standard_threshold_key"] = thr_key
    entry["standard_threshold_value"] = std_val
    # 测量语义绑定（登记册转写自标准 text 原文；缺结构化定义按待审）
    sem_ref = "%s#%s/%s" % (std["file"], std["subject"], std["criterion_id"])
    try:
        sem_doc = _load_json(os.path.join(ROOT, SEMANTICS_REL))
        sem_all = sem_doc.get("entries") or {}
        sem_sha = _sha256_file(os.path.join(ROOT, SEMANTICS_REL))
    except Exception as exc:
        sem_all, sem_sha = {}, None
        entry["semantics_error"] = "登记册读取失败：%r" % exc
    sem = sem_all.get(sem_ref)
    entry["semantics"] = {
        "registry": SEMANTICS_REL,
        "registry_sha256": sem_sha,
        "reference": sem_ref,
        "entry": sem,
    } if sem is not None else {
        "registry": SEMANTICS_REL,
        "registry_sha256": sem_sha,
        "reference": sem_ref,
        "entry": None,
    }
    if sem is None:
        st = _status("criterion", "unknown",
                     "批准标准 %s 缺结构化测量定义（指标/方向/单位待审），"
                     "不得正式通过" % sem_ref)
        st.update(entry)
        return st
    if isinstance(sem.get("bindings"), list) and sem["bindings"] \
            and a.get("result_format") == "subject_scalar":
        st = _status("criterion", "unknown",
                     "批准条目 %s 宣告 %d 个被测量（bindings 逐被测量转"
                     "写），科目层单标量结果无法逐被测量对应；须提供逐条"
                     "生产格式结果（subjects[].criteria[].measured）"
                     % (sem_ref, len(sem["bindings"])))
        st.update(entry)
        return st
    # 方向与单位等价集来源：生产格式切面取批准绑定展开值；科目层单标量
    # 切面取条目平铺定义并与结果文件自报方向单位比对。
    if a.get("result_format") == "producer_nested":
        sem_direction = a.get("comparison")
        unit_equivalents = a.get("unit_equivalents") or []
    else:
        sem_direction = sem.get("direction")
        unit_equivalents = sem.get("unit_equivalents") or []
    entry["indicator"] = sem.get("indicator") or a.get("quantity")
    if entry["comparison"] != sem_direction:
        st = _status("criterion", "fail",
                     "比较方向 %r 与批准定义 %r 不符（批准措辞见标准 "
                     "text），不得另选有利方向"
                     % (entry["comparison"], sem_direction))
        st.update(entry)
        return st
    unit_norm = str(entry["unit"]).strip()
    if unit_norm not in [str(u).strip() for u in unit_equivalents]:
        st = _status("criterion", "fail",
                     "单位 %r 与批准指标定义不符（%s 为%s，等价表示 %s），"
                     "未知单位不得正式通过"
                     % (entry["unit"], entry["indicator"],
                        sem.get("unit") or a.get("unit"),
                        "、".join(unit_equivalents)))
        st.update(entry)
        return st
    fn = CMP_FUNCS.get(entry["comparison"])
    if fn is None:
        st = _status("criterion", "unknown",
                     "比较规则 %r 不在批准规则集内" % entry["comparison"])
        st.update(entry)
        return st
    cmp_ok = fn(entry["value"], entry["threshold"])
    if verdict == "pass" and not cmp_ok:
        st = _status("criterion", "fail",
                     "值 %s 与阈值 %s 在规则 %s 下矛盾于通过字样：已知矛盾"
                     "不得通过" % (entry["value"], entry["threshold"],
                                    entry["comparison"]))
    elif verdict == "fail" and cmp_ok:
        st = _status("criterion", "unknown",
                     "值满足规则但结果字样为 fail：字样与测量矛盾，保留原"
                     "检查入口结论待核")
    elif verdict not in ("pass", "fail"):
        st = _status("criterion", "unknown",
                     "结果状态字 %r 非通过或不通过字样，不作为正式结论（原"
                     "文保留待核）" % (verdict,))
    else:
        st = _status("criterion",
                     {"pass": "pass", "fail": "fail"}.get(verdict,
                                                          "unknown"),
                     "verdict=%s 且与值阈值规则一致" % verdict)
    st.update(entry)
    return st


def _criteria(record, run_dir, identity, own_ledgers):
    """逐条科学判据（L1 接续补齐：整科必需集合由科目声明决定、生产逐条
    嵌套格式直接消费、重复与错条目出口、单判据批准范围；身份与原始输入
    哈希绑定沿用返修三版）。"""
    crit_cfg = ((record or {}).get("config") or {}).get("criteria") or {}
    if not crit_cfg:
        return [_status("criterion", "missing", "运行记录未声明判据配置")]
    kind = crit_cfg.get("kind")
    declared = crit_cfg.get("subject")
    declared_criterion = crit_cfg.get("criterion")
    files = [f for f in os.listdir(run_dir) if f.endswith("_scores.json")]
    if not files:
        return [dict(_status("criterion", "not_executed",
                             "运行目录无机器判据结果产物"),
                     subject_declared=declared, subject_actual=None)]
    raw_hashes = {ent["sha256"] for ent in own_ledgers}
    streams = sorted(f for f in os.listdir(run_dir)
                     if f.startswith("ident_stream") and f.endswith(".csv"))
    if streams:
        raw_hashes.add(_sha256_file(os.path.join(run_dir, streams[0])))

    # 整科必需判据集合：科目声明决定（判据先于数据），不由结果文件自选
    # 子集。bench_subject 要求该科目全部条目；bench_criterion 收窄为声明
    # 条目（结论记录单判据范围，不代称整科）。
    required = None
    if kind == "bench_subject" and isinstance(declared, str):
        required = _subject_criteria_ids(declared)
        if required is None:
            return [dict(_status("criterion", "unknown",
                                 "整科必需判据集合无法核对（判据文件 %s 不"
                                 "可读或科目 %s 结构不符），整科不得通过"
                                 % (CRITERIA_FILE_REL, declared)),
                         subject_declared=declared)]
    elif kind == "bench_criterion" and isinstance(declared_criterion, str):
        required = [declared_criterion]

    out = []
    seen_keys = {}
    for f in files:
        try:
            data = _load_json(os.path.join(run_dir, f))
        except Exception as exc:
            out.append(dict(_status("criterion", "unknown",
                                    "结果文件读取失败：%r" % exc),
                            subject_declared=declared, source_file=f))
            continue
        rid_exp = data.get("experiment_id")
        rid_run = data.get("run_id")
        rid_item = data.get("item")
        binds = []
        exp_self = (identity.get("experiment_id") or {}).get("value")
        run_self = (identity.get("run_id") or {}).get("value")
        item_self = (identity.get("item_id") or {}).get("value")
        # 事项、实验、运行标识为必需——缺失即阻断；事项允许经登记的合法
        # 别名（规范化后一致）。
        aliases = {str(a).strip() for a in
                   ((record or {}).get("item_aliases") or [])}
        if item_self is not None:
            aliases.add(str(item_self).strip())
        for label, got in (("实验标识", rid_exp), ("运行标识", rid_run),
                           ("事项标识", rid_item)):
            if got in (None, ""):
                binds.append("结果缺%s（必需标识，不能仅当不为空才比较）"
                             % label)
        if rid_exp is not None and exp_self is not None \
                and str(rid_exp).strip() != str(exp_self).strip():
            binds.append("结果实验标识 %r 与本次 %r 不符"
                         % (rid_exp, exp_self))
        if rid_run is not None and run_self is not None \
                and str(rid_run).strip() != str(run_self).strip():
            binds.append("结果运行标识 %r 与本次 %r 不符"
                         % (rid_run, run_self))
        if rid_item is not None and item_self is not None \
                and str(rid_item).strip() not in aliases:
            binds.append("结果事项标识 %r 与本次 %r 不符且非登记别名"
                         % (rid_item, item_self))
        analyzed = data.get("analyzed_inputs") or []
        if not analyzed:
            binds.append("结果文件未声明分析输入（漏来源），不能证明其分"
                         "析了本次数据或批准标准")
        else:
            for ent in analyzed:
                if not isinstance(ent, dict):
                    binds.append("分析输入条目结构非法：%r" % (ent,))
                    continue
                h = ent.get("sha256")
                ref = ent.get("file")
                if h in raw_hashes:
                    continue
                # 仓库内批准标准定义文件：按当前内容核哈希（标准属仓库冻
                # 结件，内容变动即来源篡改，阻断）；路径须落在仓库 config
                # 目录内，防目录穿越。
                if isinstance(ref, str) and ref.startswith("config/") \
                        and len(ref) > len("config/") \
                        and os.path.normpath(
                            os.path.join(ROOT, ref)
                        ).startswith(os.path.join(ROOT, "config") + os.sep):
                    p = os.path.join(ROOT, ref)
                    if os.path.isfile(p) and _sha256_file(p) == h:
                        continue
                    binds.append("分析输入 %s 非本次原始输入，且与仓库标准"
                                 "定义文件当前内容不符" % ref)
                else:
                    binds.append("分析输入哈希 %s 不属于本次运行的原始输入"
                                 % str(h)[:12])
        # 测量科目须绑定本次实际分析数据（返修 v2）：analyzed_inputs 至少
        # 一项哈希属于本次运行的原始输入（本目录已绑定台账或数据流）；
        # 仅登记标准定义不能证明分析用了本次数据，不给其他运行结果改挂
        # 本次身份后放行。
        if kind in ("bench_subject", "bench_criterion"):
            has_run_data = any(
                isinstance(ent, dict) and ent.get("sha256") in raw_hashes
                for ent in analyzed)
            if not has_run_data:
                binds.append("结果仅登记标准定义或外来输入，未绑定本次实"
                             "际分析数据（测量科目须实际读取本次原始记录"
                             "或可追溯派生产物并留内容对应）")
        crit_file = data.get("criteria_file")
        if not (isinstance(crit_file, str) and crit_file.strip()):
            crit_file = CRITERIA_FILE_REL
        frozen = data.get("criteria_frozen_at")
        for sub in data.get("subjects") or []:
            if not isinstance(sub, dict):
                out.append(dict(_status("criterion", "unknown",
                                        "结果科目条目结构非法：%r" % (sub,)),
                                subject_declared=declared, source_file=f))
                continue
            actual = sub.get("key")
            nested = sub.get("criteria")
            nested = nested if isinstance(nested, list) and nested else None
            if declared is not None and actual is not None \
                    and str(actual) != str(declared):
                out.append(dict(
                    _status("criterion", "not_executed",
                            "结果科目 %r 与本次声明科目 %r 不符：外来结果"
                            "不作本次判据证据" % (actual, declared)),
                    subject_declared=declared, subject_actual=actual,
                    source_file=f))
                continue
            if nested:
                aspects = _producer_aspects(sub, nested, crit_file, frozen)
            else:
                aspects = [_flat_aspect(sub, crit_file)]
            for a in aspects:
                cid = a.get("criterion_id")
                # 单判据批准范围：范围外条目如实记不适用，不参与本次判
                # 定，也不据此增减结论（批准范围明确才记录自身结论）。
                # 仅当条目属于声明科目的真实判据清单时适用；科目不存在
                # 的错条目仍走校验链，由标准来源核对阻断。
                if kind == "bench_criterion" and required is not None \
                        and isinstance(cid, str) and cid not in required \
                        and cid in (_subject_criteria_ids(declared) or []):
                    out.append(dict(
                        _status("criterion", "not_applicable",
                                "结果条目 %s 超出本次声明的单判据批准范围"
                                "（%s/%s），不参与本次判定" % (
                                    cid, declared, declared_criterion)),
                        subject_declared=declared, subject_actual=actual,
                        criterion_id=cid, quantity=a.get("quantity"),
                        source_file=f))
                    continue
                entry = {
                    "subject_declared": declared,
                    "subject_actual": actual,
                    "criterion_id": cid,
                    "quantity": a.get("quantity"),
                    "value": a.get("value") if _fin(a.get("value")) else None,
                    "unit": a.get("unit"),
                    "threshold": (a.get("threshold")
                                  if _fin(a.get("threshold")) else None),
                    "comparison": a.get("comparison"),
                    "criterion_version": a.get("criterion_version"),
                    "verdict_word": a.get("verdict_word"),
                    "result_identity": {"experiment_id": rid_exp,
                                        "run_id": rid_run},
                    "input_source": {"file": f,
                                     "sha256": _sha256_file(
                                         os.path.join(run_dir, f))},
                    "result_format": a.get("result_format"),
                }
                k = (str(actual), str(cid), str(a.get("quantity")))
                seen_keys[k] = seen_keys.get(k, 0) + 1
                if binds:
                    st = _status("criterion", "unknown",
                                 "结果与本次运行绑定失败：" + "；".join(binds))
                    st.update(entry)
                    out.append(st)
                    continue
                if a.get("aspect_problem"):
                    st = _status("criterion", "unknown", a["aspect_problem"])
                    st.update(entry)
                    out.append(st)
                    continue
                out.append(_evaluate_criterion(entry, a))
    # 重复条目：同一（科目、判据、被测量）出现多次即证据歧义，已判条目
    # 改判未知，不得据此通过。
    for st in out:
        k = (str(st.get("subject_actual")), str(st.get("criterion_id")),
             str(st.get("quantity")))
        if seen_keys.get(k, 0) > 1 and st.get("status") in ("pass", "fail"):
            st["status"] = "unknown"
            st["reason"] = ("判据条目 %s（被测量 %s）在结果文件中重复出现 "
                            "%d 次，证据歧义不得通过；原判语 %s"
                            % (st.get("criterion_id"), st.get("quantity"),
                               seen_keys[k], st.get("reason")))
    # 整科缺项：必需集合中未出现在任何结果文件的条目补 missing，阻断整
    # 科通过（缺项、未执行、未知不得整科 pass）。
    if required:
        present = {str(st.get("criterion_id")) for st in out
                   if st.get("status") != "not_applicable"}
        for cid in required:
            if str(cid) not in present:
                out.append(dict(
                    _status("criterion", "missing",
                            "必需判据条目 %s 未出现在任何结果文件；科目声明"
                            "要求的必需集合为 %s，必需集合由科目声明决定，"
                            "不能由结果文件自选子集代称整科"
                            % (cid, "、".join(str(x) for x in required))),
                    subject_declared=declared, subject_actual=declared,
                    criterion_id=cid))
    return out


def assess_execution(record):
    if not isinstance(record, dict) or not record:
        return _status("execution", "unknown", "无运行记录")
    if record.get("timed_out"):
        return _status("execution", "timed_out", "运行超时")
    comp = record.get("completeness") or {}
    if record.get("exit_code") == 0 and comp.get("complete"):
        return _status("execution", "completed",
                       "退出码零且按收尾规则完整（%s）" % comp.get("rule"))
    if record.get("exit_code") == 0:
        return _status("execution", "unknown", "退出码零但完成性未确认")
    return _status("execution", "failed", "退出码 %s"
                   % record.get("exit_code"))


# HQ 正式批准的必要清单记录锚点（事项 I-186 返修 v3）。批准记录位于
# I-186 云账本 reply（前缀【HQ批准必要清单】，批准人 HQ-Anni，2026-10-14
# 23:03:51），回读证据
# logs/hq_intake/20261014_mainline_recovery/i186_l1_rework_v3/approval_readback.json。
# 本锚点只登记批准身份与内容摘要供机器核对，不构成重新批准；清单文件内
# 容变动（哈希不符）即阻断，存在、authority 字符串或 version 均不能自行
# 证明批准。
APPROVED_REQUIRED = {
    "record_id": "HQ-I186-HOVER-REQUIRED-20261014-V1",
    "file": ("logs/hq_intake/20261014_mainline_recovery/"
             "i186_required_hover_v1/required_manifest.json"),
    "sha256": "19723b3355acc5d3399859acd4bf1e5f842f1de3d7c8d0f39fe"
              "27a0415dad0aa",
    "version": "I186-HOVER-REQUIRED-v1",
    "authority": "HQ-Anni",
    "profiles": ["hover_csv", "hover_ledger"],
    "approval_readback": ("logs/hq_intake/20261014_mainline_recovery/"
                          "i186_l1_rework_v3/approval_readback.json"),
}


def _resolve_approved_manifest(source):
    """解析必要清单来源对正式批准记录的绑定（事项 I-186 返修 v3）。

    返回 (问题或 None, info)。仅当来源引用批准锚点登记的文件且当前内容
    哈希、版本与 record_id 均与锚点一致时通过；引用其他文件（含自带版
    本标记或自称 authority 的同形文件）、内容被改、删清单或片段（分支）
    不在批准范围时返回问题，调用方按待审阻断。可选 #分支 片段须与按实
    际记录类型检出的分支一致（错分支阻断；有 CSV 记录不得用台账分支豁
    免 CSV 必需量）。
    """
    info = {"reference": source, "manifest_path": None, "manifest_sha256":
            None, "branch_declared": None, "branch_detected": None,
            "manifest": None}
    if not (isinstance(source, str) and source.strip()):
        return "来源未声明（空引用）", info
    if "#" in source:
        file_part, frag = source.split("#", 1)
        info["branch_declared"] = frag or None
    else:
        file_part, frag = source, None
    info["manifest_path"] = file_part
    if file_part != APPROVED_REQUIRED["file"]:
        return ("来源文件不是当前批准记录登记的必要清单（批准记录 %s 登"
                "记 %s）；存在、自带版本标记或自称批准的同形文件不构成批"
                "准" % (APPROVED_REQUIRED["record_id"],
                        APPROVED_REQUIRED["file"])), info
    path = os.path.join(ROOT, file_part)
    if not os.path.isfile(path):
        return "批准清单文件不存在：%s" % file_part, info
    digest = _sha256_file(path)
    info["manifest_sha256"] = digest
    if digest != APPROVED_REQUIRED["sha256"]:
        return ("批准清单内容与批准记录不符（当前 sha256 %s，批准记录 "
                "%s）：内容被改或删改清单不得正式通过"
                % (digest[:12], APPROVED_REQUIRED["sha256"][:12])), info
    try:
        doc = _load_json(path)
    except Exception as exc:
        return "批准清单文件不可读：%r" % exc, info
    if not isinstance(doc, dict) \
            or doc.get("record_id") != APPROVED_REQUIRED["record_id"] \
            or doc.get("version") != APPROVED_REQUIRED["version"]:
        return ("批准清单身份与锚点不符（record_id 或 version），不得正"
                "式通过"), info
    info["manifest"] = doc
    declared = info["branch_declared"]
    if declared is not None and declared not in APPROVED_REQUIRED["profiles"]:
        return ("声明分支 %s 不在批准范围（批准分支：%s）"
                % (declared, "、".join(APPROVED_REQUIRED["profiles"]))), info
    return None, info


def _detect_record_branch(run_dir):
    """按本次实际记录类型检出分支：有 CSV 数据流用 hover_csv（不得以台账
    分支豁免 CSV 必需量），仅原值台账用 hover_ledger；两者皆无返回 None。"""
    try:
        names = os.listdir(run_dir)
    except OSError:
        return None
    has_csv = any(f.startswith("ident_stream") and f.endswith(".csv")
                  for f in names)
    has_ledger = any(f.startswith("fw_ledger_") and f.endswith(".bin")
                     for f in names)
    if has_csv:
        return "hover_csv"
    if has_ledger:
        return "hover_ledger"
    return None


def _load_scene_witness(run_dir, record):
    """读同运行身份绑定的场景见证（返修 v4）。

    返回 (witness 或 None, 拒绝原因或 None)：见证文件缺失、不可读、身
    份与运行记录不符（外来见证）或结构非法时返回 None 加原因——实际事
    实不得由批准场景代填，缺见证即缺来源。"""
    path = os.path.join(run_dir, "scene_witness.json")
    if not os.path.isfile(path):
        return None, "运行目录无场景见证（scene_witness.json）"
    try:
        doc = _load_json(path)
    except Exception as exc:
        return None, "场景见证不可读：%r" % exc
    if not isinstance(doc, dict) or not isinstance(doc.get("facts"), dict):
        return None, "场景见证结构非法"
    rec = record or {}
    if str(doc.get("experiment_id")) != str(rec.get("experiment_id")) \
            or str(doc.get("run_id")) != str(rec.get("run_id")):
        return None, ("场景见证身份 %s/%s 与本次运行 %s/%s 不符：外来见"
                      "证不作本次证据"
                      % (doc.get("experiment_id"), doc.get("run_id"),
                         rec.get("experiment_id"), rec.get("run_id")))
    return doc, None


def _witness_value(field):
    """见证字段取值：{"value": ...} 包装取 value，裸值原样，None 保持。"""
    if isinstance(field, dict):
        return field.get("value")
    return field


def _required_facts(conditions, record, quantities, crits, branch, run_dir):
    """按批准清单 condition_definitions 逐项收集实际事实（返修 v4）。

    事实来源逐项绑定同一次执行：源码与配置读数来自起始采集（子进程生
    效快照与实际注入），身份/步骤/完成性来自运行记录，时基来自原始记
    录（CSV t 列或台账步号加步长），实际初始化/释放、来流、物理约束与
    控制器事件事实来自同运行身份绑定的场景见证（数据生产侧最小采集入
    口 modules/experiment_pipeline/scene_witness.py 落档）。批准场景只
    作对照不作来源；配置读数只证明配置不证明动作已执行；无环境覆盖不
    等于无参数，缺见证即缺事实（unknown，点名缺项）。人工接口结构链
    如实声明无物理事件，不冒充飞行。
    """
    ec = (conditions or {}).get("effective_config") or {}
    sv = (conditions or {}).get("source_version") or {}
    applied = (conditions or {}).get("applied_overrides") or {}
    rv = ec.get("runtime_versions") or {}
    cfg = (record or {}).get("config") or {}
    tags = cfg.get("tags") or {}
    witness, witness_problem = _load_scene_witness(run_dir, record)
    wf = (witness or {}).get("facts") or {}
    steps = (record or {}).get("steps") or []
    # 数据观察：时基覆盖（CSV 用 t 列首末，台账用实际步号加步长）
    coverage = None
    if branch == "hover_csv":
        for ref in (quantities or {}).get("input_refs") or []:
            p = ref.get("path")
            if p and os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        reader = csv.reader(fh)
                        cols = next(reader, None)
                        if cols and "t" in cols:
                            ti = cols.index("t")
                            first = last = None
                            for line in reader:
                                if len(line) <= ti:
                                    continue
                                try:
                                    tv = float(line[ti])
                                except ValueError:
                                    continue
                                if first is None:
                                    first = tv
                                last = tv
                            if first is not None:
                                coverage = {"t_first_s": first,
                                            "t_last_s": last,
                                            "basis": "CSV t 列实际首末行"}
                except (OSError, StopIteration):
                    pass
                break
    elif branch == "hover_ledger":
        for ref in (quantities or {}).get("input_refs") or []:
            p = ref.get("path")
            if p and os.path.isfile(p) and os.path.basename(p).startswith(
                    "fw_ledger_"):
                try:
                    sys.path.insert(0, ROOT)
                    from modules import fw_iid_ledger as _L
                    hdr, arr = _L.load(p)
                    n = int(arr.shape[0]) if hasattr(arr, "shape") else 0
                    dt = hdr.get("phys_dt")
                    coverage = {"step_first": 1, "step_last": n,
                                "physics_dt_s": dt,
                                "t_first_s": dt if dt else None,
                                "t_last_s": (n * dt) if dt else None,
                                "basis": "台账实际步号与头内步长（同运行"
                                         "实际时基绑定）"}
                except Exception:
                    pass
                break
    # 分析输入事实：来自逐条判据的输入来源与标准绑定
    analysis_std = analysis_meas = None
    producer = None
    for c in crits or []:
        src = (c.get("input_source") or {})
        if src.get("file") and analysis_meas is None:
            analysis_meas = {"file": src.get("file"),
                             "sha256": src.get("sha256")}
        std = c.get("standard") or {}
        if std.get("file") and std.get("sha256") and analysis_std is None:
            analysis_std = {"file": std.get("file"),
                            "sha256": std.get("sha256")}
        if producer is None and c.get("source_tool"):
            producer = {"tool": c.get("source_tool"),
                        "version": c.get("source_version")}
    uncommitted = sv.get("uncommitted_patch")
    interp = rv.get("interpreter") or {}
    facts = {
        "source": {
            "source_revision": sv.get("source_revision"),
            "uncommitted_content_or_clean_proof": (
                {"patch": sv.get("uncommitted_patch"),
                 "sha256": sv.get("uncommitted_patch_sha256")}
                if uncommitted else
                ("工作树干净（无未提交差异）" if sv.get("dirty") is False
                 else None)),
        },
        "run": {
            "experiment_id": (record or {}).get("experiment_id"),
            "item_id": (record or {}).get("item"),
            "run_id": (record or {}).get("run_id"),
            "execution_steps": (
                [{"name": s.get("name"), "mode": s.get("mode"),
                  "complete": s.get("complete"),
                  "env_witness": bool(s.get("env_witness"))}
                 for s in steps] or None),
            "execution_mode": (record or {}).get("mode")
            or (cfg.get("scene") or {}).get("mode"),
            "completion_status": ((record or {}).get("completeness")
                                  or {}).get("complete"),
            "data_origin": tags.get("desc") or None,
        },
        "clock": {
            "time_origin_s": (coverage or {}).get("t_first_s"),
            "physics_dt_s": ec.get("phys_dt"),
            "sample_steps_or_times": coverage,
            "run_end_s": (coverage or {}).get("t_last_s"),
        },
        "runtime": {
            "executable": interp.get("executable")
            or ((steps[0].get("env_witness") or {}).get("interpreter")
                if steps else None),
            "interpreter_version": interp.get("python_version"),
            "used_runtime_package_versions": rv.get("sim_runtimes"),
        },
        "model": {
            "backend": ((record or {}).get("mode")
                        or (cfg.get("scene") or {}).get("mode")),
            "aero_model": ec.get("aero_model"),
            "aero_model_source_revision": sv.get("source_revision"),
            "actual_model_parameters": {
                "classes": ec.get("classes"),
                "mass_kg": ec.get("mass_kg"),
            },
        },
        "configuration": {
            "config_content_hash": (record or {}).get("config_file_sha256"),
            "actual_geometry_and_mass_parameters": {
                "mass_kg": ec.get("mass_kg"),
                "launch_pitch_rad": ec.get("launch_pitch_rad"),
                "dp_target_z_m": ec.get("dp_target_z_m"),
                "WingGeometryConfig": (ec.get("classes") or {}).get(
                    "WingGeometryConfig"),
                "TailAeroConfig": (ec.get("classes") or {}).get(
                    "TailAeroConfig"),
                "FlapKinematicsConfig": (ec.get("classes") or {}).get(
                    "FlapKinematicsConfig"),
            },
            "actual_parameter_sources": (
                "config/sim_config.py 子进程生效快照实读（含桥覆盖）；默认"
                "值为当前仓库实际读数，非抄计划"
                if ec.get("snapshot_mode") == "child_effective" else None),
        },
        "initial_state": dict(wf.get("initial_state") or {}),
        "inflow": dict(wf.get("inflow") or {}),
        "physical_constraints": dict(wf.get("physical_constraints") or {}),
        "controller": {
            "controller_source_revision": sv.get("source_revision"),
            "effective_switches": ec.get("controller_switches"),
            "effective_parameters": (wf.get("controller") or {}).get(
                "effective_parameters"),
            "mode_and_reset_state": (wf.get("controller") or {}).get(
                "mode_and_reset_state"),
        },
        "locks": {
            "standard_lock_definition": (
                "modules/backend/bench_runner.py STANDARD_LOCK（锁取值唯"
                "一来源）"),
            "actual_adopted_lock_values": (
                {k: v for k, v in applied.items()
                 if not k.startswith("FLAPPER_OBSTACLE")
                 and k not in ("FLAPPER_TAG", "FLAPPER_DESC")}
                or "不适用：python 模式链步按隔离规则不注入 FLAPPER_ 锁"
                   "变量，本次无仿真子进程（可核原因：步骤环境见证）"),
            "seed_and_determinism_basis": {
                "FLAPPER_OBSTACLE_SEED": applied.get("FLAPPER_OBSTACLE_SEED"),
                "FLAPPER_IID_LOCK": applied.get("FLAPPER_IID_LOCK"),
                "note": "种子与确定性锁取实际注入读数；未注入时如实记空"
                        "（python 模式隔离无仿真子进程，可核原因：步骤"
                        "环境见证）",
            },
        },
        "analysis_inputs": {
            "measurement_input_paths_and_hashes": analysis_meas,
            "analysis_producer_and_version": producer,
            "standard_input_paths_and_hashes": analysis_std,
        },
    }
    return facts


def _fin_vec(v, n):
    """严格数值向量校验：长度 n、逐项有限数；None/空/缺项/非数均 False。"""
    if not isinstance(v, (list, tuple)) or len(v) != n:
        return False
    return all(isinstance(x, (int, float)) and not isinstance(x, bool)
               and math.isfinite(x) for x in v)


def _nonempty_source(field):
    src = field.get("source") if isinstance(field, dict) else None
    return isinstance(src, str) and src.strip()


def _event_valid(field, name, problems):
    """事件字段校验（初始化/释放/复位/钉放）：event 须为具名事件；
    none 事件按有界不适用处理，须带范围、理由与实际来源；其余事件须带
    非空来源。无理由不适用不算已恢复事实。"""
    if not isinstance(field, dict):
        problems.append("%s：事件字段须为对象" % name)
        return
    ev = field.get("event")
    if not isinstance(ev, str) or not ev.strip() or ev.strip().lower() in (
            "none", "unknown", "null"):
        if ev is None or (isinstance(ev, str)
                          and ev.strip().lower() in ("none", "unknown",
                                                     "null", "")):
            ok_na = (isinstance(field.get("reason"), str)
                     and field.get("reason").strip()
                     and isinstance(field.get("scope"), str)
                     and field.get("scope").strip()
                     and _nonempty_source(field))
            if not ok_na:
                problems.append(
                    "%s：不适用事件（%r）缺范围、理由或实际来源，无理由"
                    "不适用不算已恢复事实" % (name, ev))
        else:
            problems.append("%s：事件标识非法（%r）" % (name, ev))
        return
    if not _nonempty_source(field):
        problems.append("%s：事件 %s 缺实际来源" % (name, ev))


# 必需事实字段契约（返修 v6）：每条字段的合法外层结构、必需分量、有
# 效数值/状态与来源，整理为有限可执行对照；构包与核验复用同一规则。
# 外层必须为对象且带非空 source（事件字段按事件规则）；数值分量须严格
# 数值（布尔不算数）且有限；布尔状态须严格布尔（字符串真值非法）；
# 姿态须完整已声明的四元数或完整 roll/pitch/yaw 三分量（空姿态、部分
# 欧拉、all() 空集均阻断）；unknown/none/null/n-a 字样不算值；结构不
# 符不得落到无错误出口。
_FACT_CONTRACT = {
    "initial_state.initial_position_world_m": "vec3",
    "initial_state.initial_orientation": "orientation",
    "initial_state.initial_linear_velocity": "vec3",
    "initial_state.initial_angular_velocity": "vec3",
    "initial_state.post_release_position_world_m": "vec3",
    "initial_state.post_release_orientation": "orientation",
    "initial_state.post_release_linear_velocity": "vec3",
    "initial_state.post_release_angular_velocity": "vec3",
    "initial_state.initialization_and_release_boundary": "event",
    "inflow.ambient_flow_world_m_per_s": "vec3",
    "inflow.perturbation_policy": "perturbation",
    "physical_constraints.body_free_dofs": "dofs",
    "physical_constraints.tether_state": "present_bool",
    "physical_constraints.obstacle_state": "present_bool",
    "physical_constraints.pin_release_schedule": "event",
    "controller.effective_parameters": "params",
    "controller.mode_and_reset_state": "event",
}
_INVALID_WORDS = ("unknown", "none", "null", "n/a", "na", "")


def _is_bad_word(v):
    return isinstance(v, str) and v.strip().lower() in _INVALID_WORDS


def _field_of(facts, path):
    node = facts
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _check_wrapped(field, path, kind, problems):
    """外层结构门：字段必须是对象；先判结构再核分量，无跳过出口。"""
    if not isinstance(field, dict):
        problems.append("%s：外层结构非法（%r），合法结构为含 value 与 "
                        "source 的对象" % (path, type(field).__name__))
        return None
    return field


def _check_source(field, path, problems):
    if not _nonempty_source(field):
        problems.append("%s：来源为空（裸值无来源不算实际有效事实）"
                        % path)
        return False
    return True


def _observation_errors(value, path):
    """采集失败不算有效事实，嵌套动作的失败同样保留。"""
    errors = []
    if isinstance(value, dict):
        if value.get("query_error") not in (None, "", [], {}):
            errors.append("%s：实际查询失败（%s）" % (path, value["query_error"]))
        status = value.get("status")
        if status in ("unknown", "failed", "not_executed"):
            errors.append("%s：实际执行状态为 %s" % (path, status))
        if status == "not_applicable" and not all(
                isinstance(value.get(k), str) and value[k].strip()
                for k in ("reason", "scope")):
            errors.append("%s：不适用执行状态缺范围或理由" % path)
        for key, child in value.items():
            if key not in ("query_error", "source"):
                errors.extend(_observation_errors(child, "%s.%s" % (path, key)))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            errors.extend(_observation_errors(child, "%s[%d]" % (path, index)))
    elif isinstance(value, float) and not math.isfinite(value):
        errors.append("%s：实际观察值不是有限数" % path)
    return errors


def _has_parameter_value(value):
    """只有可空滤波状态，不能证明已取得实际参数。"""
    if isinstance(value, dict):
        return any(_has_parameter_value(v) for k, v in value.items()
                   if k not in ("source", "query_error", "reason", "scope"))
    if isinstance(value, (list, tuple)):
        return any(_has_parameter_value(v) for v in value)
    if isinstance(value, (int, float)):
        return isinstance(value, bool) or math.isfinite(value)
    return isinstance(value, str) and not _is_bad_word(value)


def _fact_field_problems(facts, runtime_observed=False):
    """按 _FACT_CONTRACT 逐字段校验（返修 v6 契约驱动，无跳过出口）。

    每条字段：先核外层合法结构（对象），再核全部必需分量（不过滤不合
    法项后查剩余）、非布尔数值类型与有限性、严格布尔状态、完整姿态表
    示与非空实际来源；有界不适用声明须范围加理由加来源。空对象、全
    null 向量、部分欧拉、字符串真值、unknown 字样、裸值无来源均点名。
    """
    problems = []
    for path, kind in _FACT_CONTRACT.items():
        field = _field_of(facts, path)
        if field is None:
            if runtime_observed and path.startswith("initial_state.post_release_"):
                problems.append("%s：运行期采集缺少释放后的实际状态" % path)
            continue  # 缺字段由 required 清单点名，此处只核已给字段
        if _check_wrapped(field, path, kind, problems) is None:
            continue
        problems.extend(_observation_errors(field, path))
        v = field.get("value")
        if kind == "vec3":
            if not _fin_vec(v, 3):
                problems.append(
                    "%s：三分量向量缺失、形状非法或含非有限/布尔分量"
                    "（%r）" % (path, v))
            _check_source(field, path, problems)
        elif kind == "orientation":
            ok = isinstance(v, dict)
            if ok:
                quat = v.get("quaternion_wxyz")
                euler = [v.get(k) for k in
                         ("roll_rad", "pitch_rad", "yaw_rad")]
                has_quat = quat is not None
                has_euler = all(e is not None for e in euler)
                if has_quat and has_euler:
                    ok = False  # 双表示歧义，须完整声明其一
                    problems.append("%s：姿态须完整声明四元数或欧拉其一"
                                    "（同给两种不合契约）" % path)
                elif has_quat:
                    if not _fin_vec(quat, 4):
                        problems.append("%s：四元数须四个有限数值分量"
                                        "（%r）" % (path, quat))
                    elif not any(component != 0.0 for component in quat):
                        problems.append("%s：零四元数不能表示实际姿态" % path)
                elif has_euler:
                    if not all(isinstance(e, (int, float))
                               and not isinstance(e, bool)
                               and math.isfinite(e) for e in euler):
                        problems.append("%s：欧拉角须 roll/pitch/yaw 三分"
                                        "量全给且有限（部分或缺角非法，"
                                        "%r）" % (path, euler))
                else:
                    ok = False
                    problems.append("%s：姿态表示为空（须完整四元数或完"
                                    "整欧拉三分量）" % path)
            else:
                problems.append("%s：姿态表示须为对象（四元数或欧拉）而"
                                "非 %r" % (path, v))
            _check_source(field, path, problems)
        elif kind == "event":
            _event_valid(field, path, problems)
        elif kind == "perturbation":
            inj = field.get("injected")
            if not isinstance(inj, bool):
                problems.append("%s：扰动注入标记须为严格布尔（%r 非法，"
                                "字符串真值不得被默认吞掉）" % (path, inj))
            if _is_bad_word(v) and not isinstance(v, (dict, list)):
                problems.append("%s：扰动策略为 unknown 类字样，不算实际"
                                "有效事实" % path)
            _check_source(field, path, problems)
        elif kind == "dofs":
            keys = ("tx", "ty", "tz", "rx", "ry", "rz")
            if not isinstance(v, dict) or not all(
                    isinstance(v.get(k), bool) for k in keys):
                problems.append("%s：六个机体自由度（三平移三转动）须逐"
                                "项严格布尔全给（%r，缺分量或类型非法）"
                                % (path, v))
            _check_source(field, path, problems)
        elif kind == "present_bool":
            if not isinstance(field.get("present"), bool):
                problems.append("%s：present 须为严格布尔（%r）"
                                % (path, field.get("present")))
            _check_source(field, path, problems)
        elif kind == "params":
            na = isinstance(field.get("not_applicable_reason"), str)                 and field.get("not_applicable_reason").strip()
            empty = v is None or v == {} or v == "" or v == []
            if _is_bad_word(v):
                problems.append("%s：参数为 unknown 类字样（%r），不算实"
                                "际有效事实" % (path, v))
            elif empty and not na:
                problems.append("%s：空对象或空值且无有依据的不适用声"
                                "明，不得靠外层存在放行" % path)
            if na and not (
                    isinstance(field.get("scope"), str)
                    and field.get("scope").strip()
                    and _nonempty_source(field)):
                problems.append("%s：不适用声明缺范围或实际来源" % path)
            if not empty and not isinstance(v, dict) and not (
                    na and isinstance(field.get("scope"), str)
                    and field["scope"].strip() and _nonempty_source(field)):
                problems.append("%s：实际参数须为具名参数对象；其他表示须有"
                                "范围、理由及来源的不适用声明" % path)
            if isinstance(v, dict) and not _has_parameter_value(v) and not na:
                problems.append("%s：参数对象中没有已知的实际参数值" % path)
            if not _nonempty_source(field):
                problems.append("%s：来源为空" % path)
    return problems


def _necessary_report(conditions, quantities, record=None, crits=None,
                      run_dir=None):
    """必要条件节核对报告（事项 I-186 返修 v3）。

    悬停基准科目（bench_subject 悬停或 bench_criterion 悬停条目）的必要
    清单由 HQ 正式批准记录（HQ-I186-HOVER-REQUIRED-20261014-V1）决定：
    来源须精确绑定批准文件（哈希、版本、record_id 一致），分支按本次实
    际记录类型选择（有 CSV 用 hover_csv，仅台账用 hover_ledger，错分支
    或用台账分支豁免 CSV 必需量均阻断）；条件为实际事实标识，按
    condition_definitions 的 required 字段逐项核对事实在档；必需量按分支
    清单与 quantity_definitions 逐项核对存在与定义有效性。无清单定义、
    未获批准或空声明按待审阻断；其他科目无批准定义仍未知待审。声明清单
    只加不减；空清单不豁免。配置声明的 FLAPPER_ 前缀条件键仍按实际注入
    核对（既有环境键语义）。
    """
    crit_cfg = ((record or {}).get("config") or {}).get("criteria") or {}
    kind = crit_cfg.get("kind")
    subject = crit_cfg.get("subject")
    hover_bench = kind in ("bench_subject", "bench_criterion") \
        and subject == "hover"
    nec = ((conditions or {}).get("declared") or {}).get(
        "necessary_conditions")
    base_note = ("清单只加不减：空清单不豁免无条件必要项（必要参数、版本"
                 "读数等由缺项检查另行把守）")
    if not hover_bench:
        if not isinstance(nec, dict):
            return {"status": "not_declared",
                    "reason": "非悬停基准科目且未声明必要条件节（本批准"
                              "记录范围仅悬停；旧配置兼容读取）",
                    "note": base_note}
        # 声明了来源但科目不在批准范围：仍按来源批准性核（防借旧口径）
        problem, info = _resolve_approved_manifest(nec.get("source"))
        if problem:
            return {"status": "pending_source", "source": nec.get("source"),
                    "reason": "必要清单批准来源待审（%s）" % problem,
                    "note": base_note}
        return {"status": "pending_source",
                "source": nec.get("source"),
                "reason": ("科目 %s 不在批准记录范围内（批准范围仅悬停 "
                           "H1/H2/H3）；该科目必要定义未知待审，不正式通"
                           "过" % subject),
                "note": base_note}
    if not isinstance(nec, dict):
        return {"status": "pending_source",
                "reason": "悬停基准科目缺必要性定义（无必要条件节）：没"
                          "有清单定义或空声明必须待审阻断，旧配置可读取"
                          "但不自动获新实验通过",
                "note": base_note}
    problem, info = _resolve_approved_manifest(nec.get("source"))
    rep = {
        "source": nec.get("source"),
        "approval": {
            "record_id": APPROVED_REQUIRED["record_id"],
            "version": APPROVED_REQUIRED["version"],
            "authority": APPROVED_REQUIRED["authority"],
            "file": APPROVED_REQUIRED["file"],
            "sha256": info.get("manifest_sha256"),
            "approval_readback": APPROVED_REQUIRED["approval_readback"],
        },
        "branch_declared": info.get("branch_declared"),
        "branch_detected": None,
        "declared_conditions": [k for k in (nec.get("conditions") or [])
                                if isinstance(k, str)],
        "declared_quantities": [q for q in (nec.get("quantities") or [])
                                if isinstance(q, str)],
        "missing_conditions": [],
        "missing_facts": [],
        "missing_quantities": [],
        "undefined_quantities": [],
        "note": base_note,
    }
    if problem:
        rep["status"] = "pending_source"
        rep["reason"] = "必要清单批准来源待审（%s）：未获批准不得正式通" \
                        "过" % problem
        return rep
    manifest = info["manifest"]
    branch = _detect_record_branch(run_dir)
    rep["branch_detected"] = branch
    if branch is None:
        rep["status"] = "pending_source"
        rep["reason"] = ("本次运行无 CSV 数据流亦无原值台账，记录类型无法"
                         "检出，分支不能选择（不得凭空选分支）")
        return rep
    if info.get("branch_declared") and info["branch_declared"] != branch:
        rep["status"] = "pending_source"
        rep["reason"] = ("声明分支 %s 与本次实际记录类型检出的分支 %s 不"
                         "符（错分支；有 CSV 记录不得用台账分支豁免 "
                         "CSV 必需量）"
                         % (info["branch_declared"], branch))
        return rep
    branch_def = (manifest.get("subjects") or {}).get(branch) or {}
    lists = (branch_def.get("necessary_conditions") or {})
    req_conditions = [c for c in (lists.get("conditions") or [])
                      if isinstance(c, str)]
    req_quantities = [q for q in (lists.get("quantities") or [])
                      if isinstance(q, str)]
    rep["required_conditions"] = req_conditions
    rep["required_quantities"] = req_quantities
    rep["record_kind"] = branch_def.get("record_kind")
    # 声明清单只加不减：删项核对（声明了的清单项须覆盖批准基线；声明为
    # 空不豁免——基线仍然全部适用）
    # 条件事实核对：按 condition_definitions 的 required 字段逐项
    cond_defs = branch_def.get("condition_definitions") or {}
    facts = _required_facts(conditions, record, quantities, crits or [],
                            branch, run_dir)
    rep["facts"] = facts
    # 场景见证来源（返修 v4）：记录见证文件与身份绑定状态，缺见证即缺
    # 实际来源（点名，不代填）
    witness, witness_problem = _load_scene_witness(run_dir, record)
    wpath = os.path.join(run_dir, "scene_witness.json")
    rep["scene_witness"] = {
        "file": "scene_witness.json" if witness is not None else None,
        "sha256": _sha256_file(wpath) if witness is not None else None,
        "producer": (witness or {}).get("producer"),
        "collection_mode": (witness or {}).get("collection_mode"),
        "problem": witness_problem,
    }
    missing_facts = []
    for cname in req_conditions:
        cdef = cond_defs.get(cname) or {}
        fact = facts.get(cname)
        if fact is None:
            missing_facts.append(cname)
            continue
        for field in (cdef.get("required") or []):
            if fact.get(field) is None:
                missing_facts.append("%s.%s" % (cname, field))
    # 返修 v5：逐字段有效性——结构、数值类型/形状/有限性、来源非空与
    # 有界不适用声明。空对象、全 null 向量、空来源、字符串真值、无理
    # 由不适用与 none/unknown 字样都不算已恢复事实（点名阻断，不因外层
    # 存在放行）。
    runtime_observed = "runtime_object" in str(
        (witness or {}).get("collection_mode") or "").split("+")
    invalid = _fact_field_problems(facts, runtime_observed=runtime_observed)
    for item in invalid:
        missing_facts.append(item)
    rep["invalid_facts"] = invalid
    rep["missing_facts"] = missing_facts
    # 声明的 FLAPPER_ 环境键条件：实际注入核对（含步骤见证并集）
    applied = dict(conditions.get("applied_overrides") or {})
    step_notes = []
    for st in (record or {}).get("steps") or []:
        if not isinstance(st, dict):
            continue
        witness = st.get("env_witness") or {}
        for k, v in (witness.get("env_final") or {}).items():
            if not k.startswith("FLAPPER_"):
                continue
            if k in applied and str(applied[k]) != str(v):
                step_notes.append("步骤 %s 的 %s=%s 与起始采集值 %s 不一"
                                  % (st.get("name"), k, v, applied[k]))
            applied.setdefault(k, v)
    rep["applied_union"] = sorted(applied)
    rep["step_value_conflicts"] = step_notes
    env_conditions = [c for c in rep["declared_conditions"]
                      if c.startswith("FLAPPER_")]
    missing_env = [c for c in env_conditions if c not in applied]
    rep["missing_conditions"] = missing_env
    # 必需量核对：分支清单逐项（存在 + 定义有效 + quantity_definitions
    # 在档）
    entries = {}
    for m in (quantities or {}).get("mappings") or []:
        if not isinstance(m, dict):
            continue
        key = m.get("column") if m.get("column") is not None else m.get(
            "channel")
        if key is not None:
            entries[str(key)] = m
    qdefs = branch_def.get("quantity_definitions") or {}
    missing_quantities = []
    undefined_quantities = []

    def _check_quantity(q):
        m = entries.get(str(q))
        if m is None:
            missing_quantities.append(q)
            return
        group = str(m.get("field_group") or "")
        meta_unknown = any(m.get(k) in (None, "unknown")
                           for k in ("unit", "frame", "sample_time",
                                     "attribute", "source"))
        qdef = qdefs.get(q)
        if group.startswith("unverified") or group == "unmapped" \
                or meta_unknown or not isinstance(qdef, dict):
            undefined_quantities.append(q)

    for q in req_quantities:
        _check_quantity(q)
    # 附加声明量（清单只加不减的加项）同样逐项核对存在与定义有效
    for q in rep["declared_quantities"]:
        if q not in req_quantities:
            _check_quantity(q)
    rep["missing_quantities"] = missing_quantities
    rep["undefined_quantities"] = undefined_quantities
    if missing_facts or missing_env or missing_quantities \
            or undefined_quantities or step_notes:
        rep["status"] = "deficient"
        gaps = (["条件事实缺失：%s" % "、".join(missing_facts)]
                if missing_facts else []) \
            + (["环境条件未实际应用：%s" % "、".join(missing_env)]
               if missing_env else []) \
            + (["必需量缺失：%s" % "、".join(missing_quantities)]
               if missing_quantities else []) \
            + (["必需量定义或来源未知：%s（不猜语义）"
                % "、".join(undefined_quantities)]
               if undefined_quantities else []) \
            + step_notes
        rep["reason"] = "必要清单缺口（%s）" % "；".join(gaps)
    else:
        rep["status"] = "complete"
    return rep


def _scenario_comparison(conditions, facts, record, run_dir):
    """实际采集值与批准悬停场景逐项比较（返修 v4）。

    批准场景（判据文件 hover 科目 scenario，随批准清单
    scientific_source 绑定）只作对照：气动模型按值比较（attached_flow、
    sane_dickinson_3d 等未获本场景批准的变体一律不符）；来流与物理约
    束按实际类别比较（有实际风、有系留或有障碍即不符，采集真值保留不
    强改）；初态与控制器只核来源存在（CSV 首行状态不要求等于初态，判
    断依据是来源而非数值）。缺实际来源记 no_actual_source（未知）而不
    是相符。返回条目清单与总判定（match/divergent/unknown）。
    """
    try:
        bench = _load_json(os.path.join(ROOT, CRITERIA_FILE_REL))
        scenario = ((bench.get("subjects") or {}).get("hover") or {}
                    ).get("scenario") or {}
    except Exception:
        scenario = {}
    ec = (conditions or {}).get("effective_config") or {}
    items = []
    am = ec.get("aero_model")
    ap = scenario.get("aero_model")
    items.append({
        "item": "aero_model",
        "actual": am,
        "approved": ap,
        "verdict": ("match" if am == ap and ap is not None
                    else "divergent"),
        "note": "气动模型按值比较；未获本悬停场景批准的变体不自动适用",
    })
    inflow = (facts or {}).get("inflow") or {}
    wind = _witness_value(inflow.get("ambient_flow_world_m_per_s"))
    pol = inflow.get("perturbation_policy")
    pol_injected = pol.get("injected") if isinstance(pol, dict) else None
    wind_zero = (isinstance(wind, (list, tuple)) and len(wind) == 3
                 and all(isinstance(v, (int, float))
                         and float(v) == 0.0 for v in wind))
    if wind is None and pol is None:
        v_inflow = "no_actual_source"
    elif wind_zero and pol_injected is not True:
        v_inflow = "match"
    else:
        v_inflow = "divergent"
    items.append({
        "item": "inflow",
        "actual": {"ambient_flow_world_m_per_s": wind,
                   "perturbation_injected": pol_injected},
        "approved": "无来流风、无扰动注入（scenario.environment）",
        "verdict": v_inflow,
        "note": "无环境覆盖不等于无风：实际来流以见证真值为准并保留",
    })
    pc = (facts or {}).get("physical_constraints") or {}
    tether = pc.get("tether_state")
    obstacle = pc.get("obstacle_state")
    pin = pc.get("pin_release_schedule")
    dofs = pc.get("body_free_dofs")
    tether_present = tether.get("present") if isinstance(tether, dict) \
        else None
    obst_present = obstacle.get("present") if isinstance(obstacle, dict) \
        else None
    if tether is None and obstacle is None and pin is None and dofs is None:
        v_pc = "no_actual_source"
    elif tether_present is True or obst_present is True:
        v_pc = "divergent"
    elif tether_present is False and obst_present is False:
        v_pc = "match"
    else:
        v_pc = "no_actual_source"
    pin_event = pin.get("event") if isinstance(pin, dict) else None
    items.append({
        "item": "physical_constraints",
        "actual": {"tether_present": tether_present,
                   "obstacle_present": obst_present,
                   "pin_release_event": pin_event,
                   "pin_release_reason": pin.get("reason")
                   if isinstance(pin, dict) else None},
        "approved": "无系留、无障碍物；钉住后释放（scenario.environment）",
        "verdict": v_pc,
        "note": "系留与障碍按严格布尔真值；钉放事件另列条目核对",
    })
    # 机体自由度逐项比较（返修 v5）：批准悬停为释放后自由飞行，六个机
    # 体自由度（三平移三转动）按实际声明逐项核对；全锁或任一锁定均不
    # 属已批准自由飞行，真值保留不改。
    dof_val = dofs.get("value") if isinstance(dofs, dict) else None
    dof_keys = ("tx", "ty", "tz", "rx", "ry", "rz")
    if not isinstance(dof_val, dict) or not all(
            isinstance(dof_val.get(k), bool) for k in dof_keys):
        v_dof = "no_actual_source"
        dof_actual = dof_val
    else:
        locked = [k for k in dof_keys if dof_val.get(k) is False]
        dof_actual = dict(dof_val)
        v_dof = "divergent" if locked else "match"
    items.append({
        "item": "body_free_dofs",
        "actual": dof_actual,
        "approved": "释放后六自由度自由飞行（钉住段除外）",
        "verdict": v_dof,
        "note": "六个机体平移/转动自由度逐项按实际值核对；三平移三转动"
                "均锁定或任一锁定均不属已批准自由飞行，采集真值保留",
    })
    # 钉放/初始化释放事件来源核对：具名事件为来源在档；none（有理由的
    # 有界不适用）不算实际初始化来源——结构诊断样例可明确缺物理事件，
    # 但不能成为悬停运行通过。
    ev = pin_event
    ev_is_none = ev is None or (isinstance(ev, str) and ev.strip().lower()
                                in ("none", "unknown", "null", ""))
    items.append({
        "item": "pin_release_event",
        "actual": ev,
        "approved": "钉住后释放（实际初始化/释放事件，不从标准或配置推"
                    "断动作已执行）",
        "verdict": ("no_actual_source" if ev_is_none else "match"),
        "note": "无实际初始化/释放事件的样例不得作为悬停运行通过",
    })
    init = (facts or {}).get("initial_state") or {}
    ctl = (facts or {}).get("controller") or {}
    boundary = init.get("initialization_and_release_boundary")
    boundary_ev = boundary.get("event") if isinstance(boundary, dict) \
        else None
    boundary_none = boundary_ev is None or (
        isinstance(boundary_ev, str)
        and boundary_ev.strip().lower() in ("none", "unknown", "null", ""))
    init_ok = all(init.get(k) is not None for k in (
        "initial_position_world_m", "initial_orientation",
        "initial_linear_velocity", "initial_angular_velocity",
        "initialization_and_release_boundary")) and not boundary_none
    ctl_ok = ctl.get("effective_parameters") is not None \
        and ctl.get("mode_and_reset_state") is not None
    items.append({
        "item": "initial_state_source",
        "actual": "同运行场景见证在档" if init_ok else "缺实际来源",
        "approved": "实际初始化与释放事实须有本次来源（不比数值）",
        "verdict": "match" if init_ok else "no_actual_source",
        "note": "CSV 首行状态不要求等于初态；依据是来源不是数值",
    })
    items.append({
        "item": "controller_source",
        "actual": "同运行场景见证在档" if ctl_ok else "缺实际来源",
        "approved": "实际控制器事实须有本次来源（不重定增益）",
        "verdict": "match" if ctl_ok else "no_actual_source",
        "note": "无环境覆盖不等于无参数；事实以见证为准",
    })
    verdicts = {i["verdict"] for i in items}
    if "divergent" in verdicts:
        total = "divergent"
    elif "no_actual_source" in verdicts:
        total = "unknown"
    else:
        total = "match"
    return {"scenario_source": "%s#hover" % CRITERIA_FILE_REL,
            "approved_aero_model": ap,
            "items": items, "verdict": total}


def _criteria_scope(crit_cfg):
    """判据声明范围（事项 I-186 接续）：结论的批准范围随包与卡分列，单
    判据范围不代称整科通过。"""
    kind = (crit_cfg or {}).get("kind")
    if kind == "bench_subject":
        return {"kind": "bench_subject",
                "subject": crit_cfg.get("subject"),
                "meaning": "整科批准范围：必需判据集合为该科目全部条目，"
                           "缺任一条不得整科通过"}
    if kind == "bench_criterion":
        return {"kind": "bench_criterion",
                "subject": crit_cfg.get("subject"),
                "criterion": crit_cfg.get("criterion"),
                "meaning": "单判据批准范围：结论仅覆盖声明条目，不代称整"
                           "科通过；范围外条目如实记不适用"}
    return {"kind": kind,
            "subject": (crit_cfg or {}).get("subject"),
            "meaning": "非基准科目判据形态（%s），按既有规则逐条评估，不"
                       "作整科齐备性核对" % kind}


def compute_verdict(pack):
    """I-183 判定规则（L1 接续版）：任何缺失、未知、错配阻止正式 pass；
    实际记录量映射缺失同样阻断；单判据范围外条目（不适用）不参与判定；
    声明的必要条件或必需量清单有缺口同样阻断。"""
    p = []
    ident = pack.get("identity") or {}
    for k in ("experiment_id", "item_id", "run_id"):
        if not (ident.get(k) or {}).get("value"):
            p.append("身份 %s 缺失" % k)
    if ident.get("run_record_sha256_mismatch"):
        p.append("运行记录哈希错配")
    if pack["execution"]["status"] != "completed":
        p.append("执行 %s" % pack["execution"]["status"])
    if pack.get("conditions_status") != "captured":
        p.append("实际条件 %s" % pack.get("conditions_status"))
    if pack["applicability"]["status"] != "applicable":
        p.append("适用性 %s" % pack["applicability"]["status"])
    if pack["data_qualification"]["status"] != "valid":
        p.append("数据资格 %s" % pack["data_qualification"]["status"])
    qm = ((pack.get("quantity_meanings") or {})
          .get("actual_mapping") or {})
    if qm.get("status") != "present":
        p.append("实际记录量映射 %s" % qm.get("status", "missing"))
    nec = pack.get("necessary_conditions") or {}
    if nec.get("status") == "deficient":
        gaps = (nec.get("missing_conditions") or []) \
            + (nec.get("missing_facts") or []) \
            + (nec.get("missing_quantities") or []) \
            + (nec.get("undefined_quantities") or [])
        p.append("必要条件清单缺口（%s）" % "、".join(gaps)[:160])
    elif nec.get("status") == "pending_source":
        p.append("必要清单批准来源待审（%s）"
                 % str(nec.get("reason"))[:120])
    crits = pack.get("criteria") or []
    if not crits:
        p.append("判据为空")
    for c in crits:
        if c.get("status") == "not_applicable":
            continue
        if c.get("status") != "pass":
            p.append("判据 %s（%s）" % (c.get("criterion_id")
                                       or c.get("subject_declared") or "?",
                                       c.get("status")))
    if not p:
        scope = pack.get("criteria_scope") or {}
        return "pass", ("身份链、条件、资格、适用性与判据全部齐备且通过"
                        "（%s）" % scope.get("meaning", "批准范围见声明")
                        if scope.get("kind") == "bench_criterion" else
                        "身份链、条件、资格、适用性与判据全部齐备且通过")
    if pack["execution"]["status"] == "failed" \
            or any(c.get("status") == "fail" for c in crits):
        return "fail", "；".join(p)
    return "unknown", "；".join(p)


def build_pack(run_dir):
    run_dir = os.path.abspath(run_dir)
    rr_path = os.path.join(run_dir, "run_record.json")
    cond_path = os.path.join(run_dir, CONDITIONS_NAME)
    record = _load_json(rr_path) if os.path.exists(rr_path) else None
    conditions = _load_json(cond_path) if os.path.exists(cond_path) else None
    legacy = record is None
    identity = _identity_from_record(record or {})
    identity["legacy_mode"] = legacy
    identity["reason_if_legacy"] = "目录无 run_record（旧配置兼容）" \
        if legacy else None
    identity["run_record"] = "run_record.json"
    identity["run_record_sha256"] = _sha256_file(rr_path) if not legacy \
        else None
    identity["run_record_sha256_mismatch"] = False
    ec = (conditions or {}).get("effective_config") or {}
    scenario_cmp = None
    if legacy:
        execution = _status("execution", "unknown", "无运行记录")
        conditions_status = "missing"
        qual = _status("data_qualification", "unknown",
                       "旧目录无台账与记录，待核实")
        crits = [_status("criterion", "missing", "旧目录无判据配置")]
        applicability = _status("applicability", "unknown",
                                "旧档条件缺失，适用性待核实")
        quantities = _quantity_mapping(run_dir, [])
        foreign_ledgers = []
        own = []
    else:
        execution = assess_execution(record)
        if conditions is None:
            conditions_status = "missing"
            qual = _status("data_qualification", "unknown",
                           "无运行起始条件采集：必要条件缺失不判有效，不"
                           "以当前配置补历史事实")
        elif ec.get("snapshot_mode") != "child_effective":
            conditions_status = "parent_default"
            qual = _status("data_qualification", "unknown",
                           "起始快照非子进程生效值（mode=%s），父进程默认"
                           "不得冒充实际配置，必要条件缺失不判有效"
                           % ec.get("snapshot_mode"))
        elif _conditions_deficiencies(conditions):
            conditions_status = "captured_deficient"
            qual = _status("data_qualification", "unknown",
                           "起始快照必要条件缺失（%s），不判有效"
                           % "；".join(_conditions_deficiencies(conditions)))
        else:
            conditions_status = "captured"
        own, foreign_ledgers = _bind_ledgers(run_dir, record, identity)
        if conditions_status == "captured":
            qual = _qualification(run_dir, own, conditions)
        crits = _criteria(record, run_dir, identity, own)
        quantities = _quantity_mapping(run_dir, own)
        # 返修 v4：实际采集值与批准悬停场景逐项比较——不符且无批准差
        # 异不得正式适用（attached_flow、sane_dickinson_3d 等未获本场景
        # 批准的模型变体阻断；缺实际来源记未知）。
        crit_cfg_live = ((record or {}).get("config") or {}).get(
            "criteria") or {}
        hover_live = crit_cfg_live.get("kind") in (
            "bench_subject", "bench_criterion") \
            and crit_cfg_live.get("subject") == "hover"
        if hover_live:
            nec_live = _necessary_report(conditions, quantities, record,
                                         crits, run_dir)
            scenario_cmp = _scenario_comparison(
                conditions, (nec_live.get("facts") or {}), record, run_dir)
        else:
            scenario_cmp = None
        if scenario_cmp is not None and scenario_cmp.get("verdict") \
                == "divergent":
            applicability = _status(
                "applicability", "not_applicable",
                "与批准悬停场景不符且无批准差异（%s）：不得正式通过"
                % "；".join("%s 实际 %r 对批准 %r"
                            % (i["item"], i["actual"], i["approved"])
                            for i in scenario_cmp["items"]
                            if i["verdict"] == "divergent")[:300])
        elif scenario_cmp is not None and scenario_cmp.get("verdict") \
                == "unknown":
            applicability = _status(
                "applicability", "unknown",
                "实际初始化/释放或自由度等事实来源缺失（%s）：结构诊断"
                "样例可明确缺物理事件，但不能成为悬停运行通过，适用性待"
                "核" % "；".join(
                    "%s 实际 %r" % (i["item"], i["actual"])
                    for i in scenario_cmp["items"]
                    if i["verdict"] == "no_actual_source")[:200])
        else:
            applicability = _status(
                "applicability",
                "applicable" if conditions_status == "captured" else
                "unknown",
                "按运行记录与子进程生效起始条件声明的工况适用"
                if conditions_status == "captured" else
                "条件缺失或非子进程生效（%s），适用性待核"
                % conditions_status)
    crit_cfg = ((record or {}).get("config") or {}).get("criteria") or {}
    necessary = _necessary_report(conditions, quantities, record, crits,
                                  run_dir)
    pack = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL,
        "publish_id": uuid.uuid4().hex,
        "generated_at": _now(),
        "identity": identity,
        "conditions_status": conditions_status,
        "criteria_scope": _criteria_scope(crit_cfg),
        "approved_scenario_comparison": scenario_cmp,
        "necessary_conditions": necessary,
        "run_conditions": {
            "source": CONDITIONS_NAME if conditions else None,
            "sha256": _sha256_file(cond_path) if conditions else None,
            "captured_at": (conditions or {}).get("captured_at"),
            "phase": (conditions or {}).get("phase"),
            "snapshot_mode": ec.get("snapshot_mode"),
            "applied_overrides": (conditions or {}).get("applied_overrides"),
            "source_version": (conditions or {}).get("source_version"),
        },
        "quantity_meanings": {
            "spec": SPEC_REL,
            "spec_sha256": _sha256_file(SPEC_PATH),
            "field_groups": len(_load_json(SPEC_PATH).get("fields", [])),
            "actual_mapping": quantities,
        },
        "execution": execution,
        "data_qualification": qual,
        "foreign_ledgers": foreign_ledgers,
        "applicability": applicability,
        "criteria": crits,
        "control_use": _status("control_use", "diagnostic_only",
                               "证据包结论不自动授予控制使用资格"),
    }
    verdict, reason = compute_verdict(pack)
    pack["experiment_verdict"] = {"status": verdict, "reason": reason}
    card = {
        "schema_version": SCHEMA_VERSION,
        "publish_id": pack["publish_id"],
        "experiment_id": identity["experiment_id"],
        "item_id": identity["item_id"],
        "run_id": identity["run_id"],
        "generated_at": pack["generated_at"],
        "criteria_scope": {
            "kind": pack["criteria_scope"].get("kind"),
            "subject": pack["criteria_scope"].get("subject"),
            "criterion": pack["criteria_scope"].get("criterion"),
            "meaning": pack["criteria_scope"].get("meaning"),
        },
        "approved_scenario": (
            {"approved_aero_model": scenario_cmp.get("approved_aero_model"),
             "verdict": scenario_cmp.get("verdict")}
            if scenario_cmp is not None else None),
        "execution": execution["status"],
        "data_qualification": qual["status"],
        "criteria": [{"subject_declared": c.get("subject_declared"),
                      "subject_actual": c.get("subject_actual"),
                      "criterion_id": c.get("criterion_id"),
                      "quantity": c.get("quantity"),
                      "status": c["status"]} for c in crits],
        "experiment_verdict": verdict,
        "reason": reason,
    }
    _publish_pair(run_dir, PACK_NAME, pack, CARD_NAME, card)
    return pack


def _publish_pair(run_dir, pack_name, pack, card_name, card):
    """成对发布：双临时文件写好后先后改名；第二失败回滚第一并抛出。"""
    tmp_pack = os.path.join(run_dir, pack_name + ".tmp")
    tmp_card = os.path.join(run_dir, card_name + ".tmp")
    with open(tmp_pack, "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, indent=1)
    with open(tmp_card, "w", encoding="utf-8") as f:
        json.dump(card, f, ensure_ascii=False, indent=1)
    target_pack = os.path.join(run_dir, pack_name)
    target_card = os.path.join(run_dir, card_name)
    backup = {}
    for t in (target_pack, target_card):
        if os.path.exists(t):
            bak = t + ".prev"
            with open(t, "rb") as f:
                old = f.read()
            with open(bak, "wb") as f:
                f.write(old)
            backup[t] = old
    os.replace(tmp_pack, target_pack)
    try:
        os.replace(tmp_card, target_card)
    except Exception:
        if target_pack in backup:
            with open(target_pack, "wb") as f:
                f.write(backup[target_pack])
        else:
            os.remove(target_pack)
        raise


def verify_pack(pack_path):
    """核验：身份链与全部输入内容哈希重查、卡片对齐、混代可查、判定
    规则一致；任何缺项、错配、变化不得维持实验通过。"""
    pack = _load_json(pack_path)
    run_dir = os.path.dirname(os.path.abspath(pack_path))
    problems = []
    if pack.get("schema_version") != SCHEMA_VERSION:
        problems.append("包版本不符")
    ident = pack.get("identity") or {}
    rr = os.path.join(run_dir, "run_record.json")
    if ident.get("run_record_sha256"):
        if not os.path.exists(rr) or _sha256_file(rr) != \
                ident["run_record_sha256"]:
            problems.append("run_record 哈希错配或缺失")
    cond = (pack.get("run_conditions") or {}).get("source")
    if cond:
        cp = os.path.join(run_dir, cond)
        if not os.path.exists(cp) or _sha256_file(cp) != \
                pack["run_conditions"]["sha256"]:
            problems.append("运行起始条件文件哈希错配或缺失")
    sv = (pack.get("run_conditions") or {}).get("source_version") or {}
    if sv.get("uncommitted_patch"):
        pp = os.path.join(run_dir, sv["uncommitted_patch"])
        if not os.path.exists(pp) or _sha256_file(pp) != \
                sv.get("uncommitted_patch_sha256"):
            problems.append("源码恢复补丁哈希错配或缺失")
    # 返修五：核验对条件内容本身重查（旧或外部条件文件同样适用），不只
    # 靠文件哈希——必要字段缺失而包称条件齐备或判 pass 即为问题
    rec_live = None
    rr_live = os.path.join(run_dir, "run_record.json")
    if os.path.exists(rr_live):
        try:
            rec_live = _load_json(rr_live)
        except Exception as exc:
            problems.append("运行记录不可读，整科齐备性与必要清单无法重"
                            "查：%r" % exc)
    if cond and os.path.exists(os.path.join(run_dir, cond)):
        try:
            live = _load_json(os.path.join(run_dir, cond))
            defs = _conditions_deficiencies(live)
            if defs and (pack.get("conditions_status") == "captured"
                         or (pack.get("experiment_verdict") or {})
                         .get("status") == "pass"):
                problems.append("条件必要字段缺失（%s）与包声明不符"
                                % "；".join(defs[:3]))
        except Exception as exc:
            problems.append("条件内容重查失败：%r" % exc)
    # 返修六：核验重查引用标准来源内容（文件、摘要、科目、阈值键）
    for i, c in enumerate(pack.get("criteria") or []):
        std = c.get("standard") or {}
        ref = std.get("reference")
        if not ref:
            continue
        ok2, std2 = _resolve_standard(ref)
        if not ok2:
            problems.append("判据 %d 标准来源失效（%s）"
                            % (i, "；".join(std2["problems"])))
            continue
        semr = c.get("semantics") or {}
        if semr.get("registry_sha256"):
            rp = os.path.join(ROOT, semr.get("registry") or SEMANTICS_REL)
            if not os.path.exists(rp) or _sha256_file(rp) !=                     semr["registry_sha256"]:
                problems.append("判据 %d 语义登记册内容变动或缺失" % i)
            else:
                try:
                    sdoc = _load_json(rp)
                    s2 = (sdoc.get("entries") or {}).get(
                        semr.get("reference"))
                    if s2 is None:
                        problems.append("判据 %d 语义条目失效（%s）"
                                        % (i, semr.get("reference")))
                    else:
                        # 方向与单位重查：生产格式条目按其绑定（quantity
                        # 对应项）；科目层单标量按条目平铺定义。
                        b2 = None
                        if c.get("result_format") == "producer_nested" \
                                and isinstance(s2.get("bindings"), list):
                            b2 = next(
                                (b for b in s2["bindings"]
                                 if isinstance(b, dict)
                                 and b.get("quantity") == c.get("quantity")),
                                None)
                        if b2 is not None:
                            if c.get("comparison") != b2.get("direction"):
                                problems.append("判据 %d 比较方向与登记册"
                                                "绑定不符" % i)
                            if str(c.get("unit", "")).strip() not in \
                                    [str(u).strip() for u in
                                     (b2.get("unit_equivalents") or [])]:
                                problems.append("判据 %d 单位与登记册绑定"
                                                "不符" % i)
                        else:
                            if c.get("comparison") != s2.get("direction"):
                                problems.append("判据 %d 比较方向与登记册不"
                                                "符" % i)
                            if str(c.get("unit", "")).strip() not in                                 [str(u).strip() for u in
                                 (s2.get("unit_equivalents") or [])]:
                                problems.append("判据 %d 单位与登记册不符" % i)
                except Exception as exc:
                    problems.append("判据 %d 语义登记册重查失败：%r"
                                    % (i, exc))
        if std.get("sha256") != std2["sha256"]:
            problems.append("判据 %d 标准文件内容变动（摘要不符）" % i)
        tk = c.get("standard_threshold_key")
        if tk and (not isinstance(std2.get("threshold_object"), dict)
                   or tk not in (std2["threshold_object"] or {})):
            problems.append("判据 %d 标准阈值键 %s 失效" % (i, tk))
    for kind, key in (("execution", "execution"),
                      ("data_qualification", "data_qualification"),
                      ("applicability", "applicability"),
                      ("control_use", "control_use")):
        if (pack.get(key) or {}).get("status") not in STATES[kind]:
            problems.append("%s 状态非法" % key)
    for ref in (pack.get("data_qualification") or {}).get("input_refs") \
            or []:
        p = ref.get("path")
        if p and (not os.path.exists(p)
                  or _sha256_file(p) != ref.get("sha256")):
            problems.append("资格输入哈希错配：%s" % p)
    for i, c in enumerate(pack.get("criteria") or []):
        if c.get("status") not in STATES["criterion"]:
            problems.append("判据 %d 状态非法" % i)
        src = c.get("input_source") or {}
        if src.get("file"):
            sp = os.path.join(run_dir, src["file"])
            if not os.path.exists(sp) or _sha256_file(sp) != src["sha256"]:
                problems.append("判据 %d 输入来源哈希错配（%s）"
                                % (i, src["file"]))
    # 返修 v2 E 组 + 返修 v3 逐值绑定：核验独立重查整科齐备性与逐值一
    # 致性——按运行声明与批准科目及原结果文件重算必需判据与被测量集合，
    # 与包逐项逐值对应（value、unit、comparison、threshold、
    # criterion_version、standard_source、verdict_word、status；原始精度
    # 比对，不加容差）。包卡相互一致不代表完整：删除、增加、修改必要条
    # 目或篡改科学值都不能维持通过。
    if rec_live is not None:
        try:
            ident_re = _identity_from_record(rec_live)
            own_re, _foreign_re = _bind_ledgers(run_dir, rec_live, ident_re)
            redrived = _criteria(rec_live, run_dir, ident_re, own_re)

            def _proj(c):
                return json.dumps({
                    "subject_actual": c.get("subject_actual"),
                    "criterion_id": c.get("criterion_id"),
                    "quantity": c.get("quantity"),
                    "value": c.get("value"),
                    "unit": c.get("unit"),
                    "comparison": c.get("comparison"),
                    "threshold": c.get("threshold"),
                    "criterion_version": c.get("criterion_version"),
                    "standard_source": c.get("standard_source"),
                    "verdict_word": c.get("verdict_word"),
                    "status": c.get("status"),
                }, sort_keys=True, ensure_ascii=False)
            proj_pack = sorted(_proj(c) for c in pack.get("criteria") or [])
            proj_re = sorted(_proj(c) for c in redrived)
            if proj_pack != proj_re:
                problems.append("核验重查：包判据与按运行声明及原结果重算"
                                "不符（删除、增加、修改必要条目或科学值与"
                                "原结果不符都不能维持通过）")
            scope_re = _criteria_scope(
                (rec_live.get("config") or {}).get("criteria") or {})
            if json.dumps(scope_re, sort_keys=True, ensure_ascii=False) \
                    != json.dumps(pack.get("criteria_scope") or {},
                                  sort_keys=True, ensure_ascii=False):
                problems.append("核验重查：判据声明范围与包不符")
            # 必要清单重查（返修 v3）：按同一批准来源、分支与事实（含重
            # 算判据的分析输入事实）重算；缺口或待审而包称完备或判 pass
            # 即为问题。
            if cond and os.path.exists(os.path.join(run_dir, cond)):
                live = _load_json(os.path.join(run_dir, cond))
                live_nec = _necessary_report(
                    live, ((pack.get("quantity_meanings") or {})
                           .get("actual_mapping") or {}),
                    rec_live, redrived, run_dir)
                if live_nec.get("status") in ("deficient", "pending_source") \
                        and ((pack.get("necessary_conditions") or {})
                             .get("status") == "complete"
                             or (pack.get("experiment_verdict") or {})
                             .get("status") == "pass"):
                    problems.append("必要清单重查与包声明不符（%s；%s）"
                                    % (live_nec.get("status"),
                                       str(live_nec.get("reason"))[:140]))
                # 场景比较重查（返修 v4）：按条件与同运行见证重算实际与
                # 批准场景的比较；包内比较被改或不符仍称适用即拒。
                crit_cfg_live = (rec_live.get("config") or {}).get(
                    "criteria") or {}
                if crit_cfg_live.get("kind") in ("bench_subject",
                                                 "bench_criterion") \
                        and crit_cfg_live.get("subject") == "hover":
                    cmp_re = _scenario_comparison(
                        live, live_nec.get("facts") or {}, rec_live, run_dir)
                    stored_cmp = pack.get("approved_scenario_comparison")
                    if json.dumps(cmp_re, sort_keys=True,
                                  ensure_ascii=False) \
                            != json.dumps(stored_cmp or {}, sort_keys=True,
                                          ensure_ascii=False):
                        problems.append("核验重查：批准场景比较与包不符")
                    if cmp_re.get("verdict") == "divergent" \
                            and (pack.get("applicability") or {}).get(
                                "status") == "applicable":
                        problems.append("核验重查：实际与批准场景不符而包"
                                        "称适用")
        except Exception as exc:
            problems.append("整科齐备性重查失败：%r" % exc)
    qm = ((pack.get("quantity_meanings") or {}).get("actual_mapping") or {})
    for ref in qm.get("input_refs") or []:
        p = ref.get("path")
        if p and (not os.path.exists(p)
                  or _sha256_file(p) != ref.get("sha256")):
            problems.append("记录量来源哈希错配：%s" % p)
    v, _r = compute_verdict(pack)
    if (pack.get("experiment_verdict") or {}).get("status") != v:
        problems.append("实验判定与规则不符")
    card_path = os.path.join(run_dir, CARD_NAME)
    if not os.path.exists(card_path):
        problems.append("结果卡缺失")
    else:
        card = _load_json(card_path)
        if card.get("publish_id") != pack.get("publish_id"):
            problems.append("包卡发布代号不一致（混代）")
        for k in ("experiment_id", "item_id", "run_id"):
            if card.get(k) != ident.get(k):
                problems.append("结果卡身份 %s 与包不一致" % k)
        if card.get("experiment_verdict") != \
                (pack.get("experiment_verdict") or {}).get("status"):
            problems.append("结果卡判定与包不一致")
        pc = {json.dumps(x, sort_keys=True) for x in card.get("criteria", [])}
        pk = {json.dumps({"subject_declared": c.get("subject_declared"),
                          "subject_actual": c.get("subject_actual"),
                          "criterion_id": c.get("criterion_id"),
                          "quantity": c.get("quantity"),
                          "status": c["status"]}, sort_keys=True)
              for c in pack.get("criteria") or []}
        if pc != pk:
            problems.append("结果卡逐条判据与包不一致")
    return {"pass": not problems, "problems": problems,
            "verdict": (pack.get("experiment_verdict") or {}).get("status"),
            "generated_at": _now()}


def main(argv):
    if len(argv) >= 3 and argv[0] == "capture":
        capture_conditions(argv[1], _load_json(argv[2]))
        print("[COND] 运行起始条件已采集 → %s"
              % os.path.join(argv[1], CONDITIONS_NAME))
        return 0
    if len(argv) >= 2 and argv[0] == "build":
        pack = build_pack(argv[1])
        print("[PACK] 判定 %s（%s）"
              % (pack["experiment_verdict"]["status"],
                 pack["experiment_verdict"]["reason"]))
        return 0
    if len(argv) >= 2 and argv[0] == "verify":
        r = verify_pack(argv[1])
        print("[VERIFY] %s：%s" % ("过" if r["pass"] else "拒",
                                   "；".join(r["problems"])))
        return 0 if r["pass"] else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
