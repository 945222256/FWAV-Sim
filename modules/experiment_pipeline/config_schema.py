# -*- coding: utf-8 -*-
"""config_schema.py 实验配置模式定义与校验，事项 I-015。

一份实验配置是一个 JSON 文件。文件根为对象，含五个必填节，节名固定为
scene、overrides、criteria、locks、tags，缺任何一节即拒绝运行，出现未
知节同样拒绝。判据先于数据的纪律内建于本模式：判据节缺失，或判据节声
明的科目在基准判据文件中不存在时，校验不通过，运行器拒绝启动实验。
可选节共五个：chain、importance、necessary_conditions、item、baseline
（事项 I-186 增后三个）。item 声明本次实验归属的事项编号；baseline 声
明基准对比标识；necessary_conditions 承接批准配方的必要条件键清单、必
需记录量清单与批准来源，运行器原样保存入运行记录，起始采集随运行留
档，缺项检查逐键核对，空清单不豁免无条件核对的必要项；条件键须在参
数节 overrides 声明（跨节核对，缺声明即拒绝运行，保证运行器有键可注
入子进程）。

各节字段约定如下。

场景节 scene 含三个键。键 argv 为命令词列表，首元素为可执行程序，其
余元素为该程序的参数。键 mode 为模式，取值 isaac 或 python；isaac 表
示命令启动 Isaac 在环仿真，python 表示命令为纯 Python 实验工具。键
duration_s 为实验时长秒数，必须为正的有限数。

参数节 overrides 为环境变量键值表，运行时由运行器逐项注入子进程。键
必须以 FLAPPER_ 开头，值必须为非空字符串。标准锁五变量与标注两变量不
许出现在本节：锁五变量由运行器按锁节声明自动补全，标注两变量由标注
节映射生成。

判据节 criteria 为判据来源声明，两种形态二选一。形态一声明基准判据文
件科目名：键 kind 取值 bench_subject，键 subject 取
config/bench_criteria.json 中 subjects 下的科目名，校验器核对科目真实
存在。形态二声明判定脚本：键 kind 取值 verdict_script，键 script 为指
向判定脚本的一句话描述。两形态均可附键 note 写补充说明。

锁节 locks 声明锁组合。现行唯一支持为标准锁组合：键 standard 取值
true。锁五变量名见 STANDARD_LOCK_VARS，其取值以
modules/backend/bench_runner.py 的 STANDARD_LOCK 为唯一来源，运行器
负责核对两处一致。

标注节 tags 含两个键。键 name 为实验名，运行时映射到环境变量
FLAPPER_TAG。键 desc 为实验描述，运行时映射到环境变量 FLAPPER_DESC。

全部字符串键与字符串值不许包含两个保留控制字符，其码位为三十与三十
一。这两个字符留作摘要规范化的固定分隔符，混入数据会破坏摘要的唯一
性。

校验函数返回问题清单，清单为空即通过。问题文本为中文，逐条可直接张
贴。本文件仅使用 Python 标准库。
"""

import json
import math
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

# 五个必填节名。
REQUIRED_SECTIONS = ("scene", "overrides", "criteria", "locks", "tags")

# 场景节允许的键与模式取值。
SCENE_KEYS = ("argv", "mode", "duration_s")
SCENE_MODES = ("isaac", "python")

# 判据节三种形态与公共可选键。bench_criterion 为单判据形态（事项
# I-186）：声明批准科目内单一判据条目为本次批准范围，整科必需判据集
# 合相应收窄为该条；结果文件不得自选子集代称整科。
CRITERIA_KINDS = ("bench_subject", "bench_criterion", "verdict_script")
CRITERIA_FILE = os.path.join(_REPO_ROOT, "config", "bench_criteria.json")

# 锁节允许的键。
LOCKS_KEYS = ("standard",)

# 标注节允许的键。
TAGS_KEYS = ("name", "desc")

# 必要条件节为可选节（事项 I-186）：承接批准配方声明的必要条件键、必
# 需记录量清单与批准来源。conditions 为参数节键清单（运行时逐一注入
# 子进程，起始采集与缺项检查逐键核对）；quantities 为必需记录量清单
# （构包时对照实际记录量映射逐项核对）；source 为批准来源引用。空清
# 单不豁免任何无条件核对的必要项，清单只加不减。
NECESSARY_KEYS = ("source", "conditions", "quantities", "note")

# 链节为可选节，事项 I-078：一份配置一条命令完成采集加拟合加入库。
# 链为步骤列表，逐步顺序执行；isaac 步骤缺省命令时回落到场景节命令，
# python 步骤必须自带命令。命令词支持五个占位符，运行时替换：采集数据
# 流路径、最近一次采集的有效标注、归档目录绝对路径、本次实验标识、本
# 次运行标识（后两项事项 I-186，供分析步把分数绑定到本次运行）。
# 事项 I-083 扩展：步骤可携带自己的参数覆盖节，只作用于该步；链内允
# 许多个 isaac 采集步顺序执行构成配置族，如多坡度批量采集。
CHAIN_STEP_KEYS = ("name", "mode", "argv", "overrides")
CHAIN_PLACEHOLDERS = ("{collect_csv}", "{tag}", "{archive_dir}",
                      "{experiment_id}", "{run_id}")
# python 步骤的运行墙钟上限秒数，与运行器约定一致。
CHAIN_PYTHON_WALL_CAP_S = 1800.0

# 参数节允许的环境变量前缀。
ENV_PREFIX = "FLAPPER_"

# 标准锁五变量名。取值唯一来源为 modules/backend/bench_runner.py 的
# STANDARD_LOCK，此处只登记名字供校验与核对。
STANDARD_LOCK_VARS = (
    "FLAPPER_POST_STEP_CB",
    "FLAPPER_PHYS_CPU",
    "FLAPPER_IID_LOCK",
    "FLAPPER_OBSTACLE_SEED",
    "FLAPPER_OBSTACLE_COUNT",
    "FLAPPER_DET_ON",
)

# 标注两变量对应的环境变量名。
TAG_ENV_KEYS = ("FLAPPER_TAG", "FLAPPER_DESC")

# 摘要规范化保留的两个控制字符，禁止出现在任何键与字符串值内。
RESERVED_CHARS = ("\x1e", "\x1f")

_TYPE_NAMES_ZH = {
    dict: "对象",
    list: "列表",
    str: "字符串",
    bool: "布尔值",
    int: "整数",
    float: "浮点数",
    type(None): "空值",
}


def _type_zh(value):
    """返回值的中文类型名，用于错误信息。"""
    return _TYPE_NAMES_ZH.get(type(value), type(value).__name__)


def _check_reserved(text, where, problems):
    """检查字符串是否含保留控制字符，命中即登记问题。"""
    if not isinstance(text, str):
        return
    for ch in RESERVED_CHARS:
        if ch in text:
            problems.append(
                "%s 含保留控制字符，码位为 %d；该字符留作摘要分隔符，"
                "不许写入配置" % (where, ord(ch)))


def _check_ascii_star(value, where, problems):
    """全库禁半角星号，事项 I-104：半角星为系统禁用字符，星标一律
    全角。对配置内任意层级的字符串值递归检查。"""
    if isinstance(value, str):
        if "*" in value:
            problems.append("%s 的字符串含半角星号；半角星为系统禁用字"
                            "符，星标一律用全角星" % where)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _check_ascii_star(item, "%s 第 %d 项" % (where, i + 1),
                              problems)
    elif isinstance(value, dict):
        for k, v in value.items():
            _check_ascii_star(v, "%s 的键 %s" % (where, k), problems)


def _criteria_subject_names():
    """读基准判据文件的科目名列表；文件不可读或结构不符时返回 None。"""
    try:
        with open(CRITERIA_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    subjects = data.get("subjects") if isinstance(data, dict) else None
    if not isinstance(subjects, dict):
        return None
    return sorted(subjects.keys())


def _subject_criterion_ids(subject):
    """读基准判据文件某科目的判据条目编号列表；不可读返回 None。"""
    try:
        with open(CRITERIA_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    sub = ((data.get("subjects") or {}).get(subject)
           if isinstance(data, dict) else None)
    if not isinstance(sub, dict) or not isinstance(sub.get("criteria"), list):
        return None
    return [c.get("id") for c in sub["criteria"]
            if isinstance(c, dict) and c.get("id")]


def _validate_necessary(necessary, problems):
    """校验必要条件节（可选节，事项 I-186）。"""
    if not isinstance(necessary, dict):
        problems.append("necessary_conditions 节必须是 JSON 对象，实际为 %s"
                        % _type_zh(necessary))
        return
    for key in sorted(set(necessary.keys()) - set(NECESSARY_KEYS)):
        problems.append("necessary_conditions 节含未知键 %s；只许 %s"
                        % (key, "、".join(NECESSARY_KEYS)))
    src = necessary.get("source")
    if src is not None and (not isinstance(src, str) or not src.strip()):
        problems.append("necessary_conditions 节键 source 必须为非空字符"
                        "串（批准配方来源引用）")
    _check_reserved(src, "necessary_conditions 节键 source", problems)
    for key in ("conditions", "quantities"):
        val = necessary.get(key)
        if val is None:
            continue
        if not isinstance(val, list):
            problems.append("necessary_conditions 节键 %s 必须为字符串列"
                            "表，实际为 %s" % (key, _type_zh(val)))
            continue
        for i, item in enumerate(val):
            if not isinstance(item, str) or not item.strip():
                problems.append("necessary_conditions 节 %s 第 %d 项必须"
                                "为非空字符串" % (key, i + 1))
            _check_reserved(item, "necessary_conditions 节 %s 第 %d 项"
                            % (key, i + 1), problems)
    note = necessary.get("note")
    if note is not None and (not isinstance(note, str) or not note):
        problems.append("necessary_conditions 节键 note 必须为非空字符串")
    _check_reserved(note, "necessary_conditions 节键 note", problems)
    # 来源引用须可解析（事项 I-186 返修 v2）：file 或 file#fragment；文
    # 件须存在、可读为 JSON 且带版本或冻结标记（未带标记的文件不作为批
    # 准定义来源）；片段须为文件内科目或顶层键。不存在、未批准或片段缺
    # 失在配置校验即拒绝运行，不等到构包才发现。
    src = necessary.get("source")
    if isinstance(src, str) and src.strip():
        if "#" in src:
            file_part, frag = src.split("#", 1)
            frag = frag or None
        else:
            file_part, frag = src, None
        src_path = os.path.join(_REPO_ROOT, file_part)
        if not os.path.isfile(src_path):
            problems.append("necessary_conditions 节来源文件不存在：%s"
                            "（批准来源须可解析，拒绝运行）" % file_part)
        else:
            try:
                with open(src_path, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (OSError, ValueError) as exc:
                problems.append("necessary_conditions 节来源文件不可读："
                                "%s：%r" % (file_part, exc))
                doc = None
            if isinstance(doc, dict):
                version = doc.get("version") or doc.get("frozen_at")
                if not (isinstance(version, str) and version.strip()):
                    problems.append("necessary_conditions 节来源 %s 未带"
                                    "版本或冻结标记，不作为批准定义来源"
                                    % file_part)
                if frag:
                    subs = doc.get("subjects")
                    if not (isinstance(subs, dict) and frag in subs) \
                            and frag not in doc:
                        problems.append("necessary_conditions 节来源片段 "
                                        "%s 不在 %s 内" % (frag, file_part))


def _validate_necessary_in_config(config, problems):
    """必要条件键与参数节的跨节核对（事项 I-186 返修 v3 收窄）。

    批准清单的 conditions 是实际事实标识（source、run、clock 等），不是
    环境变量名，不按环境键核对；仅 FLAPPER_ 前缀的条件键属环境键语义，
    必须在 overrides 节声明（运行器才有键可注入子进程）。清单只加不
    减：空清单合法（不豁免必要项），非空清单逐键核对。
    """
    necessary = config.get("necessary_conditions")
    if not isinstance(necessary, dict):
        return
    conds = necessary.get("conditions")
    if not isinstance(conds, list):
        return
    overrides = config.get("overrides")
    declared_keys = set(overrides) if isinstance(overrides, dict) else set()
    for key in conds:
        if isinstance(key, str) and key.startswith("FLAPPER_") \
                and key not in declared_keys:
            problems.append(
                "necessary_conditions 节声明的环境键条件 %s 未在 overrides "
                "参数节声明，运行器无从注入子进程，拒绝运行" % key)


def _validate_scene(scene, problems):
    """校验场景节，问题登记进 problems。"""
    if not isinstance(scene, dict):
        problems.append("scene 节必须是 JSON 对象，实际为 %s"
                        % _type_zh(scene))
        return
    for key in sorted(set(scene.keys()) - set(SCENE_KEYS)):
        problems.append("scene 节含未知键 %s；只许 argv、mode、duration_s "
                        "三键" % key)
    for key in SCENE_KEYS:
        if key not in scene:
            problems.append("scene 节缺少键 %s" % key)

    if "argv" in scene:
        argv = scene["argv"]
        if not isinstance(argv, list) or not argv:
            problems.append("scene 节 argv 必须为至少含一个元素的字符串"
                            "列表，实际为 %s" % _type_zh(argv))
        else:
            for word in argv:
                if not isinstance(word, str) or not word:
                    problems.append("scene 节 argv 的每个元素必须为非空字"
                                    "符串，存在违规元素 %r" % (word,))

    if "mode" in scene:
        mode = scene["mode"]
        if mode not in SCENE_MODES:
            problems.append("scene 节 mode 必须取 isaac 或 python，实际"
                            "为 %r" % (mode,))

    if "duration_s" in scene:
        duration = scene["duration_s"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            problems.append("scene 节 duration_s 必须为数值，实际为 %s"
                            % _type_zh(duration))
        elif not math.isfinite(duration) or duration <= 0:
            problems.append("scene 节 duration_s 必须为正的有限数，实际"
                            "为 %r" % (duration,))


def _validate_overrides(overrides, problems):
    """校验参数节，问题登记进 problems。"""
    if not isinstance(overrides, dict):
        problems.append("overrides 节必须是 JSON 对象，实际为 %s"
                        % _type_zh(overrides))
        return
    for key in sorted(overrides.keys()):
        value = overrides[key]
        where = "参数节键 %s" % key
        if not isinstance(key, str) or not key.startswith(ENV_PREFIX):
            problems.append("%s 不以 %s 开头；参数节只允许该前缀的环境变"
                            "量键" % (where, ENV_PREFIX))
        if not isinstance(value, str) or not value:
            problems.append("%s 的值必须为非空字符串；环境变量值在配置中"
                            "以字符串书写" % where)
        if key in STANDARD_LOCK_VARS:
            problems.append("%s 属于标准锁五变量；锁值由运行器按锁节自动"
                            "补全，配置里不许手抄" % where)
        if key in TAG_ENV_KEYS:
            problems.append("%s 属于标注变量；标注由标注节映射生成，不许"
                            "在参数节书写" % where)
        _check_reserved(key, where, problems)
        _check_reserved(value, where + " 的值", problems)


def _validate_criteria(criteria, problems):
    """校验判据节，问题登记进 problems。"""
    if not isinstance(criteria, dict):
        problems.append("criteria 节必须是 JSON 对象，实际为 %s"
                        % _type_zh(criteria))
        return
    kind = criteria.get("kind")
    if kind not in CRITERIA_KINDS:
        problems.append("criteria 节必须给键 kind，取值 bench_subject、"
                        "bench_criterion 或 verdict_script，实际为 %r；判"
                        "据先于数据，未声明判据来源的实验不许运行" % (kind,))
        return
    if kind == "bench_subject":
        allowed = {"kind", "subject", "note"}
        for key in sorted(set(criteria.keys()) - allowed):
            problems.append("criteria 节在 bench_subject 形态下含未知键 "
                            "%s；只许 kind、subject、note 三键" % key)
        subject = criteria.get("subject")
        if not isinstance(subject, str) or not subject:
            problems.append("criteria 节为 bench_subject 形态时必须给非空"
                            "字符串键 subject")
        else:
            names = _criteria_subject_names()
            if names is None:
                problems.append("基准判据文件 %s 不可读或结构不符，科目归"
                                "属无法核对，拒绝运行" % CRITERIA_FILE)
            elif subject not in names:
                problems.append("criteria 节科目 %s 不在基准判据文件内；"
                                "可取值为 %s" % (subject, "、".join(names)))
    elif kind == "bench_criterion":
        allowed = {"kind", "subject", "criterion", "note"}
        for key in sorted(set(criteria.keys()) - allowed):
            problems.append("criteria 节在 bench_criterion 形态下含未知键 "
                            "%s；只许 kind、subject、criterion、note 四键"
                            % key)
        subject = criteria.get("subject")
        crit_id = criteria.get("criterion")
        if not isinstance(subject, str) or not subject:
            problems.append("criteria 节为 bench_criterion 形态时必须给非"
                            "空字符串键 subject")
        if not isinstance(crit_id, str) or not crit_id:
            problems.append("criteria 节为 bench_criterion 形态时必须给非"
                            "空字符串键 criterion（科目内判据条目编号）")
        if isinstance(subject, str) and subject \
                and isinstance(crit_id, str) and crit_id:
            ids = _subject_criterion_ids(subject)
            if ids is None:
                problems.append("基准判据文件 %s 不可读或科目 %s 结构不"
                                "符，判据条目归属无法核对，拒绝运行"
                                % (CRITERIA_FILE, subject))
            elif subject not in (_criteria_subject_names() or []):
                problems.append("criteria 节科目 %s 不在基准判据文件内"
                                % subject)
            elif crit_id not in ids:
                problems.append("criteria 节判据条目 %s 不在科目 %s 的判据"
                                "清单内；可取值为 %s"
                                % (crit_id, subject, "、".join(ids)))
    else:
        allowed = {"kind", "script", "note"}
        for key in sorted(set(criteria.keys()) - allowed):
            problems.append("criteria 节在 verdict_script 形态下含未知键 "
                            "%s；只许 kind、script、note 三键" % key)
        script = criteria.get("script")
        if not isinstance(script, str) or not script:
            problems.append("criteria 节为 verdict_script 形态时必须给非空"
                            "字符串键 script，内容为指向判定脚本的描述")
    note = criteria.get("note")
    if "note" in criteria and (not isinstance(note, str) or not note):
        problems.append("criteria 节键 note 必须为非空字符串")
    _check_reserved(note, "criteria 节键 note", problems)


def _validate_locks(locks, problems):
    """校验锁节，问题登记进 problems。"""
    if not isinstance(locks, dict):
        problems.append("locks 节必须是 JSON 对象，实际为 %s"
                        % _type_zh(locks))
        return
    for key in sorted(set(locks.keys()) - set(LOCKS_KEYS)):
        problems.append("locks 节含未知键 %s；锁值不许手抄，本节只许键 "
                        "standard 声明标准锁组合" % key)
    if "standard" not in locks:
        problems.append("locks 节缺少键 standard")
    elif locks.get("standard") is not True:
        problems.append("locks 节 standard 必须为 true；运行器按此声明自动"
                        "补全标准锁五变量，现行唯一支持的锁组合为标准锁")


def _validate_tags(tags, problems):
    """校验标注节，问题登记进 problems。"""
    if not isinstance(tags, dict):
        problems.append("tags 节必须是 JSON 对象，实际为 %s" % _type_zh(tags))
        return
    for key in sorted(set(tags.keys()) - set(TAGS_KEYS)):
        problems.append("tags 节含未知键 %s；只许 name 与 desc 两键" % key)
    for key, env_name in (("name", "FLAPPER_TAG"), ("desc", "FLAPPER_DESC")):
        if key not in tags:
            problems.append("tags 节缺少键 %s" % key)
            continue
        value = tags[key]
        if not isinstance(value, str) or not value.strip():
            problems.append("tags 节键 %s 必须为非空字符串，运行时映射到"
                            "环境变量 %s" % (key, env_name))
            continue
        _check_reserved(value, "tags 节键 %s" % key, problems)


def _validate_chain(chain, scene, problems):
    """校验可选链节，问题登记进 problems。事项 I-078。"""
    if not isinstance(chain, list) or not chain:
        problems.append("chain 节必须为非空列表，实际为 %s"
                        % _type_zh(chain))
        return
    seen_names = set()
    isaac_seen = False
    for pos, step in enumerate(chain):
        where = "chain 节第 %d 步" % (pos + 1)
        if not isinstance(step, dict):
            problems.append("%s 必须是 JSON 对象，实际为 %s"
                            % (where, _type_zh(step)))
            continue
        for key in sorted(set(step.keys()) - set(CHAIN_STEP_KEYS)):
            problems.append("%s 含未知键 %s；只许 name、mode、argv 三键"
                            % (where, key))
        name = step.get("name")
        if not isinstance(name, str) or not name:
            problems.append("%s 的 name 必须为非空字符串" % where)
        elif name in seen_names:
            problems.append("%s 的 name %r 与前序步骤重名" % (where, name))
        else:
            seen_names.add(name)
        st_over = step.get("overrides")
        if st_over is not None:
            if not isinstance(st_over, dict):
                problems.append("%s 的 overrides 必须是 JSON 对象，实际"
                                "为 %s" % (where, _type_zh(st_over)))
            else:
                for k in sorted(st_over.keys()):
                    v = st_over[k]
                    if not k.startswith(ENV_PREFIX):
                        problems.append("%s 步骤覆盖键 %s 不带 %s 前缀"
                                        % (where, k, ENV_PREFIX))
                    elif k in STANDARD_LOCK_VARS or k in TAG_ENV_KEYS:
                        problems.append("%s 步骤覆盖键 %s 属于锁或标注专用"
                                        "变量，不许在步骤内改" % (where, k))
                    elif not isinstance(v, str) or not v:
                        problems.append("%s 步骤覆盖键 %s 的值必须为非空"
                                        "字符串" % (where, k))
        mode = step.get("mode")
        if mode not in SCENE_MODES:
            problems.append("%s 的 mode 必须取 isaac 或 python，实际为 %r"
                            % (where, mode))
            continue
        argv = step.get("argv")
        if mode == "python":
            if not isinstance(argv, list) or not argv:
                problems.append("%s 为 python 步骤，必须自带非空命令列表"
                                % where)
                continue
        else:
            isaac_seen = True
            if argv is not None and (not isinstance(argv, list) or not argv):
                problems.append("%s 的 argv 给出时必须为非空命令列表"
                                % where)
                continue
        if isinstance(argv, list):
            for word in argv:
                if not isinstance(word, str) or not word:
                    problems.append("%s 命令词存在空元素" % where)
                    continue
                if word.startswith("{") and word.endswith("}"):
                    if word not in CHAIN_PLACEHOLDERS:
                        problems.append("%s 命令词 %s 不是受支持的占位符；"
                                        "只许 %s"
                                        % (where, word,
                                           "、".join(CHAIN_PLACEHOLDERS)))
                    elif mode == "isaac":
                        problems.append("%s 为 isaac 步骤，命令词不许用占"
                                        "位符 %s" % (where, word))
                    elif word == "{collect_csv}" and not isaac_seen:
                        problems.append("%s 使用采集数据流占位符，但其前没"
                                        "有任何 isaac 采集步骤" % where)
    if isaac_seen and isinstance(scene, dict) \
            and scene.get("mode") != "isaac":
        problems.append("chain 节含 isaac 步骤而场景节 mode 不是 isaac")


def validate_config(config):
    """校验一份已解析的实验配置，返回问题清单，空清单即通过。"""
    if not isinstance(config, dict):
        return ["配置根必须是 JSON 对象，实际为 %s" % _type_zh(config)]
    problems = []

    for section in REQUIRED_SECTIONS:
        if section not in config:
            if section == "criteria":
                problems.append("配置缺少判据节 criteria；判据先于数据，"
                                "未声明判据来源的实验不许运行")
            else:
                problems.append("配置缺少 %s 节；五个节 scene、overrides、"
                                "criteria、locks、tags 必填" % section)
    for key in sorted(set(config.keys()) - set(REQUIRED_SECTIONS)
                      - {"chain", "importance", "necessary_conditions",
                         "item", "baseline"}):
        problems.append("配置含未知顶层节 %s；只许 scene、overrides、"
                        "criteria、locks、tags 五个必填节与可选节 chain、"
                        "importance、necessary_conditions、item、baseline"
                        % key)
    imp = config.get("importance")
    if imp is not None and not isinstance(imp, bool):
        problems.append("importance 节必须为布尔值，实际为 %s"
                        % _type_zh(imp))
    item_id = config.get("item")
    if item_id is not None and (not isinstance(item_id, str)
                                or not item_id.strip()):
        problems.append("item 节必须为非空字符串（本次实验归属的事项编"
                        "号），实际为 %s" % _type_zh(item_id))
    _check_reserved(item_id, "item 节", problems)
    baseline_id = config.get("baseline")
    if baseline_id is not None and (not isinstance(baseline_id, str)
                                    or not baseline_id.strip()):
        problems.append("baseline 节必须为非空字符串（基准对比标识），实"
                        "际为 %s" % _type_zh(baseline_id))
    _check_reserved(baseline_id, "baseline 节", problems)
    _check_ascii_star(config, "配置", problems)

    if "scene" in config:
        _validate_scene(config["scene"], problems)
    if "overrides" in config:
        _validate_overrides(config["overrides"], problems)
    if "criteria" in config:
        _validate_criteria(config["criteria"], problems)
    if "locks" in config:
        _validate_locks(config["locks"], problems)
    if "tags" in config:
        _validate_tags(config["tags"], problems)
    if "necessary_conditions" in config:
        _validate_necessary(config["necessary_conditions"], problems)
        _validate_necessary_in_config(config, problems)
    if "chain" in config:
        _validate_chain(config["chain"], config.get("scene"), problems)
    return problems


def load_config(path):
    """读配置文件并校验。

    返回二元组，首为配置对象，读入或校验失败时为 None；次为问题清单，
    空清单即通过。
    """
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return None, ["配置文件不可读：%s" % exc]
    try:
        config = json.loads(text)
    except ValueError as exc:
        return None, ["配置文件不是合法的 JSON：%s" % exc]
    return config, validate_config(config)
