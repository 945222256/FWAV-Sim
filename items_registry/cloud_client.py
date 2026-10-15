# -*- coding: utf-8 -*-
"""cloud_client.py — 本地账本工具的云端通道：接口调用、断网队列、缓存同步。

设计依据《云端托管_需求与约束_20260930.md》：
  唯一事实源是云端数据库；本地 items.json 等文件降级为只读缓存；
  写操作断网时进入本地队列按序重放；本地不开放入站端口，只主动外呼。
模式开关：CLOUD_MODE 文件写 cloud 或 local，环境变量 FLAPPER_LEDGER_MODE
优先于文件。local 模式下本模块不接管，账本行为与历史完全一致（可回退）。
仅使用 Python 标准库。
"""
import http.client
import json
import os
import ssl
import datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_DIR)
LP_SYNC = os.path.join(ROOT, "lp_sync")
MODE_FILE = os.path.join(_DIR, "CLOUD_MODE")
TOKEN_FILE = os.path.join(LP_SYNC, "agent_token")
OUTBOX_PATH = os.path.join(LP_SYNC, "outbox.json")
STATE_PATH = os.path.join(LP_SYNC, "agent_state.json")
SECRET_PATH = os.path.join(LP_SYNC, "cloud_secret.json")

_TIMEOUT_S = 10
_CTX = ssl._create_unverified_context()      # 自签证书：加密传输，不校验签发者。


class CloudUnreachable(Exception):
    """云端不可达：写调用方应把操作放进断网队列。"""


def _secret():
    with open(SECRET_PATH, "r", encoding="utf-8") as f:
        secret = json.load(f)
    override = os.environ.get("LP_CLOUD_HOST", "").strip()
    if override:
        secret["host"] = override          # 测试与灾备切换用：不改凭证文件换入口。
    port_override = os.environ.get("LP_CLOUD_PORT", "").strip()
    if port_override:
        secret["port"] = int(port_override)   # I-217 隔离验证：独立端口同 host。
    return secret


def mode():
    env = os.environ.get("FLAPPER_LEDGER_MODE", "").strip().lower()
    if env in ("cloud", "local"):
        return env
    try:
        with open(MODE_FILE, "r", encoding="utf-8") as f:
            value = f.read().strip().lower()
        return value if value in ("cloud", "local") else "local"
    except OSError:
        return "local"


def enabled():
    return mode() == "cloud"


def _token():
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def request(method, path, body=None):
    """一次云端调用。不可达时抛 CloudUnreachable。"""
    secret = _secret()
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    conn = http.client.HTTPSConnection(secret["host"], secret.get("port"),
                                       timeout=_TIMEOUT_S, context=_CTX)
    try:
        conn.request(method, path, body=payload, headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + _token()})
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
    except (OSError, http.client.HTTPException) as exc:
        raise CloudUnreachable(str(exc))
    finally:
        conn.close()
    data = json.loads(raw) if raw else {}
    if resp.status == 401:
        raise CloudUnreachable("令牌无效：%s" % data.get("error"))
    if not data.get("ok"):
        raise RuntimeError(data.get("error", "云端拒绝：%s %s" % (method, path)))
    return data.get("result")


# ---------------- 断网队列 ----------------

def _load_outbox():
    try:
        with open(OUTBOX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"queue": []}


def _atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def enqueue(path, body):
    """断网写操作入队：谁、何时、想做什么全部留痕，恢复后按序重放。"""
    obj = _load_outbox()
    obj["queue"].append({"queued_at": datetime.datetime.now().isoformat(timespec="seconds"),
                         "path": path, "body": body})
    _atomic_write(OUTBOX_PATH, obj)


def outbox_size():
    return len(_load_outbox()["queue"])


def replay():
    """按序重放断网队列。成功一条删一条；被云端拒绝的条目保留并计数。"""
    obj = _load_outbox()
    remaining, rejected = [], []
    for op in obj["queue"]:
        try:
            request("POST", op["path"], op["body"])
        except CloudUnreachable:
            remaining.extend([op] + obj["queue"][obj["queue"].index(op) + 1:])
            return len(remaining), 0                        # 云端又断了，保序中止。
        except RuntimeError:
            rejected.append(op)                             # 云端明确拒绝：留待人工处置。
        else:
            pass                                            # 重放成功。
    obj["queue"] = remaining + rejected
    _atomic_write(OUTBOX_PATH, obj)
    return len(remaining), len(rejected)


# ---------------- 缓存同步 ----------------

_CACHE_FILES = {
    "items": os.path.join(_DIR, "items.json"),
    "inbox": os.path.join(_DIR, "inbox.json"),
    "events": os.path.join(_DIR, "events.json"),
    "params": os.path.join(ROOT, "params_registry", "params.json"),
    "lanes": os.path.join(_DIR, "lanes.json"),
}


def known_seq():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("server_seq", 0)
    except (OSError, ValueError):
        return 0


def sync_cache(force=False):
    """拉云端快照写本地缓存。事件序号未推进且未强制时跳过。"""
    def _write_cache(snapshot):
        items_obj = {"schema_version": 1, "items": snapshot["items"]}
        if snapshot.get("reports"):
            items_obj["reports"] = snapshot["reports"]
        _atomic_write(_CACHE_FILES["items"], items_obj)
        _atomic_write(_CACHE_FILES["inbox"],
                      {"schema_version": 1, "queue": snapshot["inbox"]})
        # I-132 并集保留：事件缓存是云端投影，但本地直写的事件在
        # 上传前不能被下拉整档覆盖。按时间戳+动作+事项+详情前六十
        # 字为键做并集，云端有则取云端，仅本地有的保留在尾部待上传。
        cloud_events = snapshot["events"]
        try:
            local_events = json.load(
                open(_CACHE_FILES["events"], encoding="utf-8")).get("events", [])
        except Exception:
            local_events = []
        def _ev_key(e):
            return (e.get("ts", ""), e.get("action", ""),
                    e.get("item_id") or "", (e.get("detail") or "")[:60])
        cloud_keys = {_ev_key(e) for e in cloud_events}
        extra = [e for e in local_events if _ev_key(e) not in cloud_keys]
        _atomic_write(_CACHE_FILES["events"],
                      {"schema_version": 1,
                       "events": cloud_events + extra if extra else cloud_events})
        params_obj = json.load(open(_CACHE_FILES["params"], encoding="utf-8")) \
            if os.path.exists(_CACHE_FILES["params"]) else {"schema_version": 1}
        params_obj["params"] = {k: rec for k, rec in snapshot["params"].items()}
        _atomic_write(_CACHE_FILES["params"], params_obj)
        if snapshot.get("lanes"):
            _atomic_write(_CACHE_FILES["lanes"],
                          {"schema_version": 1, "lanes": snapshot["lanes"]})
        _atomic_write(STATE_PATH, {"server_seq": snapshot["server_seq"],
                                   "synced_at": snapshot["generated_at"]})

    # I-092：心跳上行携带本地路线表，写云端权威键（与快照下发同键）。
    # 本地 lanes.json 是登记源，每次心跳镜像上行，云端漂移自愈。
    # 顺序纪律：一律先推后拉——冷启动强拉也先上行，杜绝云上旧表在
    # 心跳前冲掉本地新表（双键事故的重演路径）。
    hb_body = {"info": agent_info()}
    try:
        _lanes_local = json.load(open(_CACHE_FILES["lanes"], encoding="utf-8"))
        if isinstance(_lanes_local.get("lanes"), dict) and _lanes_local["lanes"]:
            hb_body["lanes"] = _lanes_local["lanes"]
    except Exception:
        pass
    hb = request("POST", "/api/agent/heartbeat", hb_body)
    if force or known_seq() == 0 or hb.get("server_seq", 0) > known_seq():
        _write_cache(request("GET", "/api/agent/snapshot"))
        return True
    return False


# ---------------- 写入口（账本函数专用） ----------------

def write(path, body, needs_result=False):
    """云端写入口：成功后立即同步缓存保证写后读一致；断网入队。

    needs_result 为真时（如 add 要拿新编号），断网直接抛错，
    因为本地无法分配云端编号，静默入队会破坏调用方语义。
    """
    try:
        result = request("POST", path, body)
    except CloudUnreachable as exc:
        if needs_result:
            raise RuntimeError("云端不可达且本操作需要云端分配结果：%s" % exc)
        enqueue(path, body)
        print("警告：云端不可达，写操作已入断网队列（%s，当前积压 %d 条）"
              % (path, outbox_size()))
        return None
    try:
        sync_cache()
    except Exception:
        pass                                             # 写已成功，缓存刷新失败等心跳补。
    return result


def agent_info():
    import platform
    return {
        "agent_id": _agent_id(),
        "machine": platform.node(),
        "model": "LP同步代理",
        "model_version": "1.0",
        "python": platform.python_version(),
        "os": "%s %s" % (platform.system(), platform.release()),
        "cwd": ROOT,
    }


def _agent_id():
    import platform
    return "local-%s" % (platform.node() or "pc")
