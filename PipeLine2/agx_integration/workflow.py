"""Small durable DAG. Isolated attempts, immutable node commits and a run lock."""

import os
from pathlib import Path
from time import perf_counter

from pipeline2.safety import writable_path

from . import adapters
from .contracts import KnowledgeTask, RunRequest
from .experiment import experiment, feedback
from .io import digest, files, load_replay, now, read, ref, verify, write

NODES = ["knowledge", "screening", "decision", "experiment", "feedback"]
AGENTS = {
    "knowledge": "Knowledge Agent",
    "screening": "Atomistic Screening Agent",
    "decision": "Uncertainty & Budget Agent",
    "experiment": "Experimental Evidence Agent",
    "feedback": "Evidence Feedback Agent",
}
REASONS = {
    "knowledge": "Map historical P1 evidence without granting formal release",
    "screening": "Reuse ASE geometry, native mock ModelAdapter and PENDING_GATE enforcement",
    "decision": "Require uncertainty review and sparse DFT budget; never submit jobs",
    "experiment": "Use native P3 import, CA/HPLC QC and rule-based planning",
    "feedback": "Return traceable staged tasks to both consumers; human review remains required",
}


def decision(request, screening):
    disagreement = request.scenario == "disagreement"
    # Separate, named synthetic policy fixture. Not predictions from UMA/MACE/DPA4.
    pair = (
        [
            {"model": "synthetic-policy-fixture-A", "value_eV": -0.1},
            {"model": "synthetic-policy-fixture-B", "value_eV": -0.8},
        ]
        if disagreement
        else []
    )
    spread = max(p["value_eV"] for p in pair) - min(p["value_eV"] for p in pair) if pair else None
    high = spread is not None and spread > request.disagreement_threshold_eV
    jobs = (
        [
            {
                "candidate_id": screening["results"][0]["candidate_id"],
                "kind": "DFT_VALIDATION_PROPOSAL",
                "status": "BLOCKED" if not request.dft_budget_jobs else "WAITING_FOR_REVIEW",
                "blockers": ["PENDING_GATE", "human_review_required", "real_submission_disabled"]
                + (["budget_exhausted"] if not request.dft_budget_jobs else []),
                "reason": "Synthetic disagreement exceeds configured threshold",
            }
        ]
        if high
        else []
    )
    return {
        "uncertainty": "HIGH" if high else "UNCALIBRATED",
        "comparison_fixture": pair,
        "fixture_origin": "SYNTHETIC",
        "spread_eV": spread,
        "threshold_eV": request.disagreement_threshold_eV,
        "dft_proposals": jobs,
        "configured_proposal_budget_jobs": request.dft_budget_jobs,
        "actual_dft_jobs": 0,
        "actual_dft_core_hours": 0.0,
        "policy": "No automatic submission; gate and review apply even with available budget",
        "next_action": "Review OOD and obtain calibrated comparisons before selecting DFT",
        "dft_savings_rate": None,
        "savings_reason": "No comparable real DFT baseline",
    }


def execute(request: RunRequest, runs_root, *, pipeline3_root=None, p1_source=None):
    module = adapters.connect_pipeline3(pipeline3_root)
    source = Path(p1_source or adapters.P1_SNAPSHOT).resolve()
    source_manifest = read(source / "source_manifest.json")
    verify(source, [{"path": k, "sha256": v} for k, v in source_manifest["files"].items()])
    root = writable_path(Path(runs_root).resolve() / request.run_id)
    # Do not let caller configuration route writes into any source project or fixture tree.
    for protected in [
        source,
        Path(module.__file__).resolve().parents[1],
        adapters.ROOT / "pipeline2",
        adapters.ROOT / "integration/fixtures",
    ]:
        if root == protected or root.is_relative_to(protected):
            raise ValueError("Run output overlaps protected source")
    root.mkdir(parents=True, exist_ok=True)
    identity = {
        "request": request.model_dump(),
        "p1_source_sha256": digest((source / "source_manifest.json").read_bytes()),
    }
    request_path = root / "request.json"
    if request_path.exists() and read(request_path) != identity:
        raise ValueError("Run ID already belongs to different inputs; choose a new run ID")
    if (root / "manifest.json").exists():
        return load_replay(root)
    try:
        lock = os.open(root / ".lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(
            "Run is locked; inspect owner/process before recovering an interrupted process"
        ) from exc
    os.write(lock, str(os.getpid()).encode())
    os.close(lock)
    try:
        write(request_path, identity)
        write(root / "plan.json", {name: "PENDING" for name in NODES})
        software = {}
        for base in [
            adapters.ROOT / "agx_integration",
            adapters.ROOT / "pipeline2",
            Path(module.__file__).resolve().parent,
        ]:
            for p in sorted(base.rglob("*.py")):
                software[str(p.relative_to(adapters.ROOT.parent))] = digest(p.read_bytes())
        software_path = root / "software.json"
        if software_path.exists() and read(software_path) != software:
            raise ValueError("Software changed during incomplete run; start a new run ID")
        write(software_path, software)
        outputs, roots, events = {}, {}, []
        for name in NODES:
            commit = root / "nodes" / f"{name}.json"
            if commit.exists():
                saved = read(commit)
                verify(root, saved["artifacts"])
                outputs[name], roots[name] = saved["output"], root / saved["directory"]
                events.append(saved["event"])
                continue
            node = root / "attempts" / name
            node.mkdir(parents=True, exist_ok=True)
            attempt = len(list(node.iterdir())) + 1
            if attempt > 3:
                raise RuntimeError(f"Retry limit reached for {name}; inspect retained attempts")
            directory = node / str(attempt)
            directory.mkdir()
            tick, started = perf_counter(), now()
            input_ids = {"run_id": request.run_id, "depends_on": list(outputs)}
            write(
                directory / "started.json",
                {"status": "RUNNING", "started_at": started, "input": input_ids},
            )
            error = None
            try:
                if name == "knowledge":
                    output = adapters.knowledge(request.run_id, directory, source)
                    KnowledgeTask.model_validate(output)
                elif name == "screening":
                    output = adapters.screening(request, outputs["knowledge"], directory)
                elif name == "decision":
                    output = decision(request, outputs["screening"])
                elif name == "experiment":
                    output = experiment(
                        request,
                        outputs["knowledge"],
                        outputs["screening"],
                        roots["screening"],
                        directory,
                    )
                else:
                    output = feedback(
                        request,
                        outputs["knowledge"],
                        outputs["screening"],
                        outputs["experiment"],
                        roots["experiment"],
                        directory,
                    )
                status = "WAITING_FOR_REVIEW" if name == "feedback" else "COMPLETED"
            except Exception as exc:
                status = "BLOCKED" if isinstance(exc, adapters.EnvironmentBlocked) else "FAILED"
                output, error = {"reason": str(exc)}, f"{type(exc).__name__}: {exc}"
            event = {
                "node_id": name,
                "agent": AGENTS[name],
                "input": input_ids,
                "tool": {
                    "knowledge": "P3.TaskAdapter.load(P1 snapshot)",
                    "screening": "P2.run_demo / MockModelAdapter.predict",
                    "decision": "uncertainty_budget_policy",
                    "experiment": "P3.Service.import_measurement/analyze",
                    "feedback": "ExperimentalFeedback.validate / P1,P2 inbox readers",
                }[name],
                "model": request.model
                if request.mode == "LIVE" and name == "screening"
                else (
                    "analytic mock-v1" if name == "screening" else "deterministic rules / no LLM"
                ),
                "reason": REASONS[name],
                "status": status,
                "started_at": started,
                "finished_at": now(),
                "elapsed_s": perf_counter() - tick,
                "attempt": attempt,
                "error": error,
                "origin": "REPLAY" if name == "knowledge" else request.mode,
                "output": output,
                "source_refs": [ref(p, root) for p in directory.glob("*.json")],
            }
            write(directory / "event.json", event)
            events.append(event)
            if status in {"FAILED", "BLOCKED"}:
                for remaining in NODES[NODES.index(name) + 1 :]:
                    events.append(
                        {
                            "node_id": remaining,
                            "agent": AGENTS[remaining],
                            "status": "SKIPPED",
                            "reason": f"Dependency {name} is {status}",
                            "elapsed_s": 0.0,
                        }
                    )
                result = {
                    "run_id": request.run_id,
                    "mode": request.mode,
                    "status": status,
                    "events": events,
                    "outputs": outputs,
                    "actual_dft_jobs": 0,
                    "official_publish": False,
                    "retryable": status == "FAILED",
                    "node_directories": {k: str(v.relative_to(root)) for k, v in roots.items()},
                }
                write(directory / "failure.json", result)
                return result
            artifacts = [
                {"path": str((directory / f["path"]).relative_to(root)), "sha256": f["sha256"]}
                for f in files(directory)
            ]
            write(
                commit,
                {
                    "directory": str(directory.relative_to(root)),
                    "output": output,
                    "event": event,
                    "artifacts": artifacts,
                },
            )
            outputs[name], roots[name] = output, directory
        result = {
            "schema_version": "agx-workflow-result-1",
            "run_id": request.run_id,
            "mode": request.mode,
            "status": "WAITING_FOR_REVIEW",
            "software_workflow_status": "COMPLETED",
            "gate_status": "PENDING_GATE",
            "official_publish": False,
            "actual_dft_jobs": 0,
            "events": events,
            "outputs": outputs,
            "node_directories": {k: str(v.relative_to(root)) for k, v in roots.items()},
        }
        write(root / "result.json", result)
        report = [
            "# Ag-X Workflow Report",
            "",
            f"Run: {request.run_id} / {request.mode}",
            "Software workflow completed; scientific gate PENDING_GATE; human review required.",
            "P1: historical knowledge. P2: analytic mock. P3: synthetic measurements.",
            "Actual DFT jobs: 0. No measured savings rate. No official scientific publication.",
            "",
        ]
        report += [f"- {e['agent']}: {e['status']} ({e['elapsed_s']:.4f} s)" for e in events]
        report_path = root / "report.md"
        with report_path.open("x") as handle:
            handle.write("\n".join(report) + "\n")
        write(
            root / "manifest.json",
            {
                "schema_version": "agx-run-manifest-1",
                "run_id": request.run_id,
                "created_at": now(),
                "mode": request.mode,
                "artifacts": files(root),
            },
        )
        return result
    finally:
        (root / ".lock").unlink()
