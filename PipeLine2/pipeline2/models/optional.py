"""Reserved real-model interfaces. They never fabricate or download predictions."""

from typing import Protocol


class OptionalFoundationAdapter:
    model_id = "unconfigured-foundation-model"

    def __init__(self, *args, **kwargs):
        # Configuration is retained for a future explicit integration, never executed.
        self.configuration = kwargs

    def capabilities(self) -> dict:
        return {
            "model_id": self.model_id,
            "status": "NOT_CONFIGURED",
            "runtime_validation": "NOT_RUNTIME_VERIFIED",
            "tasks": [],
            "reason": "Real backend and licensed local weights have not been integrated",
            "automatic_download": False,
        }

    def supports(self, atoms, task: str) -> bool:
        return False

    def initialize(self) -> dict:
        return self.capabilities()

    def predict(self, atoms, *, task: str = "energy_forces"):
        raise NotImplementedError(f"NOT_CONFIGURED:{self.model_id}; no synthetic fallback")

    def relax(self, atoms, *, fmax: float, max_steps: int):
        raise NotImplementedError(f"NOT_CONFIGURED:{self.model_id}; no synthetic fallback")


class UMAAdapter(OptionalFoundationAdapter):
    """Future fairchem UMA integration; declare exact task/theory and model license."""

    model_id = "UMA"


class MACEMH1Adapter(OptionalFoundationAdapter):
    """Future MACE-MH-1 integration; declare exact head, elements and checkpoint hash."""

    model_id = "MACE-MH-1"


# Preserve the original public import name.
MACEAdapter = MACEMH1Adapter


class DPA4Adapter(OptionalFoundationAdapter):
    """Future DPA4-Beta integration; declare exact branch and level of theory."""

    model_id = "DPA4"


class ModelCalibration(Protocol):
    def fit(self, *, reviewed_labels: list[dict], dataset_version: str) -> dict: ...
    def evaluate(self, *, independent_labels: list[dict]) -> dict: ...


class FineTunePlugin(Protocol):
    def train(self, *, reviewed_labels: list[dict], frozen_split: dict) -> dict: ...
