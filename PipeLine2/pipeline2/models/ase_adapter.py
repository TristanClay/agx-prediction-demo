import time
from collections import Counter
from typing import Literal

import numpy as np
from ase import Atoms
from ase.optimize import BFGS

from pipeline2.registry import content_hash
from pipeline2.structure import (
    InvalidStructure,
    canonical_structure,
    structure_id,
    validate_structure,
)

from .base import PredictionResult, RelaxationResult


class ASECalculatorAdapter:
    """Caller must declare model provenance; the adapter does not invent a theory."""

    def __init__(
        self,
        calculator,
        *,
        model_id: str,
        model_version: str,
        model_hash: str,
        model_branch: str,
        reference_profile_id: str,
        supported_elements: list[str],
        comparison_context_id: str = "neutral-vacuum-demo-v1",
        source_type: Literal["mock", "predicted"] = "predicted",
    ):
        if source_type not in {"mock", "predicted"}:
            raise ValueError("Calculator output is mock or predicted, never real_dft")
        if not all(
            [
                model_id,
                model_version,
                model_hash,
                model_branch,
                reference_profile_id,
                comparison_context_id,
            ]
        ):
            raise ValueError("Model provenance must be explicit")
        self.calculator = calculator
        self.model_id, self.model_version, self.model_hash = model_id, model_version, model_hash
        self.model_branch, self.reference_profile_id = model_branch, reference_profile_id
        self.supported_elements = supported_elements
        self.comparison_context_id, self.source_type = comparison_context_id, source_type

    def capabilities(self) -> dict:
        return {
            "tasks": ["energy_forces"],
            "supported_elements": self.supported_elements,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "model_hash": self.model_hash,
            "model_branch": self.model_branch,
            "reference_profile_id": self.reference_profile_id,
            "calibration_status": "UNCALIBRATED",
            "source_type": self.source_type,
        }

    def supports(self, atoms: Atoms, task: str) -> bool:
        return task == "energy_forces" and set(atoms.get_chemical_symbols()) <= set(
            self.supported_elements
        )

    def predict(self, atoms: Atoms, *, task: str = "energy_forces") -> PredictionResult:
        started = time.perf_counter()
        # No calculator is invoked before deterministic structural validation succeeds.
        try:
            validate_structure(
                atoms, supported_elements=self.supported_elements, require_slab=bool(any(atoms.pbc))
            )
            if not self.supports(atoms, task):
                raise InvalidStructure("Unsupported model task/elements")
        except InvalidStructure as exc:
            return self._result(atoms, task, started, "INVALID_OUT_OF_SCOPE", error=str(exc))
        try:
            working = atoms.copy()
            working.calc = self.calculator
            energy = float(working.get_potential_energy())
            forces = working.get_forces(apply_constraint=False)
            if (
                not np.isfinite(energy)
                or forces.shape != (len(atoms), 3)
                or not np.isfinite(forces).all()
            ):
                raise ValueError("Non-finite energy or invalid force array")
            return self._result(atoms, task, started, "SUCCESS", energy, forces.tolist())
        except Exception as exc:
            return self._result(atoms, task, started, "FAILED", error=f"{type(exc).__name__}:{exc}")

    def _result(self, atoms, task, started, status, energy=None, forces=None, error=None):
        try:
            sid = structure_id(atoms)
            geometry = canonical_structure(atoms)
        except (InvalidStructure, ValueError):
            sid = "INVALID_" + content_hash({"numbers": atoms.numbers.tolist()})
            geometry = {}
        return PredictionResult(
            structure_id=sid,
            model_id=self.model_id,
            model_version=self.model_version,
            model_hash=self.model_hash,
            model_branch=self.model_branch,
            task_name=task,
            energy_eV=energy,
            forces_eV_per_A=forces,
            reference_profile_id=self.reference_profile_id,
            prediction_status=status,
            source_type=self.source_type,
            runtime_s=time.perf_counter() - started,
            composition=dict(Counter(atoms.get_chemical_symbols())),
            charge=int(atoms.info.get("charge", 0)),
            comparison_context_id=self.comparison_context_id,
            cell_hash=content_hash(geometry.get("cell_A", [])),
            pbc=atoms.pbc.tolist(),
            fixed_indices=geometry.get("fixed_indices", []),
            error_message=error,
        )

    def relax(self, atoms: Atoms, *, fmax: float, max_steps: int) -> RelaxationResult:
        if not np.isfinite(fmax) or fmax <= 0 or max_steps < 1:
            raise ValueError("Positive relaxation limits required")
        initial = self.predict(atoms, task="energy_forces")
        if initial.prediction_status != "SUCCESS":
            return RelaxationResult(
                atoms.copy(), initial, initial.prediction_status, False, 0, initial.error_message
            )
        working = atoms.copy()
        working.calc = self.calculator
        try:
            optimizer = BFGS(working, logfile=None)
            converged = bool(optimizer.run(fmax=fmax, steps=max_steps))
            final = self.predict(working, task="energy_forces")
            status = (
                "SUCCESS" if converged and final.prediction_status == "SUCCESS" else "NOT_CONVERGED"
            )
            return RelaxationResult(working, final, status, converged, optimizer.nsteps)
        except Exception as exc:
            return RelaxationResult(working, None, "FAILED", False, 0, str(exc))
