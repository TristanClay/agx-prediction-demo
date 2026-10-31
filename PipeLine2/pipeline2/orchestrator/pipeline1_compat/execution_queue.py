# Migrated from Pipeline1 without modifying its source.
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

from .identifiers import element_candidate_id, mechanism_bucket_id

EXECUTION_QUEUE_SCHEMA_VERSION = "validation-execution-queue-v2.3-1"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def load_feedback_gate_snapshot(
    feedback_run: str | Path | None,
    expected_pipeline1_run: str | Path | None = None,
) -> Dict[str, Any]:
    """Load append-only feedback gates or return an explicit zero-feedback state."""

    empty_gates = {
        "baseline_state": "awaiting_feedback",
        "benchmark_state": "candidate_gate_pending",
        "baseline_gate_complete": False,
        "benchmark_gate_complete": False,
        "ag_furoate_deactivation_causality_established": False,
        "ni_low_cost_dft_gate_established": False,
    }
    if feedback_run is None:
        return {
            "present": False,
            "feedback_run": "",
            "feedback_hash": "",
            "gates": empty_gates,
            "candidate_states": {},
        }
    directory = Path(feedback_run)
    overlay_path = directory / "feedback_overlay.json"
    audit_path = directory / "feedback_audit.json"
    if not overlay_path.is_file() or not audit_path.is_file():
        raise FileNotFoundError(f"Feedback run is missing overlay or audit: {directory}")
    overlay = _read_json(overlay_path)
    audit = _read_json(audit_path)
    if overlay.get("append_only") is not True:
        raise ValueError("Feedback overlay must be append-only")
    if audit.get("source_artifacts_unchanged") is not True:
        raise ValueError("Feedback audit does not confirm immutable Pipeline1 sources")
    if expected_pipeline1_run is not None:
        expected = Path(expected_pipeline1_run).resolve()
        actual = Path(str(overlay.get("pipeline1_run", ""))).resolve()
        if actual != expected:
            raise ValueError("Feedback overlay belongs to a different Pipeline1 run")
    states = {
        str(row.get("element_candidate_id", "")): row
        for row in overlay.get("candidate_states", [])
        if isinstance(row, dict) and row.get("element_candidate_id")
    }
    return {
        "present": True,
        "feedback_run": str(directory.resolve()),
        "feedback_hash": _sha256(overlay_path),
        "gates": {**empty_gates, **(overlay.get("gates") or {})},
        "candidate_states": states,
    }


def _queue_result(
    row: Mapping[str, Any],
    status: str,
    reason: str,
    blockers: Iterable[str] = (),
) -> Dict[str, Any]:
    priority = {
        "ready_for_execution": 0,
        "queued_until_gate": 1,
        "backlog_pending_gate": 2,
        "completed_or_not_applicable": 3,
        "workflow_audit_required": 4,
    }.get(status, 9)
    return {
        "schema_version": EXECUTION_QUEUE_SCHEMA_VERSION,
        "validation_id": str(row.get("validation_id", "")),
        "task_id": str(row.get("task_id", "")),
        "system": str(row.get("system", "")),
        "element": str(row.get("element", "")),
        "task_kind": str(row.get("task_kind", "")),
        "measurement": str(row.get("measurement", "")),
        "measurement_group_id": str(row.get("measurement_group_id", "")),
        "element_candidate_id": str(row.get("element_candidate_id", "")),
        "mechanism_bucket_id": str(row.get("mechanism_bucket_id", "")),
        "static_activation_status": str(row.get("activation_status", "")),
        "static_execution_gate_status": str(row.get("execution_gate_status", "")),
        "execution_status": status,
        "executable": status == "ready_for_execution",
        "queue_reason": reason,
        "blockers": "; ".join(dict.fromkeys(str(item) for item in blockers if item)),
        "queue_priority": priority,
        "allowed_structure_model_ids": str(row.get("allowed_structure_model_ids", "")),
        "comparison_or_control": str(row.get("comparison_or_control", "")),
    }


def build_execution_queue(
    plan_rows: Iterable[Mapping[str, Any]],
    feedback_snapshot: Mapping[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """Derive the *current* executable work package from immutable planning rows.

    A static plan is intentionally broader than the queue.  This function is
    the only V2.3 place that releases a result-entry template for a candidate.
    """

    snapshot = feedback_snapshot or load_feedback_gate_snapshot(None)
    gates = snapshot.get("gates") or {}
    baseline_state = str(gates.get("baseline_state", "awaiting_feedback"))
    benchmark_state = str(gates.get("benchmark_state", "candidate_gate_pending"))
    furoate_causality = _as_bool(gates.get("ag_furoate_deactivation_causality_established"))
    ni_low_cost_gate = _as_bool(gates.get("ni_low_cost_dft_gate_established"))
    candidate_states = snapshot.get("candidate_states") or {}
    baseline_id = element_candidate_id("ag_baseline", "Ag")
    cu_id = element_candidate_id("moderate_furfural_adsorption", "Cu")
    ni_id = element_candidate_id("OH_affinity_site", "Ni")
    bi_id = element_candidate_id("coverage_induced_poisoning", "Bi")
    her_bucket_id = mechanism_bucket_id("cathodic_HER_regeneration")
    queue: List[Dict[str, Any]] = []

    for row in plan_rows:
        candidate_id = str(row.get("element_candidate_id", ""))
        bucket_id = str(row.get("mechanism_bucket_id", ""))
        if bucket_id == her_bucket_id or "HER" in str(row.get("mechanism_bucket", "")):
            queue.append(
                _queue_result(
                    row,
                    "backlog_pending_gate",
                    "HER/bubble-assisted regeneration remains deferred until recoverable Ag deactivation is established.",
                    ["recoverable_ag_deactivation_not_established"],
                )
            )
            continue
        if candidate_id == baseline_id:
            if baseline_state == "baseline_failed":
                queue.append(
                    _queue_result(
                        row,
                        "workflow_audit_required",
                        "Ag baseline failed; comparative execution is blocked.",
                        ["ag_baseline_failed"],
                    )
                )
            elif baseline_state == "baseline_established":
                queue.append(
                    _queue_result(
                        row,
                        "completed_or_not_applicable",
                        "Ag baseline gate is already established.",
                    )
                )
            else:
                queue.append(
                    _queue_result(
                        row,
                        "ready_for_execution",
                        "Ag baseline control work package is the only initial executable package.",
                    )
                )
            continue
        if candidate_id == cu_id:
            if baseline_state == "baseline_failed":
                queue.append(
                    _queue_result(
                        row,
                        "workflow_audit_required",
                        "Ag baseline failed; Ag-Cu benchmark cannot be interpreted.",
                        ["ag_baseline_failed"],
                    )
                )
            elif baseline_state != "baseline_established":
                queue.append(
                    _queue_result(
                        row,
                        "queued_until_gate",
                        "Ag-Cu unlocks only after the complete Ag baseline gate passes.",
                        ["ag_baseline_not_established"],
                    )
                )
            elif benchmark_state == "benchmark_failed":
                queue.append(
                    _queue_result(
                        row,
                        "workflow_audit_required",
                        "Ag-Cu benchmark failed; audit the workflow before candidate work.",
                        ["ag_cu_benchmark_failed"],
                    )
                )
            elif benchmark_state == "benchmark_established":
                queue.append(
                    _queue_result(
                        row,
                        "completed_or_not_applicable",
                        "Ag-Cu benchmark gate is already established.",
                    )
                )
            else:
                queue.append(
                    _queue_result(
                        row,
                        "ready_for_execution",
                        "Ag-Cu benchmark is released after Ag baseline establishment.",
                    )
                )
            continue
        if candidate_id == bi_id:
            blockers: List[str] = []
            if baseline_state != "baseline_established":
                blockers.append("ag_baseline_not_established")
            if benchmark_state != "benchmark_established":
                blockers.append("ag_cu_benchmark_not_established")
            if not furoate_causality:
                blockers.append("ag_furoate_deactivation_causality_not_established")
            if blockers:
                queue.append(
                    _queue_result(
                        row,
                        "backlog_pending_gate",
                        "Ag-Bi remains a gated poisoning/desorption candidate.",
                        blockers,
                    )
                )
            else:
                queue.append(
                    _queue_result(
                        row,
                        "ready_for_execution",
                        "Ag-Bi is released only after matched controls and recoverable furoate-associated deactivation.",
                    )
                )
            continue
        if candidate_id == ni_id:
            blockers = []
            if baseline_state != "baseline_established":
                blockers.append("ag_baseline_not_established")
            if benchmark_state != "benchmark_established":
                blockers.append("ag_cu_benchmark_not_established")
            candidate = candidate_states.get(ni_id, {})
            mechanism_ready = _as_bool(candidate.get("mechanism_evidence_ready"))
            if not mechanism_ready and not ni_low_cost_gate:
                blockers.append("oh_interface_evidence_or_low_cost_dft_gate_missing")
            if blockers:
                queue.append(
                    _queue_result(
                        row,
                        "backlog_pending_gate",
                        "Ag-Ni remains a gated OH/interface candidate.",
                        blockers,
                    )
                )
            else:
                queue.append(
                    _queue_result(
                        row,
                        "ready_for_execution",
                        "Ag-Ni is released after controls and its OH/interface gate.",
                    )
                )
            continue
        queue.append(
            _queue_result(
                row,
                "backlog_pending_gate",
                "Non-canonical or exploratory plan row is not released by V2.3.",
                ["not_in_v2_3_execution_scope"],
            )
        )

    return sorted(
        queue,
        key=lambda value: (
            int(value.get("queue_priority", 9)),
            str(value.get("system", "")),
            str(value.get("task_kind", "")),
            str(value.get("measurement", "")),
        ),
    )


def queue_manifest(
    snapshot: Mapping[str, Any], queue: Iterable[Mapping[str, Any]]
) -> Dict[str, Any]:
    rows = list(queue)
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {
        "schema_version": EXECUTION_QUEUE_SCHEMA_VERSION,
        "feedback_present": bool(snapshot.get("present")),
        "feedback_run": snapshot.get("feedback_run", ""),
        "feedback_hash": snapshot.get("feedback_hash", ""),
        "queue_hash": hashlib.sha256(encoded).hexdigest(),
        "counts": {
            "ready_for_execution": sum(
                row.get("execution_status") == "ready_for_execution" for row in rows
            ),
            "queued_until_gate": sum(
                row.get("execution_status") == "queued_until_gate" for row in rows
            ),
            "backlog_pending_gate": sum(
                row.get("execution_status") == "backlog_pending_gate" for row in rows
            ),
            "workflow_audit_required": sum(
                row.get("execution_status") == "workflow_audit_required" for row in rows
            ),
        },
        "gates": snapshot.get("gates") or {},
    }
