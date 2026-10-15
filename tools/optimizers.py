# -*- coding: utf-8 -*-
"""
optimizers.py — 统一优化器库：唯一模块（I-080）
================================================
收编范围与纪律：
  1. TPE（树形结构估计器，Tree-structured Parzen Estimator）：算法逐字
     收编自 tools/unified_search_v3.py 的 sample_rng、hist_draw 与 TPE
     类，行为零改动（同种子同序列输出逐位一致，契约测试作证）。
  2. NSGA-II（带精英保留的非支配排序遗传算法，Non-dominated Sorting
     Genetic Algorithm II）：本模块新建，纯 numpy 实现，最小化口径。
  3. 本模块只依赖 numpy 与标准库，禁止导入项目框架（config、登记库、
     仿真内核都不许出现）——这是 ltv/hss_tpe.py 复制时留下的教训：
     优化器库一旦带上框架依赖，框架类型变更就会把它连带弄崩。

各队使用规则：调增益、设计波形、调训练参数一律从本模块引用，
不许再抄拷贝。旧引用点（ltv/hss_tpe.py、tools/allocator_tune_tpe.py、
tools/unified_search_v3.py）已全部改为本模块的转发或导入。

接口约定（两个算法共用）：
  定义域 dom = {键名: (下界, 上界)}；点是 {键名: 数值} 字典；
  TPE.tell(点列表, 标量损失列表)；NSGA2.tell(点列表, 目标矩阵列表)，
  每个点给一个目标向量（全部按最小化理解）。
自检：python tools/test_optimizers.py
"""
import numpy as np

__all__ = ["sample_rng", "hist_draw", "TPE", "NSGA2"]


# ======================= TPE（行为零改动收编）=======================

def sample_rng(rng, dom):
    return {k: float(rng.uniform(dom[k][0], dom[k][1])) for k in dom}


def hist_draw(rng, vals, lo, hi):
    if len(vals) < 5:
        return float(rng.uniform(lo, hi))
    bins = 12
    h, edges = np.histogram(np.clip(vals, lo, hi), bins=bins, range=(lo, hi))
    p = (h + 1.0) / np.sum(h + 1.0)
    b = int(rng.choice(bins, p=p))
    return float(rng.uniform(edges[b], edges[b + 1]))


class TPE:
    def __init__(self, dom, seed):
        self.dom = dom
        self.keys = list(dom.keys())
        self.rng = np.random.default_rng(seed)
        self.x = []
        self.y = []

    def ask_batch(self, n_total):
        if len(self.x) < 8:
            return [sample_rng(self.rng, self.dom) for _ in range(n_total)]
        arr = np.array(self.x)
        good = arr[np.array(self.y) <= np.percentile(self.y, 25)]
        out = []
        for _ in range(n_total):
            if self.rng.random() < 0.25:
                out.append(sample_rng(self.rng, self.dom))
                continue
            p = {}
            for j, k in enumerate(self.keys):
                lo, hi = self.dom[k]
                p[k] = hist_draw(self.rng, good[:, j], lo, hi)
            out.append(p)
        return out

    def tell(self, pts, ys):
        self.x.extend([[p[k] for k in self.keys] for p in pts])
        self.y.extend(ys)


# ======================= NSGA-II（新建，最小化口径）=======================

def _dominates(a, b):
    """a 支配 b：a 的每个目标不大于 b，且至少一个严格小于。"""
    return np.all(a <= b) and np.any(a < b)


class NSGA2:
    """字典定义域上的 NSGA-II。

    用法：
        opt = NSGA2(dom, seed=0, pop=40)
        for gen in range(30):
            cands = opt.ask()              # 首代随机，之后为子代
            objs = [objectives(c) for c in cands]
            opt.tell(objs)                  # 父代+子代合并环境选择
        front = opt.pareto_front()          # [(点, 目标向量), ...]
    变异与交叉：模拟二进制交叉（分布指数 eta_c）与多项式变异
    （分布指数 eta_m），箱约束裁剪。
    """

    def __init__(self, dom, seed, pop=40, eta_c=15.0, eta_m=20.0):
        self.dom = dict(dom)
        self.keys = list(dom.keys())
        self.rng = np.random.default_rng(seed)
        self.pop_n = int(pop)
        self.eta_c = float(eta_c)
        self.eta_m = float(eta_m)
        self.pop = []          # [(x_dict, obj_array), ...]
        self._pending = None

    # ---------- 编解码 ----------
    def _vec(self, p):
        return np.array([p[k] for k in self.keys], dtype=float)

    def _pt(self, v):
        lo = np.array([self.dom[k][0] for k in self.keys])
        hi = np.array([self.dom[k][1] for k in self.keys])
        v = np.clip(v, lo, hi)
        return {k: float(v[j]) for j, k in enumerate(self.keys)}

    def _rand(self):
        return sample_rng(self.rng, self.dom)

    # ---------- 遗传算子 ----------
    def _crossover(self, v1, v2):
        u = self.rng.random(len(v1))
        beta = np.where(u <= 0.5, (2 * u) ** (1.0 / (self.eta_c + 1)),
                        (1.0 / (2 * (1 - u))) ** (1.0 / (self.eta_c + 1)))
        c1 = 0.5 * ((1 + beta) * v1 + (1 - beta) * v2)
        c2 = 0.5 * ((1 - beta) * v1 + (1 + beta) * v2)
        return c1, c2

    def _mutate(self, v):
        lo = np.array([self.dom[k][0] for k in self.keys])
        hi = np.array([self.dom[k][1] for k in self.keys])
        mask = self.rng.random(len(v)) < 1.0 / len(v)   # 每维概率 1/n
        u = self.rng.random(len(v))
        delta = np.where(
            u < 0.5,
            (2 * u) ** (1.0 / (self.eta_m + 1)) - 1.0,
            1.0 - (2 * (1 - u)) ** (1.0 / (self.eta_m + 1)))
        v = v + (hi - lo) * delta * mask
        return v

    # ---------- 非支配排序与拥挤距离 ----------
    def _nd_sort(self, F):
        n = len(F)
        S = [[] for _ in range(n)]
        nd = np.zeros(n, dtype=int)
        fronts = [[]]
        for p in range(n):
            for q in range(n):
                if p == q:
                    continue
                if _dominates(F[p], F[q]):
                    S[p].append(q)
                elif _dominates(F[q], F[p]):
                    nd[p] += 1
            if nd[p] == 0:
                fronts[0].append(p)
        i = 0
        while fronts[i]:
            nxt = []
            for p in fronts[i]:
                for q in S[p]:
                    nd[q] -= 1
                    if nd[q] == 0:
                        nxt.append(q)
            i += 1
            fronts.append(nxt)
        fronts.pop()
        return fronts

    def _crowding(self, F, idx):
        if len(idx) <= 2:
            return np.zeros(len(idx))
        d = np.zeros(len(idx))
        M = F[idx[0]].shape[0]
        for m in range(M):
            order = np.argsort([F[i][m] for i in idx])
            d[order[0]] = d[order[-1]] = np.inf
            fmin = F[idx[order[0]]][m]
            fmax = F[idx[order[-1]]][m]
            rng_m = fmax - fmin if fmax > fmin else 1.0
            for r in range(1, len(idx) - 1):
                d[order[r]] += (F[idx[order[r + 1]]][m]
                                - F[idx[order[r - 1]]][m]) / rng_m
        return d

    # ---------- 对外接口 ----------
    def ask(self):
        """产出子代点列表（首代随机采样）。"""
        if not self.pop:
            self._pending = [self._rand() for _ in range(self.pop_n)]
            return list(self._pending)
        child = []
        while len(child) < self.pop_n:
            # 二元锦标赛（按秩与拥挤距离）
            def better():
                a, b = self.rng.integers(0, len(self.pop), 2)
                ra = self._rank_of[a]
                rb = self._rank_of[b]
                if ra != rb:
                    return a if ra < rb else b
                return a if self._crowd_of[a] >= self._crowd_of[b] else b
            p1, p2 = better(), better()
            v1, v2 = self._crossover(self._vec(self.pop[p1][0]),
                                     self._vec(self.pop[p2][0]))
            child.append(self._pt(self._mutate(v1)))
            if len(child) < self.pop_n:
                child.append(self._pt(self._mutate(v2)))
        self._pending = child
        return list(child)

    def tell(self, objs):
        """登记子代目标并做环境选择（父代+子代合并取前 pop_n）。"""
        assert self._pending is not None and len(objs) == len(self._pending), \
            "tell 的目标数必须与上一次 ask 的点数一致"
        offspring = [(x, np.asarray(o, dtype=float))
                     for x, o in zip(self._pending, objs)]
        merged = self.pop + offspring
        F = [o for _, o in merged]
        fronts = self._nd_sort(F)
        new_pop = []
        for front in fronts:
            if len(new_pop) + len(front) <= self.pop_n:
                new_pop.extend(front)
            else:
                cd = self._crowding(F, front)
                order = sorted(range(len(front)), key=lambda j: -cd[j])
                new_pop.extend(front[j] for j in order[:self.pop_n
                                                       - len(new_pop)])
                break
        self.pop = [merged[i] for i in new_pop]
        # 重算整档秩与拥挤度供下一轮锦标赛用
        F2 = [o for _, o in self.pop]
        fronts2 = self._nd_sort(F2)
        self._rank_of = np.zeros(len(self.pop), dtype=int)
        self._crowd_of = np.zeros(len(self.pop))
        for rk, front in enumerate(fronts2):
            cd = self._crowding(F2, front)
            for j, i in enumerate(front):
                self._rank_of[i] = rk
                self._crowd_of[i] = cd[j]
        self._pending = None
        return self

    def pareto_front(self):
        """当前种群的第一非支配前沿。"""
        if not self.pop:
            return []
        F = [o for _, o in self.pop]
        front = self._nd_sort(F)[0]
        return [(self.pop[i][0], self.pop[i][1]) for i in front]
