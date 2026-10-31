"""DFT planning only: this module intentionally has no process-execution dependency."""

import json
from pathlib import Path
from typing import Protocol

from pipeline2.contracts import RunConfig
from pipeline2.orchestrator import BudgetGuard
from pipeline2.orchestrator.gate import GateValidator
from pipeline2.registry import content_hash
from pipeline2.safety import new_directory, write_new
from pipeline2.structure import StructureRecord, validate_structure, write_structure

from .parser import parse_vasp


class DFTAdapter(Protocol):
    def prepare_job(self, structure: StructureRecord, directory: str | Path) -> dict: ...
    def estimate_cost(self) -> dict: ...
    def validate_gate_and_budget(self) -> dict: ...
    def submit(self, job: dict) -> dict: ...
    def parse_result(self, path: str | Path, **kwargs) -> dict: ...


class PBSAdapter:
    def submit(
        self, script: str, *, dry_run: bool = True, allow_real_vasp_submit: bool = False
    ) -> dict:
        if dry_run:
            return {"status": "DRY_RUN", "expected_command": ["qsub", script], "executed": False}
        return {
            "status": "REAL_SUBMISSION_NOT_IMPLEMENTED"
            if allow_real_vasp_submit
            else "REAL_SUBMISSION_DISABLED",
            "executed": False,
        }

    def status(self, job_id: str) -> dict:
        return {
            "status": "NOT_IMPLEMENTED",
            "expected_command": ["qstat", job_id],
            "executed": False,
        }

    def cancel(self, job_id: str) -> dict:
        return {
            "status": "NOT_IMPLEMENTED",
            "expected_command": ["qdel", job_id],
            "executed": False,
        }


class VASPAdapter:
    def __init__(self, config: RunConfig, budget: BudgetGuard):
        self.config, self.budget = config, budget

    def estimate_cost(self) -> dict:
        resources = self.config.resources
        return {
            "jobs": 1,
            "estimated_core_hours": resources.cores * resources.walltime_hours,
            "cores": resources.cores,
            "walltime_hours": resources.walltime_hours,
            "estimate_status": "UNBENCHMARKED_PLANNING_ESTIMATE",
        }

    def validate_gate_and_budget(self) -> dict:
        gate = GateValidator().evaluate(self.config.task, self.config.gate_source)
        blockers = list(gate.blockers)
        if gate.status != "RELEASED":
            blockers.append("PENDING_GATE")
        if self.config.resources.cores > self.config.resources.available_cores:
            blockers.append("insufficient_available_cores")
        cost, budget = self.estimate_cost(), self.budget.snapshot()
        if budget["reserved_jobs"] + 1 > min(budget["max_jobs"], self.config.task.dft_budget_jobs):
            blockers.append("dft_job_budget_exceeded")
        if budget["reserved_core_hours"] + cost["estimated_core_hours"] > min(
            budget["max_core_hours"], self.config.task.dft_budget_core_hours
        ):
            blockers.append("dft_core_hour_budget_exceeded")
        return {
            "allowed": not blockers,
            "blockers": sorted(set(blockers)),
            "gate": gate.to_dict(),
            "cost": cost,
            "budget": budget,
        }

    def propose(self, structure_id: str) -> dict:
        check = self.validate_gate_and_budget()
        return {
            "job_id": "J_"
            + content_hash(
                {
                    "structure_id": structure_id,
                    "reference_profile_id": self.config.task.reference_profile_id,
                }
            ),
            "structure_id": structure_id,
            "reference_profile_id": self.config.task.reference_profile_id,
            "status": "PROPOSED"
            if check["allowed"]
            else "PENDING_GATE"
            if check["gate"]["status"] == "PENDING_GATE"
            else "BLOCKED",
            "executed": False,
            "source_type": "predicted",
            "validation": check,
        }

    def prepare_job(self, structure: StructureRecord, directory: str | Path) -> dict:
        validate_structure(
            structure.atoms,
            supported_elements=self.config.structure.supported_elements,
            min_distance_A=self.config.structure.min_distance_A,
        )
        proposal = self.propose(structure.structure_id)
        target = new_directory(directory)
        if proposal["status"] == "PROPOSED":
            proposal["structure_artifacts"] = write_structure(structure, target)
            write_new(
                target / "INCAR.example",
                "# EXAMPLE ONLY; NOT A VASP RUN PROFILE\n# No validated INCAR/KPOINTS/POTCAR is supplied.\n",
            )
            write_new(
                target / "KPOINTS.example",
                "# EXAMPLE ONLY; select a converged grid in an approved profile.\n",
            )
            proposal["status"] = "EXAMPLE_PREPARED_NOT_RUNNABLE"
        # Pending work gets a JSON proposal only, never a runnable PBS/VASP job.
        write_new(target / "job_manifest.json", json.dumps(proposal, indent=2) + "\n")
        return proposal

    def submit(self, job: dict) -> dict:
        check = self.validate_gate_and_budget()  # Never trust a prepared job's stale gate snapshot.
        task = self.config.task
        if not task.dry_run:
            status = (
                "REAL_SUBMISSION_NOT_IMPLEMENTED"
                if task.allow_real_vasp_submit
                else "REAL_SUBMISSION_DISABLED"
            )
            return {"status": status, "executed": False, "validation": check}
        if not check["allowed"]:
            return {
                "status": "PENDING_GATE"
                if check["gate"]["status"] == "PENDING_GATE"
                else "BLOCKED",
                "executed": False,
                "validation": check,
            }
        return {
            "status": "DRY_RUN",
            "expected_command": ["qsub", "<NOT_GENERATED_EXAMPLE_SCRIPT>"],
            "executed": False,
            "validation": check,
        }

    def parse_result(self, path: str | Path, **kwargs) -> dict:
        return parse_vasp(
            path, reference_profile_id=self.config.task.reference_profile_id, **kwargs
        )
