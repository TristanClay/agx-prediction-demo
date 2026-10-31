"""Run native experimental import/QC with explicit synthetic fixtures."""

from .adapters import candidate_mapping, receive_feedback
from .contracts import ExperimentalFeedback
from .io import digest, ref, verify, write


def experiment(request, knowledge, screening, screening_root, directory):
    from pipeline3.demo import generate_fixtures
    from pipeline3.service import Service

    fixtures = directory / "fixtures"
    manifest = generate_fixtures(fixtures)
    service = Service(directory / "store")
    ns = "demo:" + request.run_id
    try:
        task = service.create_task(
            ns,
            "Ag baseline / SYNTHETIC evidence",
            source_type="synthetic",
            upstream={
                "external_id": knowledge["task_id"],
                "hypothesis_id": knowledge["hypothesis_id"],
                "run_id": request.run_id,
                "gate_ref": "PENDING_GATE",
            },
        )
        protocol = service.protocol(ns, task["id"])
        links = service.import_adapter_result(
            ns, candidate_mapping(screening["results"], screening_root), "candidate"
        )
        states = {}
        for i in [1] if request.scenario == "missing" else [1, 2]:
            _, fresh = service.create_sample(
                ns,
                f"Ag-S{i} SYNTHETIC",
                f"synthetic-batch-{i}",
                task["id"],
                source_type="synthetic",
            )
            states[i] = {"fresh": fresh}
            for group, parent in [
                ("deactivated", "fresh"),
                ("washed", "deactivated"),
                ("spike", "fresh"),
                ("solution_control", "fresh"),
            ]:
                states[i][group] = service.treat(
                    ns,
                    states[i][parent]["id"],
                    group,
                    "SYNTHETIC treatment",
                    event_key=f"synthetic-{i}-{group}",
                )
        for entry in manifest["files"]:
            kind, phase = entry["kind"], entry["phase"]
            if kind == "Task" or phase == "anomaly":
                continue
            if request.scenario == "missing" and phase != "initial":
                continue
            if request.scenario != "counter" and phase == "counter":
                continue
            if (
                request.scenario == "counter"
                and kind == "CA"
                and entry["group"] == "washed"
                and phase != "counter"
            ):
                continue
            verify(fixtures, [{"path": entry["file"], "sha256": entry["sha256"]}])
            content = (fixtures / entry["file"]).read_bytes()
            if kind == "Calibration":
                service.import_calibration(
                    ns, content, entry["file"], entry["metadata"], source_type="synthetic"
                )
            else:
                service.import_measurement(
                    ns,
                    content,
                    entry["file"],
                    kind,
                    states[entry["sample"]][entry["group"]]["id"],
                    protocol["id"],
                    entry["metadata"],
                    source_type="synthetic",
                )
        if request.scenario == "mismatch":
            service.record_trend_review(
                ns,
                task["id"],
                links[0]["id"],
                "MISMATCH",
                "SYNTHETIC scenario: claimed prediction trend conflicts; eV cannot be subtracted from current",
                "synthetic-scenario-fixture",
                "synthetic",
            )
        analysis = service.analyze(ns, task["id"])
        records = service.store.list(ns)
        write(directory / "analysis.json", analysis)
        write(directory / "records.json", records)
        return {
            "task": task,
            "analysis": analysis,
            "records": records,
            "namespace": ns,
            "analysis_artifact": ref(directory / "analysis.json", directory),
        }
    finally:
        service.store.close()


def feedback(request, task, screening, exp, experiment_root, directory):
    analysis = exp["analysis"]
    evidence = analysis["payload"]["evidence"]
    incomplete = any(
        evidence[k]
        for k in [
            "insufficient_groups",
            "missing_product_groups",
            "invalid_measurements",
            "qc_issues",
        ]
    )
    incomplete = incomplete or not evidence["post_ca_record_present"]
    baseline = next(r for r in screening["results"] if "Cu" not in r["composition"])
    records = exp["records"]
    samples = [r["id"] for r in records if r["kind"] == "Sample"]
    measurements = [r["id"] for r in records if r["kind"] == "Measurement"]
    # References copied into this node keep its contract independently verifiable.
    write(directory / "analysis.json", analysis)
    write(directory / "records.json", records)
    actions = analysis["payload"]["recommendations"] + [
        {
            "rule_id": "human_review",
            "title": "人工核对条件、原始证据和 P1 科学门控",
            "target": "P1/P2",
        }
    ]
    result = ExperimentalFeedback(
        run_id=request.run_id,
        feedback_id="FB_" + digest([request.run_id, analysis["id"]])[:24],
        candidate_id=baseline["candidate_id"],
        hypothesis_id=task["hypothesis_id"],
        sample_id=samples[0],
        measurement_ids=measurements,
        experimental_findings=evidence,
        qc_status="INCOMPLETE" if incomplete else "PASS",
        prediction_deviation={
            "status": "MISMATCH" if evidence["p2_mismatch"] else "NOT_COMPARABLE",
            "numeric_delta": None,
            "reason": "Adsorption eV and experimental current differ in observable and units",
            "basis": "explicit SYNTHETIC scenario fixture"
            if evidence["p2_mismatch"]
            else "No quantitative mapping available",
        },
        conclusion=analysis["payload"]["interpretation"],
        recommended_actions=actions,
        provenance={
            "run_id": request.run_id,
            "origin": "SYNTHETIC",
            "producer": "P3.Service.analyze",
            "version": "1.0.0",
            "artifacts": [
                ref(directory / "analysis.json", directory),
                ref(directory / "records.json", directory),
            ],
            "limitations": [
                "Synthetic inputs; software QC PASS is not scientific approval",
                "Human review pending; formal P1 feedback ingestion unavailable",
            ],
        },
    )
    payload = result.model_dump()
    write(directory / "feedback.json", payload)
    receipts = {}
    for target in ["P1", "P2"]:
        receipts[target] = receive_feedback(payload, target)
        write(
            directory / f"inbox_{target}.json", {"feedback": payload, "receipt": receipts[target]}
        )
    return {"feedback": payload, "receipts": receipts}
