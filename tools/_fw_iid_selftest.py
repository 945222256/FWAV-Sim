"""FW-IID 台账仪器自测（不启动 Isaac，纯数据层往返验证）。

覆盖三案：其一，人工构造的末位注入必须被比较器定位到正确步与正确通
道；其二，完全相同的两份台账必须判逐位相同；其三，损坏档案必须被判
格式错误。全部通过输出 SELFTEST PASS，任一失败非零退出。
"""

import os
import struct
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))
from modules.fw_iid_ledger import (CHAN_LAYOUT, LedgerRecorder, compare,
                                   load)

N = sum(n for _, n in CHAN_LAYOUT)


def synth(path, ulp_step=None, ulp_col=None):
    """构造确定性台账：步号 + 平滑序列；可选在指定步指定列注入一个
    三十二位末位单位。"""
    rec = LedgerRecorder(path, [f"j{i}" for i in range(9)], 1.0 / 120.0,
                         meta={"tag": "selftest"})
    rng_vals = {}
    for s in range(12):
        fields = {}
        for name, n in CHAN_LAYOUT:
            base = np.arange(n, dtype=float) * 0.25 + s * 0.01 + hash(name) % 7
            fields[name] = base
            rng_vals[(s, name)] = base.copy()
        if ulp_step is not None and s == ulp_step and ulp_col is not None:
            name, idx = ulp_col
            v = np.float32(fields[name][idx])
            bits = struct.unpack("<I", struct.pack("<f", float(v)))[0] ^ 1
            fields[name][idx] = struct.unpack("<f", struct.pack("<I", bits))[0]
        rec.record(s, fields)
    rec.close()
    return rec.path


def main():
    tmp = tempfile.mkdtemp(prefix="fw_iid_selftest_")
    ok = True

    # 案一：末位注入定位
    a = synth(os.path.join(tmp, "a.bin"))
    b = synth(os.path.join(tmp, "b.bin"), ulp_step=5, ulp_col=("tgt", 2))
    jo = os.path.join(tmp, "case1.json")
    rc = compare(a, b, json_out=jo)
    import json
    rep = json.load(open(jo, encoding="utf-8"))
    c1 = (rc == 1 and rep["s_star"] == 5
          and rep["first_channel"].startswith("tgt.")
          and rep["first_bits32"] == 1
          and rep["n_diff_steps"] >= 1)
    print(f"案一 末位注入定位: {'PASS' if c1 else 'FAIL'}"
          f" (rc={rc}, s*={rep['s_star']}, c*={rep['first_channel']},"
          f" bits32={rep['first_bits32']})")
    ok = ok and c1

    # 案二：完全相同
    c = synth(os.path.join(tmp, "c.bin"))
    rc2 = compare(a, c)
    c2 = rc2 == 0
    print(f"案二 逐位相同判定: {'PASS' if c2 else 'FAIL'} (rc={rc2})")
    ok = ok and c2

    # 案三：损坏档案
    bad = os.path.join(tmp, "bad.bin")
    with open(bad, "wb") as f:
        f.write(b"NOTALEDR" + b"\x00" * 64)
    rc3 = compare(a, bad)
    c3 = rc3 == 2
    print(f"案三 格式错误检测: {'PASS' if c3 else 'FAIL'} (rc={rc3})")
    ok = ok and c3

    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
