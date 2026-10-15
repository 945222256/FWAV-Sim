# -*- coding: utf-8 -*-
"""selftest_template.py — 模板全链路自测。

在临时目录用 bootstrap 实例化一个测试项目，填入最小路线表后
走完整链路：提交、审理、批准、认领、完成关闭、看板状态组装、
依赖图校验、领地自检。全部通过说明模板填空即可运转。
测试目录跑完即删。仅使用 Python 标准库。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE = os.path.join(_HERE, "template")


def step(msg):
    print("— %s" % msg)


def main():
    tmp = tempfile.mkdtemp(prefix="agentic_selftest_")
    ok = True
    try:
        step("bootstrap 实例化：%s" % tmp)
        subprocess.run([sys.executable,
                        os.path.join(_TEMPLATE, "registry", "bootstrap.py"), tmp],
                       check=True, capture_output=True)

        reg = os.path.join(tmp, "registry")
        step("填路线表与项目配置")
        with open(os.path.join(reg, "lanes.json"), "w", encoding="utf-8") as f:
            json.dump({"schema_version": 1,
                       "shared_paths": ["tools/", "logs/"],
                       "lanes": {
                           "HQ": {"name": "指挥部", "mission": "治理",
                                  "keywords": ["计划"], "exclusive_paths": [],
                                  "may_change_parameters": False,
                                  "may_refactor": False},
                           "L1": {"name": "甲线", "mission": "甲",
                                  "keywords": ["甲活"], "exclusive_paths": ["src_a/"],
                                  "may_change_parameters": False,
                                  "may_refactor": False}}}, f, ensure_ascii=False)
        with open(os.path.join(reg, "data", "config.json"), "w", encoding="utf-8") as f:
            json.dump({"schema_version": 1, "project_code": "TESTPROJ",
                       "env_prefix": "TP_",
                       "acronym_whitelist": [], "tool_data_globs": []}, f,
                      ensure_ascii=False, indent=2)

        sys.path.insert(0, reg)
        for m in ("dispatch", "ledger", "events", "schedule", "intake",
                  "langcheck", "check_commit"):
            if m in sys.modules:
                del sys.modules[m]

        import intake
        import dispatch
        import ledger
        import schedule

        step("提交待审")
        qid = intake.submit("甲线首件登记", "在甲目标推进时遇到登记需求",
                            "总账出现编号为 I-001 的事项且状态为待认领",
                            "L1队")
        print("  队列编号 %s" % qid)

        step("lint 审理")
        rep = intake.lint(qid)
        print("  判定 %s，建议路线 %s" % (rep["verdict"], rep["suggested_lane"]))
        if rep["verdict"] != "pass":
            ok = False

        step("批准入册（附机主说明）")
        new_id = intake.approve(qid, "架构师", lane="L1",
                                owner_note="这是测试项目的首个事项入册说明。"
                                           "本事项为验证模板链路而登记，"
                                           "结果是总账获得第一条正式记录，"
                                           "链路各环节齐全才能走到这一步，"
                                           "下一步是认领并完成关闭验证收尾。")
        print("  事项编号 %s" % new_id)

        step("认领")
        dispatch.claim(new_id, "L1队")
        st = [i for i in json.load(open(os.path.join(reg, "items.json"),
                                        encoding="utf-8"))["items"]
              if i["id"] == new_id][0]
        print("  状态 %s，认领人 %s" % (st["status"], st["claimed_by"]))
        if st["status"] != "in_progress":
            ok = False

        step("完成关闭（附证据与判据对照）")
        ledger.close(new_id,
                     evidence=["测试证据：骨架自测记录"],
                     closer="L1队",
                     conclusion="判据通过",
                     criterion_check=[{"判据": "总账出现编号为 I-001 的事项且状态为待认领",
                                       "结果": "过",
                                       "数值出处": "items.json 事项 I-001"}],
                     owner_note="测试事项的完成关闭验证。结果是从登记到关闭的"
                                "全部环节按制度走通，证据与判据对照齐全是"
                                "关闭被接受的原因，下一步清理测试目录结束。")

        step("依赖图校验")
        errs = schedule.validate()
        print("  %s" % ("通过" if not errs else errs))
        if errs:
            ok = False

        step("领地自检")
        r = subprocess.run([sys.executable,
                            os.path.join(reg, "check_commit.py"), "--selftest"],
                           capture_output=True, text=True)
        print("  退出码 %d" % r.returncode)
        if r.returncode != 0:
            ok = False

        step("看板状态组装")
        board_dir = os.path.join(tmp, "board")
        sys.path.insert(0, board_dir)
        import server as board_server
        state = board_server.build_state()
        print("  未关闭 %d，已关闭 %d，路线 %d 条"
              % (state["totals"]["open"], state["totals"]["closed"],
                 len(state["lanes"])))
        if state["totals"]["closed"] != 1:
            ok = False

        print("[过] 模板全链路自测通过" if ok else "[拒] 模板全链路自测不通过")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        print("测试目录已清理：%s" % tmp)


if __name__ == "__main__":
    sys.exit(main())
