from dataclasses import dataclass
from typing import Protocol

from ase import Atoms
from pydantic import model_validator

from pipeline2.contracts import Contract, SourceType


class PredictionResult(Contract):
    structure_id: str
    model_id: str
    model_version: str
    model_hash: str
    model_branch: str
    task_name: str
    energy_eV: float | None
    forces_eV_per_A: list[list[float]] | None
    reference_profile_id: str
    prediction_status: str
    source_type: SourceType
    runtime_s: float
    composition: dict[str, int]
    charge: int
    comparison_context_id: str
    cell_hash: str = ""
    pbc: list[bool] = []
    fixed_indices: list[int] = []
    error_message: str | None = None
    schema_version: str = "pipeline2-prediction-1"

    @model_validator(mode="after")
    def consistent(self):
        if self.prediction_status == "SUCCESS":
            if self.energy_eV is None or self.forces_eV_per_A is None:
                raise ValueError("Successful prediction needs E and F")
            if len(self.forces_eV_per_A) != sum(self.composition.values()) or any(
                len(f) != 3 for f in self.forces_eV_per_A
            ):
                raise ValueError("Force array shape mismatch")
        return self


@dataclass
class RelaxationResult:
    atoms: Atoms
    prediction: PredictionResult | None
    prediction_status: str
    converged: bool
    steps: int
    error_message: str | None = None


class ModelAdapter(Protocol):
    @property
    def model_id(self) -> str: ...
    def capabilities(self) -> dict: ...
    def supports(self, atoms: Atoms, task: str) -> bool: ...
    def predict(self, atoms: Atoms, *, task: str) -> PredictionResult: ...
    def relax(self, atoms: Atoms, *, fmax: float, max_steps: int) -> RelaxationResult: ...
