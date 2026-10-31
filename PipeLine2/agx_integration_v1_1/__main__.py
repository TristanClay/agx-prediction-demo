"""V1.1 commands delegate calculations to unchanged native software."""

import argparse
import json
import os
from pathlib import Path

from agx_integration.contracts import RunRequest

from .runtime import DEFAULT_RUNS, SCENARIOS, open_bundle, run_new


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--run-id", required=True)
    run.add_argument("--scenario", choices=SCENARIOS, default="baseline")
    run.add_argument("--mode", choices=["SYNTHETIC", "LIVE"], default="SYNTHETIC")
    run.add_argument("--model", choices=["UMA", "MACE-MH-1", "DPA4"], default="UMA")
    run.add_argument(
        "--runs-root", type=Path, default=Path(os.environ.get("AGX_V1_1_RUNS_ROOT", DEFAULT_RUNS))
    )
    run.add_argument(
        "--pipeline3-root",
        type=Path,
        default=Path(
            os.environ.get("AGX_PIPELINE3_ROOT", Path(__file__).resolve().parents[2] / "PipeLine3")
        ),
    )
    run.add_argument("--dft-budget-jobs", type=int, default=0)
    replay = commands.add_parser("replay")
    replay.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.command == "replay":
        bundle = open_bundle(args.path)
    else:
        bundle = run_new(
            RunRequest(
                run_id=args.run_id,
                scenario=args.scenario,
                mode=args.mode,
                model=args.model,
                dft_budget_jobs=args.dft_budget_jobs,
            ),
            args.runs_root,
            args.pipeline3_root,
        )
    print(
        json.dumps(
            {
                "run_id": bundle.result["run_id"],
                "origin": bundle.result["mode"],
                "status": bundle.result["status"],
                "root": str(bundle.root),
            },
            ensure_ascii=False,
        )
    )
    return 2 if bundle.result["status"] in {"FAILED", "BLOCKED"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
