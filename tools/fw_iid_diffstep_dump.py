"""FW-IID 首差步邻域导出器（方案 5.4 回溯导出的执行件）。

对两份台账在指定步（默认首差步）前后各若干步，打印全部通道的数值
对照与位差，用于判读注入形态（末位放大族对单步跳入族）。

用法：python tools/fw_iid_diffstep_dump.py A.bin B.bin [--step N] [--w 2]
"""

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))
from modules.fw_iid_ledger import load


def f32ulp(x):
    import struct as s
    b = s.unpack("<I", s.pack("<f", float(x)))[0]
    return b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--step", type=int, default=None,
                    help="中心步（默认取首差步）")
    ap.add_argument("--w", type=int, default=2, help="前后窗步数")
    a = ap.parse_args()
    ha, A = load(a.a)
    hb, B = load(a.b)
    jn = ha.get("joint_names") or [f"j{i}" for i in range(9)]
    sl = {}
    i = 1
    for n, c in ha["channels"]:
        sl[n] = (i, c)
        i += c
    sa = {int(s): r for r, s in enumerate(A[:, 0])}
    sb = {int(s): r for r, s in enumerate(B[:, 0])}
    steps = sorted(set(sa) & set(sb))
    if a.step is None:
        Au = A.view(np.uint64)
        Bu = B.view(np.uint64)
        T = min(len(A), len(B))
        neq = Au[:T] != Bu[:T]
        rows = np.flatnonzero(neq.any(axis=1))
        if len(rows) == 0:
            print("两台账逐位相同，无首差步")
            return 0
        center = int(A[rows[0], 0])
    else:
        center = a.step
    for s in range(max(1, center - a.w), min(steps[-1], center + a.w) + 1):
        ra, rb = sa[s], sb[s]
        diffs = []
        for n, (cs, cn) in sl.items():
            for j in range(cn):
                va, vb = float(A[ra, cs + j]), float(B[rb, cs + j])
                ba, bb = f32ulp(va), f32ulp(vb)
                if ba != bb or A[ra, cs + j].view(np.uint64) != \
                        B[rb, cs + j].view(np.uint64):
                    lab = (f"{jn[j]}" if n in ("jp", "jv", "je", "tgt")
                           and j < len(jn) else f"[{j}]")
                    diffs.append(f"{n}.{lab}: {va:+.9e} 对 {vb:+.9e}"
                                 f"（差 {vb - va:+.3e}，32位位差"
                                 f"{abs(ba - bb)}）")
        head = (f"步 {s}（t={s * float(ha.get('phys_dt') or 0):.3f}s）"
                f" 差通道数 {len(diffs)}")
        print(head)
        for d in diffs:
            print("  " + d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
