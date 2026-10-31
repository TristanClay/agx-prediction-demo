"""An analytic test potential, not a learned or chemically meaningful model."""

from pathlib import Path

import numpy as np
from ase.calculators.calculator import Calculator, all_changes

from pipeline2.registry import content_hash, sha256_file

from .ase_adapter import ASECalculatorAdapter


class MockCalculator(Calculator):
    implemented_properties = ["energy", "forces"]

    def __init__(self, seed=42):
        super().__init__()
        self.scale = 0.01 * (1 + seed % 97 / 100)

    def calculate(self, atoms=None, properties=None, system_changes=all_changes):
        super().calculate(atoms, properties, system_changes)
        assert self.atoms is not None
        vectors = self.atoms.get_all_distances(mic=bool(any(self.atoms.pbc)), vector=True)
        energy = -0.01 * float(self.atoms.numbers.sum())
        forces = np.zeros((len(self.atoms), 3))
        for i in range(len(self.atoms)):
            for j in range(i + 1, len(self.atoms)):
                v = vectors[i, j]
                pair = self.scale * np.exp(-np.dot(v, v) / 9.0)
                energy -= pair
                forces[i] += 2 * pair / 9.0 * v
                forces[j] -= 2 * pair / 9.0 * v
        self.results = {"energy": energy, "forces": forces}


class MockModelAdapter(ASECalculatorAdapter):
    def __init__(self, seed: int = 42, reference_profile_id: str = "mock-neutral-v1"):
        params = {
            "potential": "analytic-gaussian-pairs-v1",
            "seed": seed,
            "implementation_sha256": sha256_file(Path(__file__)),
        }
        super().__init__(
            MockCalculator(seed),
            model_id="mock-v1",
            model_version="1",
            model_hash=content_hash(params),
            model_branch="synthetic-test-potential",
            reference_profile_id=reference_profile_id,
            supported_elements=["Ag", "Cu", "Ni", "Bi", "C", "H", "O"],
            source_type="mock",
        )
