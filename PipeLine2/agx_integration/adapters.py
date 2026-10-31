"""Boundary adapters reuse native P2/P3 APIs; P1 historical artifacts stay read-only."""

import shutil
from pathlib import Path

from pipeline2.contracts import RunConfig, load_config
from pipeline2.models import DPA4Adapter, MACEMH1Adapter, UMAAdapter
from pipeline2.orchestrator.runner import run_demo

from .contracts import ExperimentalFeedback, KnowledgeTask, ScreeningResult
from .io import read, ref, verify, write

ROOT = Path(__file__).resolve().parents[1]
P1_SNAPSHOT = ROOT / "integration/fixtures/p1"


def connect_pipeline3(path=None):
    """Source checkout support; installed pipeline3 also works without a sibling repo."""
    import importlib
    import sys

    if path:
        sys.path.insert(0, str(Path(path).resolve()))
    try:
        return importlib.import_module("pipeline3.service")
    except ImportError as exc:
        raise RuntimeError(
            "Pipeline3 unavailable; install its package or supply --pipeline3-root"
        ) from exc


def knowledge(run_id, directory, source=P1_SNAPSHOT):
    from pipeline3.adapters import TaskAdapter

    source, directory = Path(source), Path(directory)
    manifest = read(source / "source_manifest.json")
    verify(source, [{"path": k, "sha256": v} for k, v in manifest["files"].items()])
    target = directory / "p1_snapshot"
    target.mkdir()
    for name in [*manifest["files"], "source_manifest.json"]:
        shutil.copyfile(source / name, target / name)
    mapped = TaskAdapter().load(target / "06_experiment_tasks.json")
    native = next(
        r["upstream_payload"]
        for r in mapped.records
        if r["upstream_payload"].get("element") == "Ag"
    )
    hypothesis = next(
        h
        for h in read(target / "04_ag_targeted_hypotheses.json")
        if h["hypothesis_id"] == native["linked_hypothesis"]
    )
    task = KnowledgeTask(
        task_id=native["task_id"],
        run_id=run_id,
        hypothesis_id=native["linked_hypothesis"],
        hypothesis=hypothesis["Ag_targeted_hypothesis"],
        candidate_elements=["Ag"],
        evidence_refs=native["source_paper_ids"],
        scientific_constraints=[
            "Ag baseline first; Cu remains exploratory until formal Ag gates pass",
            "No real VASP/PBS submission; no automatic scientific approval",
            "FF adsorption demo does not implement charged furoate mechanism validation",
            *native.get("transfer_limitations", []),
        ],
        target_properties=["adsorption_energy"],
        priority=native["priority"],
        confidence=None,
        provenance={
            "run_id": run_id,
            "origin": "REPLAY",
            "producer": "P1.TaskAdapter",
            "version": "native-task-list+agx-1",
            "source_run_id": manifest["original_run"],
            "artifacts": [ref(target / n, directory) for n in manifest["files"]],
            "limitations": [
                "Historical knowledge, not a new search; confidence not inferred from priority"
            ],
        },
    )
    write(directory / "knowledge.json", task.model_dump())
    return task.model_dump()


def screening(request, task, directory):
    if request.mode == "LIVE":
        cls = {"UMA": UMAAdapter, "MACE-MH-1": MACEMH1Adapter, "DPA4": DPA4Adapter}[request.model]
        raise EnvironmentBlocked(
            f"{request.model}: {cls().initialize()['status']}; no synthetic fallback"
        )
    results, native_runs = [], []
    for element in ["Ag", "Cu"] if request.include_cu else ["Ag"]:
        raw = load_config(ROOT / "configs/pipeline2/ag_baseline.yaml").model_dump()
        raw["mock_seed"] = request.seed
        raw["task"].update(
            task_id=f"{request.run_id}-{element}",
            hypothesis_id=task["hypothesis_id"],
            evidence_refs=task["evidence_refs"],
            dopants=[] if element == "Ag" else ["Cu"],
            dopant_sites=[] if element == "Ag" else [27],
        )
        config = RunConfig.model_validate(raw)
        config_file = write(directory / f"config_{element}.json", config.model_dump())
        run_path = directory / f"p2_{element}"
        native = run_demo(config, config_file, run_path)
        if native["status"] != "COMPLETED_OFFLINE":
            raise ValueError(f"Native P2 rejected request: {native['status']}")
        native_runs.append(
            {
                "element": element,
                "manifest": ref(run_path / "run_manifest.json", directory),
                "gate": native["gate"],
                "source_type": "mock",
                "actual_dft_jobs": 0,
            }
        )
        # The registry stores full native PredictionResult objects, unlike the CSV summary.
        from pipeline2.registry import DataRegistry

        registry = DataRegistry(run_path / "registry.sqlite")
        predictions = registry.records("Prediction")
        for prop in read(run_path / "properties.json"):
            sid = prop["candidate_id"]
            prediction = next(p for p in predictions if p["structure_id"] == sid)
            value = ScreeningResult(
                run_id=request.run_id,
                task_id=task["task_id"],
                hypothesis_id=task["hypothesis_id"],
                candidate_id=f"C_{element}_{sid[2:18]}",
                structure_id=sid,
                structure_artifact=ref(run_path / "structures" / f"{sid}.extxyz", directory),
                composition=prediction["composition"],
                predicted_properties=[
                    {
                        "value": prop["value"],
                        "reference_structure_ids": prop["reference_structure_ids"],
                    }
                ],
                model_name=prediction["model_id"],
                model_version=prediction["model_version"],
                model_hash=prediction["model_hash"],
                evidence_refs=task["evidence_refs"],
                provenance={
                    "run_id": request.run_id,
                    "origin": "SYNTHETIC",
                    "producer": "P2.native-runner",
                    "version": "0.1.0",
                    "artifacts": [ref(run_path / "run_manifest.json", directory)],
                    "limitations": [
                        "Analytic mock potential, not MLIP; uncalibrated",
                        "Exploratory Cu is an explicit demo case, not a released P1 candidate",
                    ],
                },
            )
            results.append(value.model_dump())
    write(directory / "screening.json", results)
    return {
        "results": results,
        "native_runs": native_runs,
        "models": {c.model_id: c().initialize() for c in [UMAAdapter, MACEMH1Adapter, DPA4Adapter]},
    }


class EnvironmentBlocked(RuntimeError):
    pass


def candidate_mapping(results, root):
    from pipeline3.contracts import AdapterResult

    records = []
    for item in results:
        result = ScreeningResult.model_validate(item)
        verify(root, [result.structure_artifact.model_dump()])
        records.append(
            {
                "external_id": result.candidate_id,
                "structure_id": result.structure_id,
                "hypothesis_id": result.hypothesis_id,
                "run_id": result.run_id,
                "descriptor": result.predicted_properties[0].model_dump(),
                "source_type": "mock",
                "data_origin": result.provenance.origin,
                "gate_ref": "PENDING_GATE",
                "upstream_payload": result.model_dump(),
            }
        )
    return AdapterResult(
        records=records, gate_status="unresolved", integration_status="AGX_VALIDATED_STAGED_MAPPING"
    )


def receive_feedback(payload, target):
    """P1/P2 review inbox readers. Never call the formal upstream feedback writer."""
    if target not in {"P1", "P2"}:
        raise ValueError("Unknown feedback target")
    value = ExperimentalFeedback.model_validate(payload)
    return {
        "target": target,
        "feedback_id": value.feedback_id,
        "candidate_id": value.candidate_id,
        "hypothesis_id": value.hypothesis_id,
        "run_id": value.run_id,
        "status": "STAGED",
        "scientific_update_performed": False,
        "official_publish": False,
        "blocked_from_formal_feedback": value.qc_status != "PASS",
        "next_tasks": value.recommended_actions,
        "reason": "QC and human review required; existing P1 v2.3 formal ingestion is a separate authority",
    }
