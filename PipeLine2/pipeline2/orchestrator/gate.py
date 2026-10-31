"""Fail-closed Pipeline1 bridge. User configuration never grants scientific authority."""

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from pipeline2.contracts import GateSource, TaskSpec
from pipeline2.registry import sha256_file

from .pipeline1_compat.attestation import (
    attest_result_artifacts,
    comparability_attestation,
    load_comparability_manifest,
)
from .pipeline1_compat.controls import (
    RESULT_CONTRACT_VERSION,
    STRUCTURE_MODEL_CATALOG,
    _baseline_furoate_causality,
    _control_pack,
    _ni_low_cost_gate,
)
from .pipeline1_compat.execution_queue import build_execution_queue
from .pipeline1_compat.identifiers import element_candidate_id


@dataclass
class GateDecision:
    status: str = "PENDING_GATE"
    blockers: list[str] = field(default_factory=list)
    source_hashes: dict[str, str] = field(default_factory=dict)
    allowed_structure_model_ids: list[str] = field(default_factory=list)
    allowed_operations: list[str] = field(default_factory=lambda: ["offline_exploration"])
    candidate_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def candidate_id(task: TaskSpec) -> str:
    element = task.dopants[0] if task.dopants else task.substrate
    bucket = {
        "Ag": "ag_baseline",
        "Cu": "moderate_furfural_adsorption",
        "Ni": "OH_affinity_site",
        "Bi": "coverage_induced_poisoning",
    }.get(element, "exploratory")
    return element_candidate_id(bucket, element)


class GateValidator:
    def evaluate(self, task: TaskSpec, source: GateSource | None) -> GateDecision:
        result = GateDecision(candidate_id=candidate_id(task))
        if source is None:
            result.blockers = ["formal_pipeline1_source_missing"]
            return result
        try:
            self._evaluate(task, source, result)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            result.status = "PENDING_GATE"
            result.allowed_operations = ["offline_exploration"]
            result.blockers = [f"source_verification_failed:{exc}"]
        return result

    @staticmethod
    def _read(path: Path, result: GateDecision) -> dict:
        result.source_hashes[str(path.resolve())] = sha256_file(path)
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Expected JSON object")
        return value

    def _verify_hashes(self, mapping: dict, result: GateDecision, within: Path | None = None):
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError("Missing audit hash map")
        for name, expected in mapping.items():
            path = Path(name).resolve()
            if within and within != path.parent:
                raise ValueError("Audit input belongs to another run")
            actual = sha256_file(path)
            result.source_hashes[str(path)] = actual
            if actual != expected:
                raise ValueError(f"sha256 mismatch:{path.name}")

    def _evaluate(self, task: TaskSpec, source: GateSource, result: GateDecision):
        feedback, p1, validation = (
            Path(source.feedback_run).resolve(),
            Path(source.pipeline1_run).resolve(),
            Path(source.validation_run).resolve(),
        )
        overlay = self._read(feedback / "feedback_overlay.json", result)
        audit = self._read(feedback / "feedback_audit.json", result)
        if overlay.get("schema_version") != "pipeline-feedback-v2.3-1":
            result.blockers = ["legacy_or_unsupported_snapshot_cannot_release"]
            return
        if overlay.get("source_type") in {"fixture", "mock"}:
            raise ValueError("Fixture snapshots cannot release scientific gates")
        if audit.get("schema_version") != overlay["schema_version"]:
            raise ValueError("Feedback/audit schema mismatch")
        if (
            overlay.get("append_only") is not True
            or audit.get("source_artifacts_unchanged") is not True
        ):
            raise ValueError("Immutable-source audit required")
        for payload in (overlay, audit):
            if Path(payload.get("pipeline1_run", "")).resolve() != p1:
                raise ValueError("Wrong Pipeline1 run")
            if Path(payload.get("validation_run", "")).resolve() != validation:
                raise ValueError("Wrong validation run")
        before, after = (
            audit.get("source_artifact_hashes_before"),
            audit.get("source_artifact_hashes_after"),
        )
        if before != after:
            raise ValueError("Pipeline1 source hashes changed")
        self._verify_hashes(after or {}, result, p1)
        self._verify_hashes(audit.get("validation_input_hashes") or {}, result)
        required = {
            str((validation / name).resolve())
            for name in ["validation_plan.csv", "validation_manifest.json"]
        }
        if not required <= set(audit["validation_input_hashes"]):
            raise ValueError("Validation plan/manifest not attested")
        with (validation / "validation_plan.csv").open(encoding="utf-8") as handle:
            plans = list(csv.DictReader(handle))
        by_id = {row.get("validation_id"): row for row in plans}
        if len(by_id) != len(plans):
            raise ValueError("Duplicate validation ids in plan")
        raw_results_path = Path(audit.get("result_source", "")).resolve()
        if str(raw_results_path) not in audit["validation_input_hashes"]:
            raise ValueError("Raw result table is not attested")
        with raw_results_path.open(encoding="utf-8") as handle:
            raw_results = list(csv.DictReader(handle))
        raw_by_id = {row.get("validation_id"): row for row in raw_results}
        if len(raw_by_id) != len(raw_results):
            raise ValueError("Duplicate validation result ids")
        qualifying = []
        manifest_info = audit.get("comparability_manifest") or {}
        manifest_path = manifest_info.get("path")
        registry = load_comparability_manifest(manifest_path)
        if manifest_path and Path(manifest_path).is_file():
            result.source_hashes[str(Path(manifest_path).resolve())] = sha256_file(manifest_path)
        artifact_base = Path(
            audit.get("result_source", str(validation / "validation_results.csv"))
        ).parent
        for row in overlay.get("results", []):
            if row.get("calibration_eligible") is not True:
                continue
            plan = by_id.get(row.get("validation_id"))
            if not plan or any(
                str(row.get(k, "")) != str(plan.get(k, ""))
                for k in ("element_candidate_id", "measurement_id", "task_id")
            ):
                raise ValueError("Result/plan identity mismatch")
            raw_row = raw_by_id.get(row.get("validation_id"))
            critical = (
                "artifact_path",
                "artifact_sha256",
                "verification_artifact_path",
                "verification_artifact_sha256",
                "reviewed_by",
                "reviewed_at",
                "verified_by",
                "verified_at",
                "criterion_result",
                "human_outcome",
                "replicate_count",
                "run_context_id",
                "conditions_hash",
                "comparability_scope_id",
                "structure_model_id",
                "result_contract_version",
                "result_status",
                "realized_structure_model",
                "verification_method",
                "observed_value",
                "reference_value",
                "delta_value",
                "unit",
                "uncertainty",
                "comparator",
            )
            if not raw_row or any(
                str(row.get(k, "")).strip() != str(raw_row.get(k, "")).strip() for k in critical
            ):
                raise ValueError("Overlay result differs from attested raw result table")
            if str(raw_row.get("structure_verified", "")).lower() not in {"true", "1", "yes", "y"}:
                raise ValueError("Raw result does not verify the structure")
            if row.get("result_status", row.get("status")) != "completed":
                raise ValueError("Incomplete result cannot calibrate")
            if not row.get("reviewed_by") or not row.get("reviewed_at"):
                raise ValueError("Human result review required")
            datetime.fromisoformat(row["reviewed_at"].replace("Z", "+00:00"))
            model = STRUCTURE_MODEL_CATALOG.get(row.get("structure_model_id"))
            if (
                row.get("result_contract_version") != RESULT_CONTRACT_VERSION
                or row.get("structure_verified") is not True
                or not model
                or row.get("realized_structure_model") != model["model"]
            ):
                raise ValueError("Reviewed structure and current result contract required")
            for required_field in (
                "verification_method",
                "verified_by",
                "verified_at",
                "uncertainty",
                "observed_value",
                "reference_value",
                "delta_value",
                "unit",
                "comparator",
            ):
                if not str(row.get(required_field, "")).strip():
                    raise ValueError(f"Missing result evidence field:{required_field}")
            datetime.fromisoformat(row["verified_at"].replace("Z", "+00:00"))
            if int(row.get("replicate_count", 0)) < 1:
                raise ValueError("Invalid replicate count")
            expected_outcome = {
                "supports": "pass",
                "contradicts": "fail",
                "inconclusive": "inconclusive",
            }.get(row.get("human_outcome"))
            if not expected_outcome or row.get("criterion_result") != expected_outcome:
                raise ValueError("Criterion/outcome mismatch")
            attestations = attest_result_artifacts(row, artifact_base)
            if any(a["status"] != "verified_local" for a in attestations):
                raise ValueError("Local result and structure artifacts must be verified")
            for item in attestations:
                result.source_hashes[item["resolved_path"]] = item["actual_sha256"]
            if comparability_attestation(row, registry)["status"] != "human_approved":
                raise ValueError("Human-approved comparability required")
            if str(row.get("data_source", "")) in {"fixture", "mock", "synthetic"}:
                raise ValueError("Fixture/mock cannot release scientific gates")
            qualifying.append(row)
        gates = overlay.get("gates") or {}
        # Recompute the complete control packs from reviewed evidence, not gate text.
        packs = {}
        for control in ("baseline", "benchmark"):
            pack = _control_pack(qualifying, control)
            packs[control] = pack
            declared = gates.get(f"{control}_state")
            if (
                declared in {f"{control}_established", f"{control}_failed"}
                and declared != pack["state"]
            ):
                raise ValueError(f"{control} declaration lacks complete evidence")
            if gates.get(f"{control}_gate_complete") is True and not pack["complete"]:
                raise ValueError(f"{control} completeness conflict")
        if gates.get(
            "ag_furoate_deactivation_causality_established"
        ) is True and not _baseline_furoate_causality(packs["baseline"]):
            raise ValueError("Furoate causality lacks complete reviewed evidence")
        if gates.get("ni_low_cost_dft_gate_established") is True and not _ni_low_cost_gate(
            qualifying
        ):
            raise ValueError("Ni low-cost gate lacks reviewed DFT evidence")
        if gates.get("benchmark_state") == "benchmark_established":
            baseline_scopes = set(packs["baseline"].get("comparability_scope_ids", []))
            benchmark_scopes = set(packs["benchmark"].get("comparability_scope_ids", []))
            if not baseline_scopes or baseline_scopes != benchmark_scopes:
                raise ValueError("Baseline/benchmark comparability not established")
        baseline_scopes = set(packs["baseline"].get("comparability_scope_ids", []))
        benchmark_scopes = set(packs["benchmark"].get("comparability_scope_ids", []))
        expected_benchmark_state = (
            packs["benchmark"]["state"]
            if (
                packs["baseline"]["state"] == "baseline_established"
                and baseline_scopes
                and baseline_scopes == benchmark_scopes
            )
            else "candidate_gate_pending"
        )
        if (
            gates.get("baseline_state") != packs["baseline"]["state"]
            or gates.get("benchmark_state") != expected_benchmark_state
        ):
            raise ValueError("Declared control states differ from reviewed evidence")
        if gates.get("baseline_gate_complete") is not packs["baseline"]["complete"] or gates.get(
            "benchmark_gate_complete"
        ) is not (
            packs["benchmark"]["complete"] and packs["baseline"]["state"] == "baseline_established"
        ):
            raise ValueError("Declared control completeness differs from reviewed evidence")
        if task.substrate != "Ag":
            result.blockers = ["not_in_pipeline1_ag_scope"]
            return
        matching = [
            row
            for row in plans
            if row.get("element_candidate_id") == result.candidate_id
            and row.get("task_kind") == "dft"
        ]
        if not matching:
            result.blockers = ["candidate_has_no_formal_dft_plan"]
            return
        if task.hypothesis_id not in {row.get("representative_hypothesis_id") for row in matching}:
            result.blockers = ["hypothesis_not_in_formal_candidate_plan"]
            return
        # Human deferral is never overridden by rank or queue score.
        if any(
            "defer" in str(row.get("human_decision", row.get("decision", ""))).lower()
            for row in matching
        ):
            result.blockers = ["human_deferred"]
            return
        states = {row["element_candidate_id"]: row for row in overlay.get("candidate_states", [])}
        queue = build_execution_queue(matching, {"gates": gates, "candidate_states": states})
        blocked = [row for row in queue if row.get("execution_status") != "ready_for_execution"]
        if blocked:
            result.blockers = sorted(
                {row.get("blockers") or row["execution_status"] for row in blocked}
            )
            return
        # Require the exact structure model allowed by Pipeline1, not merely its element.
        expected_model = {
            "Ag": "SM_AG_CLEAN_SURFACE_V1",
            "Cu": "SM_AG_CU_SURFACE_SUB_V1",
            "Ni": "SM_AG_NI_SURFACE_SUB_V1",
        }.get(task.dopants[0] if task.dopants else "Ag")
        allowed = sorted(
            {
                v.strip()
                for row in matching
                for v in str(row.get("allowed_structure_model_ids", "")).split(";")
                if v.strip()
            }
        )
        result.allowed_structure_model_ids = allowed
        if expected_model not in allowed:
            result.blockers = ["substitution_structure_not_allowed_by_pipeline1"]
            return
        if not task.evidence_refs:
            result.blockers = ["task_evidence_refs_missing"]
            return
        result.status = "RELEASED"
        result.blockers = []
        result.allowed_operations = ["offline_exploration", "dft_proposal", "prepare_example"]


ScientificGate = GateValidator
