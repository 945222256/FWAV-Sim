# -*- coding: utf-8 -*-
"""check_contacts.py — I-179 公共组织通讯录格式检查器。

对 organization/contacts.json 执行判据一至判据五的机器检查，并内置
判据六要求的检查样例（格式错误、重复人员标识、分类拼写不符、未知
联系地址、会话更换、身份变更），全部样例为隔离测试值，不投递任何
真实服务，不发送任何消息（发送与回执样例只在另有明确授权时执行，
见 manager_operations.md 与 operation_examples.json）。

用法：
  python check_contacts.py                 检查正式通讯录
  python check_contacts.py --selftest      只跑检查样例（隔离值）
  python check_contacts.py --all           正式检查加样例自检
仅使用 Python 标准库。
"""
import datetime
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
CONTACTS = os.path.join(_HERE, "contacts.json")
SAMPLES = os.path.join(_HERE, "contacts_samples.json")

REQUIRED_MEMBER_FIELDS = ("name", "role", "tag", "onboarding_file", "contact")
REQUIRED_CONTACT_FIELDS = ("platform", "host_id", "thread_id", "verified_at")
ALLOWED_TAGS = ("manager", "Advisor")        # 大小写敏感：机主指定保留写法
TAG_REASONS = {"elsa": "manager（判据三指定）", "yulia": "Advisor（判据三指定）"}
NINE = ("anni", "beth", "ciri", "doro", "elsa", "fil", "gwen", "yulia", "zoya")


def _tz_ok(value):
    """verified_at 必须是带时区的 ISO 时间。"""
    if not isinstance(value, str) or not value:
        return False
    try:
        dt = datetime.datetime.fromisoformat(value)
    except ValueError:
        return False
    return dt.tzinfo is not None


def check_member(pid, m, problems, root=ROOT):
    """检查单个成员条目，问题追加到 problems。"""
    where = "members.%s" % pid
    if not isinstance(m, dict):
        problems.append("%s 必须是对象" % where)
        return
    for f in REQUIRED_MEMBER_FIELDS:
        if f not in m:
            problems.append("%s 缺字段 %s" % (where, f))
    if not isinstance(m.get("name"), str) or not m["name"].strip():
        problems.append("%s.name 必须为非空字符串" % where)
    if not isinstance(m.get("role"), str) or not m["role"].strip():
        problems.append("%s.role 必须为非空字符串" % where)
    if m.get("tag") not in ALLOWED_TAGS:
        problems.append("%s.tag 分类拼写不符（只允许 %s，大小写敏感）：%r"
                        % (where, ALLOWED_TAGS, m.get("tag")))
    ob = m.get("onboarding_file")
    if ob is None:
        pass                                  # 缺卡留空是允许的
    elif not isinstance(ob, str) or not ob:
        problems.append("%s.onboarding_file 必须为非空字符串或 null" % where)
    elif not os.path.exists(os.path.join(root, ob)):
        problems.append("%s.onboarding_file 引用不可用：%s" % (where, ob))
    contact = m.get("contact")
    if not isinstance(contact, dict):
        problems.append("%s.contact 必须是对象" % where)
        return
    for f in REQUIRED_CONTACT_FIELDS:
        if f not in contact:
            problems.append("%s.contact 缺字段 %s" % (where, f))
    thread = contact.get("thread_id")
    if thread is None:
        # 未知地址留空是允许的，但必须给出留空依据。
        basis = (contact.get("source") or {}).get("basis") or ""
        if not basis:
            problems.append("%s.contact.thread_id 为空但未附留空依据" % where)
    elif not isinstance(thread, str) or not thread.strip():
        problems.append("%s.contact.thread_id 必须为非空字符串或 null" % where)
    if not _tz_ok(contact.get("verified_at")):
        problems.append("%s.contact.verified_at 必须为含时区时间：%r"
                        % (where, contact.get("verified_at")))
    src = contact.get("source")
    if not isinstance(src, dict) or not (src.get("method") and src.get("basis")):
        problems.append("%s.contact.source 必须含 method 与 basis" % where)


def check_history_entry(pid, h, problems):
    """检查会话更换历史条目：旧地址、替代时间与来源可查。"""
    where = "members.%s.history[]" % pid
    for f in ("thread_id", "replaced_at", "replaced_by_thread_id"):
        if not h.get(f):
            problems.append("%s 缺字段 %s" % (where, f))
    if not _tz_ok(h.get("replaced_at")):
        problems.append("%s.replaced_at 必须为含时区时间" % where)
    if h.get("replaced_by_thread_id") and h.get("thread_id") and \
            h["replaced_by_thread_id"] == h["thread_id"]:
        problems.append("%s 新旧地址相同，不构成更换" % where)


def validate_contacts(obj, root=ROOT, require_nine=True):
    """返回问题清单；空清单即全部通过。

    require_nine 仅对正式通讯录开启（判据二九人齐备）；
    检查样例是隔离的最小构造，不要求九人。"""
    problems = []
    if not isinstance(obj, dict):
        return ["通讯录必须是 JSON 对象"]
    if obj.get("schema_version") != 1:
        problems.append("schema_version 必须为 1")
    members = obj.get("members")
    if not isinstance(members, dict) or not members:
        problems.append("members 必须为非空对象")
        return problems
    seen_names = {}
    for pid, m in members.items():
        check_member(pid, m, problems, root=root)
        if isinstance(m, dict):
            name = m.get("name")
            if name:
                if name in seen_names:
                    problems.append("重复人员标识：%s 与 %s 同名 %s"
                                    % (pid, seen_names[name], name))
                else:
                    seen_names[name] = pid
            for h in m.get("history") or []:
                if not isinstance(h, dict):
                    problems.append("members.%s.history 含非对象条目" % pid)
                    continue
                check_history_entry(pid, h, problems)
    if require_nine:
        for pid in NINE:
            if pid not in members:
                problems.append("判据二要求的人员 %s 未入库" % pid)
    return problems


# ---------------- 判据六与判据四的隔离检查样例 ----------------

def _selftest():
    """检查样例：全部为隔离测试值，不含真实人员与会话。"""
    checks = []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:200]})
        print("[%s] %s %s" % ("过" if ok else "拒", name, detail))

    # 样例一：格式错误（缺字段）必须被拒。
    bad_format = {"schema_version": 1, "members": {
        "sample": {"name": "样例", "tag": "manager"}}}
    check("格式错误样例被拒", validate_contacts(bad_format) != [],
          str(validate_contacts(bad_format)[:2]))

    # 样例二：重复人员标识必须被拒。
    dup = {"schema_version": 1, "members": {
        "a": {"name": "重复者", "role": "样例", "tag": "manager",
              "onboarding_file": None,
              "contact": {"platform": "test", "host_id": "test",
                          "thread_id": None, "verified_at": "2026-10-12T00:00:00+00:00",
                          "source": {"method": "样例", "basis": "留空依据"}}},
        "b": {"name": "重复者", "role": "样例", "tag": "manager",
              "onboarding_file": None,
              "contact": {"platform": "test", "host_id": "test",
                          "thread_id": None, "verified_at": "2026-10-12T00:00:00+00:00",
                          "source": {"method": "样例", "basis": "留空依据"}}}}}
    probs = validate_contacts(dup)
    check("重复人员标识样例被拒", any("重复人员标识" in p for p in probs),
          str([p for p in probs if "重复" in p][:1]))

    # 样例三：分类拼写不符（advisor、Manager、MANAGER）必须被拒。
    for wrong in ("advisor", "Manager", "MANAGER", "顾问"):
        bad_tag = {"schema_version": 1, "members": {
            "sample": {"name": "样例", "role": "样例", "tag": wrong,
                       "onboarding_file": None,
                       "contact": {"platform": "test", "host_id": "test",
                                   "thread_id": "t", "verified_at": "2026-10-12T00:00:00+00:00",
                                   "source": {"method": "样例", "basis": "样例"}}}}}
        probs = validate_contacts(bad_tag)
        check("分类拼写样例被拒（%r）" % wrong,
              any("分类拼写" in p for p in probs))

    # 样例四：未知联系地址——留空且附依据通过，留空无依据被拒。
    empty_ok = {"schema_version": 1, "members": {
        "sample": {"name": "样例", "role": "样例", "tag": "manager",
                   "onboarding_file": None,
                   "contact": {"platform": "test", "host_id": "test",
                               "thread_id": None,
                               "verified_at": "2026-10-12T00:00:00+00:00",
                               "source": {"method": "样例", "basis": "未核实留空"}}}}}
    check("未知地址留空附依据通过",
          validate_contacts(empty_ok, require_nine=False) == [])
    empty_no_basis = json.loads(json.dumps(empty_ok))
    empty_no_basis["members"]["sample"]["contact"]["source"]["basis"] = ""
    probs = validate_contacts(empty_no_basis)
    check("未知地址留空无依据被拒",
          any("留空依据" in p for p in probs))

    # 样例五：会话更换——人员标识相等、旧地址替代关系可查。
    samples = json.load(open(SAMPLES, encoding="utf-8"))
    sess = samples["session_change_sample"]
    before, after = sess["before"], sess["after"]
    same_id = before["person_id"] == after["person_id"]
    hist = after["member"]["history"][0]
    traceable = (hist["thread_id"] == before["member"]["contact"]["thread_id"]
                 and hist["replaced_by_thread_id"]
                 == after["member"]["contact"]["thread_id"]
                 and _tz_ok(hist["replaced_at"]))
    changed = before["member"]["contact"]["thread_id"] != \
        after["member"]["contact"]["thread_id"]
    after_problems = validate_contacts({"schema_version": 1,
                                        "members": {"sample": after["member"]}},
                                       require_nine=False)
    check("会话更换样例：标识不变、旧地址替代关系可查",
          same_id and traceable and changed and after_problems == [],
          "after 校验问题：%s" % after_problems[:2])

    # 样例六：身份变更——人员标识不变、变更附来源与时间。
    ident = samples["identity_change_sample"]
    before_i, after_i = ident["before"], ident["after"]
    same_id_i = before_i["person_id"] == after_i["person_id"]
    change = ident["change"]
    sourced = change.get("source", {}).get("basis") and _tz_ok(change.get("changed_at"))
    role_changed = before_i["member"]["role"] != after_i["member"]["role"]
    after_problems_i = validate_contacts({"schema_version": 1,
                                          "members": {"sample": after_i["member"]}},
                                         require_nine=False)
    check("身份变更样例：标识不变、变更附来源与时间",
          same_id_i and sourced and role_changed and after_problems_i == [],
          "after 校验问题：%s" % after_problems_i[:2])

    bad = sum(1 for c in checks if not c["ok"])
    print("检查样例总判定：%s（%d/%d 过）"
          % ("全部通过" if bad == 0 else "存在不过项", len(checks) - bad, len(checks)))
    return checks, bad == 0


def check_real():
    obj = json.load(open(CONTACTS, encoding="utf-8"))
    problems = validate_contacts(obj)
    print("正式通讯录：%s（%d 名成员）" % (CONTACTS, len(obj.get("members", {}))))
    for p in problems:
        print("[拒] %s" % p)
    if not problems:
        print("[过] 正式通讯录格式检查全部通过")
    return problems


def main(argv):
    if "--selftest" in argv:
        _, ok = _selftest()
        return 0 if ok else 1
    if "--all" in argv:
        problems = check_real()
        _, ok = _selftest()
        return 0 if (not problems and ok) else 1
    problems = check_real()
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
