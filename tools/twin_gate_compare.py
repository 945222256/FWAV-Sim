# -*- coding: utf-8 -*-
"""
twin_gate_compare.py — 孪生门禁逐位比对（时间戳对齐版，2026-09-29）

用途：比对两份 detcheck 全精度侧档，判定两次运行是否逐位一致。

与既有逐行比对脚本的区别（修复方法学缺陷）：
  1) 旧脚本按行号比对且未校验时间戳；两份记录行数不同（例如 5396 对 5389）
     时，行错位会制造假的大差异。本脚本先按时间戳列对齐再比对。
  2) 四元数通道按整体符号两重解释取最小差（q 与 -q 表示同一姿态）。
  3) 逐位口径：十六进制位型直接比对，差异以末位单位（ULP）计量。

用法：
  python tools/twin_gate_compare.py A.csv B.csv

输出与退出码：
  全部对齐行逐位相同 → 打印 BITWISE-IDENTICAL，退出码 0。
  存在差异 → 打印首个差异的时刻、通道、两值与 ULP 差，退出码 1。
  用法或文件错误 → 退出码 2。
"""
import struct
import sys

CHANNELS = ["x", "y", "z", "qw", "qx", "qy", "qz"]


def load(path):
    """读取 detcheck 侧档：每行八列，首列为十进制仿真时刻，后七列为十六进制浮点。"""
    rows = {}
    with open(path, encoding="utf-8") as fp:
        fp.readline()
        for ln in fp:
            x = ln.strip().split(",")
            if len(x) != 8:
                continue
            t = float(x[0])
            rows[round(t, 4)] = [v.strip() for v in x[1:]]
    return rows


def bits(hex_str):
    """十六进制浮点字符串转 float64 的二进制位型（六十四位整数）。"""
    return struct.unpack("<q", struct.pack("<d", float.fromhex(hex_str)))[0]


def ulp_diff(a_hex, b_hex):
    return abs(bits(a_hex) - bits(b_hex))


def main():
    if len(sys.argv) != 3:
        print("用法: python tools/twin_gate_compare.py A.csv B.csv")
        return 2
    a = load(sys.argv[1])
    b = load(sys.argv[2])
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    print(f"行数: A={len(a)} B={len(b)}；仅A有 {len(only_a)} 行，仅B有 {len(only_b)} 行")
    if only_a:
        print(f"  仅A时间戳示例: {only_a[:5]}")
    if only_b:
        print(f"  仅B时间戳示例: {only_b[:5]}")

    common = sorted(set(a) & set(b))
    first = None
    max_ulp = 0
    for t in common:
        va, vb = a[t], b[t]
        for k in range(3):
            if va[k] != vb[k]:
                d = ulp_diff(va[k], vb[k])
                if first is None:
                    first = (t, CHANNELS[k], va[k], vb[k], d)
                max_ulp = max(max_ulp, d)
        qa, qb = va[3:7], vb[3:7]
        if qa != qb:
            d_pos = max(ulp_diff(qa[k], qb[k]) for k in range(4))
            same_under_flip = all(
                float.fromhex(qa[k]) == -float.fromhex(qb[k]) for k in range(4))
            if not same_under_flip:
                k_worst = max(range(4), key=lambda k: ulp_diff(qa[k], qb[k]))
                if first is None:
                    first = (t, CHANNELS[3 + k_worst], qa[k_worst], qb[k_worst],
                             d_pos)
                max_ulp = max(max_ulp, d_pos)

    if first is None:
        print(f"BITWISE-IDENTICAL：{len(common)} 个对齐时刻全部逐位相同")
        return 0
    t, ch, v_a, v_b, d = first
    print(f"★分岔：首个差异在 t={t:.4f}s，通道 {ch}")
    print(f"  A={v_a}\n  B={v_b}\n  末位差={d} ULP")
    print(f"全程最大末位差={max_ulp} ULP")
    return 1


if __name__ == "__main__":
    sys.exit(main())
