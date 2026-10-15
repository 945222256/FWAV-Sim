"""Finite, restartable acceptance work; only writes under the explicit fixture."""
import argparse
import hashlib
import json
import os
import pathlib
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--steps", type=int, default=24)
    ap.add_argument("--outcome", default="evidence_appended", choices=["evidence_appended", "pending_review", "blocked"])
    args = ap.parse_args()
    work = pathlib.Path(args.work).resolve()
    state = pathlib.Path(args.state).resolve()
    allowed = pathlib.Path(__file__).resolve().parents[1] / "logs" / "runtime_real_acceptance"
    if not work.is_relative_to(allowed.resolve()) or not state.is_relative_to(allowed.resolve()):
        raise SystemExit("fixture path required")
    work.mkdir(parents=True, exist_ok=True)
    result = state / "inbox" / ("result-" + args.item + ".json")
    if result.exists() or (work / "completed.json").exists():
        print("already completed; no work repeated")
        return 0
    for number in range(args.steps):
        marker = work / ("step-%03d.json" % number)
        if marker.exists():
            continue
        payload = {"step": number, "digest": hashlib.sha256(str(number).encode()).hexdigest(),
                   "pid": os.getpid(), "time": time.time()}
        tmp = marker.with_suffix(".%d.tmp" % os.getpid())
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        # Exclusive completed step: concurrent duplicate work is observable.
        try:
            os.link(tmp, marker)
        except FileExistsError:
            (work / "duplicate-step.json").write_text(json.dumps(payload), encoding="utf-8")
            raise SystemExit("duplicate effective step detected")
        finally:
            tmp.unlink(missing_ok=True)
        time.sleep(.5)
    evidence = str(work / "completed.json")
    (work / "completed.json").write_text(json.dumps({"run_id": args.run, "steps": args.steps,
                                                    "finished_at": time.time()}), encoding="utf-8")
    body = {"schema_version": 1, "kind": "result_submit", "result_id": "result-" + args.item,
            "run_id": args.run, "item_id": args.item, "outcome": args.outcome,
            "evidence": "real isolated execution completed " + evidence}
    if args.outcome == "pending_review":
        body.update(delivery_version="real-fixture-v1", evidence_ptr=evidence, reviewer="beth")
    elif args.outcome == "blocked":
        if (work / "required_input.json").exists():
            raise SystemExit("blocked fixture requires an absent input marker")
        body.update(reason="专用隔离阻塞验收：本项所需required_input.json未提供，有限步骤已保存，按简报申报缺输入。",
                    blocker_type="缺外部输入", needs="提供本项隔离required_input.json后再决定继续，本项不占用后续无依赖的执行槽。",
                    owner_note="本项是机主授权原冻结判据要求的专用阻塞验收。真实执行器已经完成有限步骤并保存原运行号证据，按简报核对隔离所需输入不存在后申报阻塞，内核应回写真实状态并允许同线无依赖第三项继续；不得把此构造性输入缺失写成生产事故或判据通过。",
                    reporter="L3队")
    state.joinpath("inbox").mkdir(parents=True, exist_ok=True)
    tmp = result.with_suffix(".%d.tmp" % os.getpid())
    tmp.write_text(json.dumps(body), encoding="utf-8")
    os.replace(tmp, result)
    print("completed", evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
