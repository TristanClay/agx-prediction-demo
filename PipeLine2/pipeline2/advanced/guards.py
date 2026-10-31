"""Read-only user challenges call the same deterministic MVP validation tools."""

from pathlib import Path
from tempfile import TemporaryDirectory

from ase import Atoms
from ase.constraints import FixAtoms

from pipeline2.models import PredictionResult
from pipeline2.orchestrator.budget import BudgetExceeded, BudgetGuard
from pipeline2.orchestrator.gate import GateValidator
from pipeline2.properties import adsorption_energy
from pipeline2.registry import DataRegistry
from pipeline2.structure import InvalidStructure, validate_structure

from .contracts import AdvancedConfig
from .dft import TeachingPBSAdapter


def run_guardrail(case: str, data: dict) -> dict:
    config = AdvancedConfig.model_validate(data["config"])
    if case == "collision":
        geometry = next(r["geometry"] for r in data["structures"] if r["role"] == "reference_slab")
        atoms = Atoms(
            numbers=geometry["numbers"],
            positions=geometry["positions_A"],
            cell=geometry["cell_A"],
            pbc=geometry["pbc"],
        )
        atoms.set_constraint(FixAtoms(indices=geometry["fixed_indices"]))
        atoms.positions[1] = atoms.positions[0]
        try:
            validate_structure(atoms)
        except InvalidStructure as exc:
            return {"case": case, "status": exc.status, "reason": str(exc)}
        raise AssertionError("Collision was not rejected")
    if case == "charged_reference":
        candidate = data["candidates"][0]
        predictions = {
            row["structure_id"]: PredictionResult.model_validate(
                {k: v for k, v in row.items() if k in PredictionResult.model_fields}
            )
            for row in data["predictions"]
        }
        charged = predictions[candidate["molecule_reference_id"]].model_copy(update={"charge": -1})
        prop = adsorption_energy(
            predictions[candidate["structure_id"]],
            predictions[candidate["slab_reference_id"]],
            charged,
        )
        return {"case": case, "status": prop.status, "reason": prop.to_dict()}
    gate = GateValidator().evaluate(config.task, config.gate_source).to_dict()
    if case == "scientific_gate":
        return {"case": case, "status": gate["status"], "reason": gate["blockers"]}
    if case == "zero_formal_budget":
        with TemporaryDirectory(prefix="agx-zero-budget-") as temporary:
            guard = BudgetGuard(DataRegistry(Path(temporary) / "budget.sqlite"), "zero", 0, 0.0)
            try:
                guard.reserve("demo-must-fail", 16.0)
            except BudgetExceeded as exc:
                return {
                    "case": case,
                    "status": "BLOCKED",
                    "reason": str(exc),
                    "budget": guard.snapshot(),
                    "gate": gate,
                    "executed": False,
                }
        raise AssertionError("Zero budget was not blocked")
    if case == "submit_even_if_requested":
        return {
            "case": case,
            **TeachingPBSAdapter().submit(dry_run=False, allow_real_vasp_submit=True),
        }
    raise ValueError("Unknown guardrail case")
