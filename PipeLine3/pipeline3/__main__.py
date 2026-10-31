import argparse
import json
from pathlib import Path

from pipeline3.adapters import CandidateAdapter, TaskAdapter
from pipeline3.demo import generate_fixtures, load_demo
from pipeline3.service import Service


def main():
    parser = argparse.ArgumentParser(description="Pipeline 3 offline evidence demo")
    parser.add_argument("command", choices=["fixtures", "demo", "inspect", "audit-upstream"])
    parser.add_argument("--root", default="data/local")
    parser.add_argument("--namespace", default="demo:cli")
    parser.add_argument("--scenario", choices=["missing", "complete", "counter"], default="complete")
    parser.add_argument("--output", default="deliverables/example_run")
    parser.add_argument("--p1-task")
    parser.add_argument("--p2-bundle")
    args = parser.parse_args()
    if args.command == "fixtures":
        manifest = generate_fixtures()
        print(f"Generated {len(manifest['files'])} synthetic fixtures")
        return
    if args.command == "audit-upstream":
        output = {}
        for label, path, adapter in [("p1", args.p1_task, TaskAdapter()), ("p2", args.p2_bundle, CandidateAdapter())]:
            if path:
                r = adapter.load(path)
                output[label] = {"records": len(r.records), "gate_status": r.gate_status,
                    "integration_status": r.integration_status, "issues": r.issues, "source_refs": r.source_refs}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return
    service = Service(args.root)
    try:
        if args.command == "inspect":
            print(json.dumps({ns: {kind: len(service.store.list(ns, kind)) for kind in ["Sample", "Measurement", "AnalysisRun", "FeedbackPackage"]}
                for ns in service.store.namespaces()}, ensure_ascii=False, indent=2))
            return
        task = load_demo(service, args.namespace, "initial")
        initial = service.analyze(args.namespace, task["id"])
        run = initial
        if args.scenario != "missing":
            load_demo(service, args.namespace, "counter" if args.scenario == "counter" else "supplement")
            run = service.analyze(args.namespace, task["id"])
        review = service.review(args.namespace, run["id"], "CLI demo reviewer", "simulated review; no official acceptance")
        package, files = service.export(args.namespace, run["id"], review["id"])
        output = Path(args.output) / package["id"]
        output.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (output / name).write_bytes(content)
        print(json.dumps({"initial_rules": [r["rule_id"] for r in initial["payload"]["recommendations"]],
            "final_rules": [r["rule_id"] for r in run["payload"]["recommendations"]], "outcome": run["hypothesis_outcome"],
            "analysis_id": run["id"], "package": str(output), "official_publish": False}, ensure_ascii=False, indent=2))
    finally:
        service.store.close()


if __name__ == "__main__":
    main()
