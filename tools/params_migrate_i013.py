# -*- coding: utf-8 -*-
"""
params_migrate_i013.py — I-013 参数登记库全量迁移工具
======================================================
依据 docs/params_migration_map.md 主表（六域 278 行）把现行配置值
登记入 params_registry。登记一律经 registry.put()（唯一合法通道），
本工具不直接改 params.json。

用法：
  python tools/params_migrate_i013.py --dry     干跑：解析+取值+预览，不写账
  python tools/params_migrate_i013.py --apply   执行登记
  python tools/params_migrate_i013.py --verify  核对：登记库值 vs 配置现值逐键比对
  python tools/params_migrate_i013.py --sweep   残留清点：两配置文件全部数值
                                               定义逐处分类（主表已登记/附表二
                                               不迁/域外未分类）

取值规则：导入 config.sim_config 与 config.unified_search_config 后按
定义位置解析属性现值（导入前清空 FLAPPER_* 环境变量，保证取文件默认值
而非会话临时覆盖）。MULTISINE_FREQS 逗号串转数值列表；元组与 numpy
标量转 JSON 安全类型。已存在且数值相同的键自动跳过；数值不同则报
MISMATCH 并跳过（改值须走 registry.retire 留档，本工具不代改）。
"""
import os
import re
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MAP_PATH = os.path.join(ROOT, "docs", "params_migration_map.md")


def _clean_env():
    """清掉 FLAPPER_* 会话覆盖，保证读到配置文件默认值。"""
    for k in list(os.environ):
        if k.startswith("FLAPPER_"):
            del os.environ[k]


def _sanitize(v):
    """转 JSON 安全类型：numpy 标量转内建数，元组转列表，递归处理。"""
    import numpy as _np
    if isinstance(v, (_np.floating,)):
        return float(v)
    if isinstance(v, (_np.integer,)):
        return int(v)
    if isinstance(v, (list, tuple)):
        return [_sanitize(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _sanitize(x) for k, x in v.items()}
    if isinstance(v, (bool, int, float, str)) or v is None:
        return v
    return str(v)


def parse_main_table():
    """解析映射表主表六域全部行。返回 [(参数名, 定义位置, 域, 键名, 单位, 来源说明)]。"""
    rows = []
    in_main = False
    with open(MAP_PATH, encoding="utf-8") as f:
        for line in f:
            if line.startswith("## 二、"):
                in_main = True
                continue
            if line.startswith("## 三、"):
                break
            if not in_main or not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) != 6 or cells[0] in ("参数名", "---",) or set(cells[0]) <= {"-", " ", ":"}:
                continue
            rows.append(tuple(cells))
    return rows


def parse_appendix2_names():
    """解析附表二全部参数名（含三组），供残留清点豁免。"""
    names = set()
    in_app2 = False
    with open(MAP_PATH, encoding="utf-8") as f:
        for line in f:
            if line.startswith("## 四、"):
                in_app2 = True
                continue
            if line.startswith("## 五、"):
                break
            if not in_app2 or not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 4 or set(cells[0]) <= {"-", " ", ":"}:
                # 参数名.子键 形如 WING_DRIVE_CONFIG.pitch_mode 需拆出主名
                continue
            first = cells[0]
            if first in ("参数名",):
                continue
            name = first.split(".")[0] if "." in first and not first.startswith("_") else first
            names.add(name)
            names.add(first)
    return names


def resolve_value(param_name, cfg, usearch):
    """按参数名解析配置现值。"""
    if param_name.startswith("UNIFIED_SEARCH."):
        node = usearch
        rest = param_name[len("UNIFIED_SEARCH."):]
        seg_map = {"hard": "hard_constraints", "domain": "search_domains",
                   "time": "time_grids", "criteria": "criteria",
                   "engine": "engine", "anchor": "anchors"}
        parts = rest.split(".")
        node = node[seg_map[parts[0]]]
        tail = parts[1]
        if parts[0] == "domain":
            return _sanitize([node[tail]["lo"], node[tail]["hi"]])
        if parts[0] in ("hard", "time"):
            return _sanitize(node[tail]["value"])
        if parts[0] == "anchor":
            return _sanitize(node[tail])
        return _sanitize(node[tail])
    if param_name.startswith("WING_DRIVE_CONFIG."):
        key = param_name.split(".", 1)[1]
        return _sanitize(cfg.WING_DRIVE_CONFIG[key])
    if "." in param_name:
        cls_name, attr = param_name.split(".", 1)
        obj = getattr(cfg, cls_name)
        val = getattr(obj, attr)
        if param_name.endswith("MULTISINE_FREQS") and isinstance(val, str):
            return [float(x) for x in val.split(",") if x.strip()]
        return _sanitize(val)
    return _sanitize(getattr(cfg, param_name))


def short_source(param_name, location, source_note):
    """登记库来源字段：定义位置+参数名+映射表出处；待考项带附表三标记。"""
    s = "%s %s；docs/params_migration_map.md 主表" % (location, param_name)
    m = re.search(r"附表三第\s*(\d+)\s*行", source_note)
    if m:
        s += "；来源待考附表三第%s行" % m.group(1)
    return s


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--dry"
    if mode not in ("--dry", "--apply", "--verify", "--sweep"):
        raise SystemExit("用法: params_migrate_i013.py --dry|--apply|--verify|--sweep")

    rows = parse_main_table()
    print("主表解析：%d 行" % len(rows))

    _clean_env()
    import config.sim_config as cfg
    import config.unified_search_config as usc

    if mode == "--sweep":
        return do_sweep(rows, cfg, usc)

    sys.path.insert(0, os.path.join(ROOT, "params_registry"))
    import registry

    plan, mismatch, errors = [], [], []
    for (pname, loc, domain, key, unit, note) in rows:
        try:
            val = resolve_value(pname, cfg, usc.UNIFIED_SEARCH)
        except Exception as e:
            errors.append((pname, repr(e)))
            continue
        existing = registry.get_record(key) if _has_key(registry, key) else None
        if existing is not None:
            if not _value_eq(existing.get("value"), val):
                mismatch.append((key, existing.get("value"), val))
            continue
        plan.append((key, val, unit, short_source(pname, loc, note), domain))

    print("待新登记：%d 键；已存在同值跳过；数值不符：%d；解析失败：%d"
          % (len(plan), len(mismatch), len(errors)))
    for k, old, new in mismatch:
        print("  [MISMATCH] %s 登记库=%r 配置现值=%r（改值须走 retire）" % (k, old, new))
    for p, e in errors:
        print("  [ERROR] %s %s" % (p, e))

    if mode == "--dry":
        for key, val, unit, src, domain in plan[:10]:
            print("  示例 %s = %r [%s] (%s)" % (key, val, unit, domain))
        if len(plan) > 10:
            print("  ... 其余 %d 键同格式" % (len(plan) - 10))
        return

    # --apply
    done = 0
    for key, val, unit, src, domain in plan:
        registry.put(key, val, unit, src)
        done += 1
    print("登记完成：%d 键" % done)

    # --verify 逻辑同样适用于 apply 后自检
    bad = 0
    for (pname, loc, domain, key, unit, note) in rows:
        try:
            val = resolve_value(pname, cfg, usc.UNIFIED_SEARCH)
        except Exception:
            continue
        rec = registry.get_record(key)
        if not _value_eq(rec.get("value"), val):
            print("  [DRIFT] %s 登记库=%r 配置=%r" % (key, rec.get("value"), val))
            bad += 1
    print("核对完成：不符 %d 键" % bad)
    chk = registry.check()
    print("registry.check：suspect=%d review=%d"
          % (len(chk["suspect"]), len(chk["review"])))


def do_sweep(rows, cfg, usc):
    """残留清点：两配置文件全部数值定义逐处分类。"""
    import ast
    main_names = {r[0].split(".")[0] if not r[0].startswith("UNIFIED") else r[0]
                  for r in rows}
    main_full = {r[0] for r in rows}
    app2 = parse_appendix2_names()
    out = {"registered": [], "appendix2": [], "out_of_scope": [], "unknown": []}
    for fname, modname in (("config/sim_config.py", "sim_config"),):
        path = os.path.join(ROOT, fname)
        tree = ast.parse(open(path, encoding="utf-8").read())
        # 模块级数值定义
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        _classify(t.id, "sim_config", main_names, app2, out)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                _classify(node.target.id, "sim_config", main_names, app2, out)
        # 类内属性定义
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    tgt = None
                    if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name):
                        tgt = stmt.targets[0].id
                    elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                        tgt = stmt.target.id
                    if tgt:
                        full = "%s.%s" % (node.name, tgt)
                        if full in main_full or full in main_names:
                            out["registered"].append(full)
                        elif full in app2 or tgt in app2:
                            out["appendix2"].append(full)
                        else:
                            out["out_of_scope"].append(full)
    print("主表已登记名：%d；附表二：%d；域外类内属性：%d；模块级未分类：%d"
          % (len(out["registered"]), len(out["appendix2"]),
             len(out["out_of_scope"]), len(out["unknown"])))
    with open(os.path.join(ROOT, "logs", "params_sweep_i013.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("清单落盘 logs/params_sweep_i013.json")


def _classify(name, mod, main_names, app2, out):
    if name in main_names:
        out["registered"].append(name)
    elif name in app2:
        out["appendix2"].append(name)
    else:
        out["out_of_scope"].append(name)


def _has_key(registry, key):
    try:
        registry.get_record(key)
        return True
    except KeyError:
        return False


def _value_eq(a, b):
    try:
        return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    except Exception:
        return a == b


if __name__ == "__main__":
    main()
