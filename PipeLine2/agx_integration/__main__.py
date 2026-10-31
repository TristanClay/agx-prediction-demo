import argparse
import json
from pathlib import Path

from .adapters import ROOT
from .contracts import ExperimentalFeedback, KnowledgeTask, RunRequest, ScreeningResult
from .io import load_replay, write
from .workflow import execute


def main():
    parser = argparse.ArgumentParser(
        description="Ag-X local workflow; synthetic by default, never submits DFT"
    )
    parser.add_argument("command", choices=["run", "replay", "schemas"])
    parser.add_argument("--run-id", default="ag-baseline-demo")
    parser.add_argument("--mode", choices=["LIVE", "SYNTHETIC"], default="SYNTHETIC")
    parser.add_argument(
        "--scenario",
        choices=["baseline", "disagreement", "mismatch", "missing", "counter"],
        default="baseline",
    )
    parser.add_argument("--model", choices=["UMA", "MACE-MH-1", "DPA4"], default="UMA")
    parser.add_argument("--runs-root", type=Path, default=ROOT / "runs/integration")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--pipeline3-root", type=Path, default=ROOT.parent / "PipeLine3")
    parser.add_argument("--p1-source", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "schemas":
            for cls in [KnowledgeTask, ScreeningResult, ExperimentalFeedback, RunRequest]:
                write(
                    ROOT / "integration/schemas" / f"{cls.__name__}.json", cls.model_json_schema()
                )
            print("Schemas exported to integration/schemas")
            return 0
        if args.command == "replay":
            result = load_replay(args.bundle or args.runs_root / args.run_id)
        else:
            request = RunRequest(
                run_id=args.run_id, mode=args.mode, scenario=args.scenario, model=args.model
            )
            result = execute(
                request,
                args.runs_root,
                pipeline3_root=args.pipeline3_root,
                p1_source=args.p1_source,
            )
        print(
            json.dumps(
                {k: result[k] for k in ["run_id", "status", "actual_dft_jobs", "official_publish"]},
                indent=2,
            )
        )
        return 2 if result["status"] in {"FAILED", "BLOCKED"} else 0
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"Integration error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
