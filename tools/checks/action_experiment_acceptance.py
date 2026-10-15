"""实际实验验收入口：反例回归通过不能替代本入口的真实证据重算。"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.action_experiment import load_plan, verify_saved_result
from modules.experiment_pipeline.action_recipe import read_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(ROOT / "outputs/i187_action_experiment_manifest.json"))
    args = parser.parse_args(argv)
    try:
        manifest = read_json(args.manifest)
        directory = ROOT / manifest["experiment_directory"]
        if not directory.resolve().is_relative_to(ROOT):
            raise ValueError("实验目录须位于当前项目内")
        plan = load_plan(directory)
        if manifest.get("item") != "I-187" or manifest.get("plan_sha256") != plan["plan_sha256"]:
            raise ValueError("验收入口未绑定当前事项及完整冻结配方")
        result = verify_saved_result(directory)
        print("I-187 六条实际实验判据通过，结果摘要 " + result["result_sha256"])
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("I-187 验收未通过：" + str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
