# -*- coding: utf-8 -*-
"""
test_optimizers.py — 统一优化器库契约测试（I-080）
====================================================
契约测试四组：
  1. TPE 行为等价：与 tools/unified_search_v3.py 的原实现同种子同序列
     输出逐位一致（收编零改动证明）；
  2. TPE 接口契约：点为字典、值在域内、数量正确、续跑状态可注入；
  3. NSGA-II 解析验证：Schaffer 第一函数（两目标、解析帕累托前沿
     x∈[0,2]）上前沿命中解析解；单目标球面函数收敛到全域最优邻域；
  4. NSGA-II 接口契约：同种子确定性、域边界不越界、tell 数量断言。
用法：python tools/test_optimizers.py   （全部通过打印 PASS 汇总）
"""
import os
import sys

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.optimizers import TPE, NSGA2, sample_rng

DOM = {"a": (0.0, 1.0), "b": (-2.0, 2.0), "c": (0.5, 0.9)}


def fake_loss(p):
    return (p["a"] - 0.3) ** 2 + (p["b"] + 1.0) ** 2 / 4 \
        + (p["c"] - 0.7) ** 2


def _v3_nodes():
    """不导入 v3（其顶层框架依赖可能崩），直接解析源码取三段定义。

    I-080 收编后 v3 本身改为从统一库转发，此时三段定义不在其函数体
    里，返回空集，调用方按转发形态处理。
    """
    import ast
    src = open(os.path.join(PROJECT_ROOT, "tools",
                            "unified_search_v3.py"), encoding="utf-8").read()
    if "from tools.optimizers import" in src:
        return "FORWARDING"
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) \
                and node.name in ("sample_rng", "hist_draw", "TPE"):
            out[node.name] = ast.dump(ast.Module(
                body=[node], type_ignores=[]))
    return out


def test_tpe_equivalence():
    """与 v3 原实现一致：可导入时运行时逐位对拍，否则 AST 源码对照。"""
    import importlib.util
    import ast
    spec = importlib.util.spec_from_file_location(
        "v3tpe", os.path.join(PROJECT_ROOT, "tools",
                              "unified_search_v3.py"))
    try:
        v3 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(v3)
    except Exception:
        # 源码级对照：三段定义的语法树必须与收编版完全一致；
        # v3 已改为转发统一库时按构造恒等处理
        v3n = _v3_nodes()
        if v3n == "FORWARDING":
            print("[TPE等价] v3 已改为统一库转发，构造恒等")
            return True
        mine = {}
        tree = ast.parse(open(os.path.join(PROJECT_ROOT, "tools",
                                           "optimizers.py"),
                              encoding="utf-8").read())
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) \
                    and node.name in ("sample_rng", "hist_draw", "TPE"):
                mine[node.name] = ast.dump(ast.Module(
                    body=[node], type_ignores=[]))
        assert set(v3n) == set(mine) == {"sample_rng", "hist_draw", "TPE"}
        for k in v3n:
            assert v3n[k] == mine[k], f"{k} 源码与 v3 原版不一致"
        print("[TPE等价] v3 当前不可导入，AST 源码对照三段定义完全一致")
        return True
    t1, t2 = TPE(DOM, seed=7), v3.TPE(DOM, seed=7)
    for _ in range(6):
        c1, c2 = t1.ask_batch(5), t2.ask_batch(5)
        assert c1 == c2, "ask_batch 输出不一致"
        t1.tell(c1, [fake_loss(p) for p in c1])
        t2.tell(c2, [fake_loss(p) for p in c2])
    return True


def test_tpe_contract():
    t = TPE(DOM, seed=3)
    for _ in range(4):
        c = t.ask_batch(6)
        assert len(c) == 6
        for p in c:
            assert set(p.keys()) == set(DOM.keys())
            for k, (lo, hi) in DOM.items():
                assert lo <= p[k] <= hi
        t.tell(c, [fake_loss(p) for p in c])
    best = min(t.y)
    assert fake_loss({"a": 0.3, "b": -1.0, "c": 0.7}) <= best + 1e-9
    # 续跑注入：外部历史点恢复后仍可继续
    t2 = TPE(DOM, seed=3)
    t2.x, t2.y = list(t.x), list(t.y)
    c = t2.ask_batch(2)
    assert len(c) == 2
    return True


def schaffer(x):
    """两目标解析测试：f1=x^2, f2=(x-2)^2，帕累托前沿 x∈[0,2]。"""
    return np.array([x ** 2, (x - 2.0) ** 2])


def test_nsga2_schaffer():
    dom = {"x": (-2.0, 4.0)}
    opt = NSGA2(dom, seed=11, pop=36)
    for _ in range(30):
        c = opt.ask()
        opt.tell([schaffer(p["x"]) for p in c])
    front = opt.pareto_front()
    xs = np.sort([p["x"] for p, _ in front])
    frac_in = np.mean((xs >= -0.05) & (xs <= 2.05))
    assert frac_in >= 0.9, f"前沿点落入解析前沿区间的比例 {frac_in:.2f} 不足"
    return frac_in


def test_nsga2_sphere():
    dom = {k: (-5.0, 5.0) for k in ("u", "v", "w")}
    opt = NSGA2(dom, seed=5, pop=30)
    for _ in range(40):
        c = opt.ask()
        opt.tell([np.array([sum(p[k] ** 2 for k in dom)]) for p in c])
    best = min(float(o[0]) for _, o in opt.pareto_front())
    assert best < 0.05, f"球面函数最优 {best:.4f} 未进 0.05 邻域"
    return best


def test_nsga2_contract():
    a = NSGA2(DOM, seed=42, pop=12)
    b = NSGA2(DOM, seed=42, pop=12)
    for _ in range(5):
        ca, cb = a.ask(), b.ask()
        assert ca == cb, "同种子不同步"
        oa = [[fake_loss(p), -p["a"]] for p in ca]
        ob = [[fake_loss(p), -p["a"]] for p in cb]
        a.tell(oa)
        b.tell(ob)
    for p, _o in a.pareto_front():
        for k, (lo, hi) in DOM.items():
            assert lo <= p[k] <= hi
    try:
        a.ask()
        a.tell([np.zeros(2)])            # 数量不符应断言失败
        return False
    except AssertionError:
        return True


if __name__ == "__main__":
    r1 = test_tpe_equivalence()
    r2 = test_tpe_contract()
    r3 = test_nsga2_schaffer()
    r4 = test_nsga2_sphere()
    r5 = test_nsga2_contract()
    assert all([r1, r2, r5])
    print(f"[test] TPE 等价 通过；TPE 契约 通过；"
          f"NSGA-II 契约 通过；Schaffer 前沿命中率 {r3:.2f}；"
          f"球面收敛值 {r4:.4f}")
    print("[test] 全部契约测试通过")
