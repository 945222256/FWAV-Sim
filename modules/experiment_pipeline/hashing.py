# -*- coding: utf-8 -*-
"""hashing.py 实验配置的规范化序列化与摘要计算，事项 I-015。

规范化方法如下。把配置树递归展开为记录序列。容器节点先落一条自身记
录，再展开子节点。每条记录由三段构成，依次为路径、类型标记、值文
本。段间用固定分隔符 FIELD_SEP 分开，记录间用固定分隔符 RECORD_SEP 分
开；两个分隔符各为一个控制字符，码位分别为三十一与三十。字典的键按
字符码升序排序，列表按原顺序展开并以序号为路径段。类型标记区分整
数、浮点数、字符串、布尔值、空值、字典、列表七种，保证整数一、浮点
数一点零与字符串形式的一互不相同；空字典与空列表也因自身记录的不同
而互不相同。

摘要方法如下。对规范化文本按 utf-8 编码取字节，用 hashlib 的 sha256
求摘要，输出完整六十四位十六进制串；十二位短标识取完整摘要的前十二
位。

三条不变量由 selftest 函数逐条验证。第一条为同配置两次计算得到逐字
节相同的规范化文本与相同摘要。第二条为改动任意一个值后摘要必然改
变。第三条为键的书写顺序重排不改变摘要。本文件仅使用 Python 标准
库。
"""

import copy
import hashlib

# 记录间与段间的固定分隔符，码位分别为三十与三十一。
RECORD_SEP = "\x1e"
FIELD_SEP = "\x1f"

# 短标识位数。
SHORT_LEN = 12


def _emit(prefix, value, out):
    """把以 prefix 为路径的值展开为记录，追加进 out 列表。"""
    if isinstance(value, bool):
        out.append(FIELD_SEP.join(prefix + ["bool", "1" if value else "0"]))
    elif isinstance(value, int):
        out.append(FIELD_SEP.join(prefix + ["int", repr(value)]))
    elif isinstance(value, float):
        out.append(FIELD_SEP.join(prefix + ["float", repr(value)]))
    elif isinstance(value, str):
        out.append(FIELD_SEP.join(prefix + ["str", value]))
    elif value is None:
        out.append(FIELD_SEP.join(prefix + ["null", ""]))
    elif isinstance(value, list):
        out.append(FIELD_SEP.join(prefix + ["seq", str(len(value))]))
        for index, item in enumerate(value):
            _emit(prefix + ["#" + str(index)], item, out)
    elif isinstance(value, dict):
        out.append(FIELD_SEP.join(prefix + ["map", str(len(value))]))
        for key in sorted(value.keys()):
            _emit(prefix + [key], value[key], out)
    else:
        raise TypeError("配置含不支持的类型 %s，路径为 %s"
                        % (type(value).__name__, "/".join(prefix)))


def canonical_text(config):
    """返回配置的规范化文本。同配置必然返回逐字节相同的字符串。"""
    out = []
    _emit([], config, out)
    return RECORD_SEP.join(out)


def digest_hex(config):
    """返回配置摘要的完整六十四位十六进制串。"""
    return hashlib.sha256(canonical_text(config).encode("utf-8")).hexdigest()


def short_id(config):
    """返回配置摘要的前十二位短标识。"""
    return digest_hex(config)[:SHORT_LEN]


def selftest():
    """验证摘要的三条不变量，返回问题文本清单，空清单即全部通过。"""
    sample = {
        "scene": {
            "argv": ["python", "-m", "modules.backend.bench_runner", "--fast"],
            "mode": "python",
            "duration_s": 60,
        },
        "overrides": {"FLAPPER_MAX_S": "60", "FLAPPER_HEADLESS": "1"},
        "criteria": {"kind": "bench_subject", "subject": "hover"},
        "locks": {"standard": True},
        "tags": {"name": "SELFTEST", "desc": "摘要自测"},
    }
    problems = []

    text_a = canonical_text(sample)
    digest_a = digest_hex(sample)

    # 第一条不变量：同配置两次计算逐字节相同。
    if canonical_text(sample) != text_a or digest_hex(sample) != digest_a:
        problems.append("同配置两次计算的规范化文本或摘要不一致")

    # 第三条不变量：键序重排不改变摘要。
    reordered = {
        "tags": {"desc": "摘要自测", "name": "SELFTEST"},
        "locks": {"standard": True},
        "criteria": {"subject": "hover", "kind": "bench_subject"},
        "overrides": {"FLAPPER_HEADLESS": "1", "FLAPPER_MAX_S": "60"},
        "scene": {
            "duration_s": 60,
            "mode": "python",
            "argv": ["python", "-m", "modules.backend.bench_runner",
                     "--fast"],
        },
    }
    if digest_hex(reordered) != digest_a:
        problems.append("键序重排后摘要改变，排序规范化失效")

    # 第二条不变量：任意一处改动必换摘要。
    mutations = []

    mutated = copy.deepcopy(sample)
    mutated["overrides"]["FLAPPER_MAX_S"] = "61"
    mutations.append(("参数值六十改六十一", mutated))

    mutated = copy.deepcopy(sample)
    mutated["scene"]["duration_s"] = 61
    mutations.append(("时长六十改六十一", mutated))

    mutated = copy.deepcopy(sample)
    mutated["scene"]["duration_s"] = 60.0
    mutations.append(("整数六十改浮点数六十点零", mutated))

    mutated = copy.deepcopy(sample)
    mutated["tags"]["name"] = "SELFTEST2"
    mutations.append(("实验名改一个字符", mutated))

    mutated = copy.deepcopy(sample)
    mutated["scene"]["argv"].append("--extra")
    mutations.append(("命令追加一个参数", mutated))

    mutated = copy.deepcopy(sample)
    del mutated["overrides"]["FLAPPER_HEADLESS"]
    mutations.append(("删除一个参数键", mutated))

    mutated = copy.deepcopy(sample)
    mutated["locks"]["standard"] = 1
    mutations.append(("锁声明真值改为整数一", mutated))

    for name, mutated_config in mutations:
        if digest_hex(mutated_config) == digest_a:
            problems.append("改动 %s 后摘要未变" % name)

    # 附加对照：空字典与空列表必须得到不同摘要。
    with_empty_dict = copy.deepcopy(sample)
    with_empty_dict["extra"] = {}
    with_empty_list = copy.deepcopy(sample)
    with_empty_list["extra"] = []
    if digest_hex(with_empty_dict) == digest_hex(with_empty_list):
        problems.append("空字典与空列表摘要相同，容器记录失效")

    return problems


if __name__ == "__main__":
    issues = selftest()
    if issues:
        for line in issues:
            print("[拒] " + line)
        raise SystemExit(1)
    print("[过] 摘要自测通过：同配置同摘要，键序无关，任意一处改动换摘要")
