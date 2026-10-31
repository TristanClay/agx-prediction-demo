"""Advanced contracts wrap existing MVP contracts without changing their defaults."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from pipeline2.contracts import Contract, GateSource, TaskSpec, load_payload


def demo_task() -> TaskSpec:
    return TaskSpec(
        task_id="advanced-offline-teaching",
        source_pipeline="pipeline_1",
        hypothesis_id="TEACHING_NOT_A_SCIENTIFIC_HYPOTHESIS",
        evidence_refs=[],
        substrate="Ag",
        surface="111",
        dopants=[],
        dopant_sites=[],
        adsorbates=["FF"],
        coverage=1 / 9,
        prediction_targets=["adsorption_energy"],
        reference_profile_id="mock-neutral-v1",
        model_candidates=["mock-v1"],
        dft_budget_jobs=0,
        dft_budget_core_hours=0.0,
        data_source="fixture",
    )


class TeachingBudget(Contract):
    """Simulation allowance, never a formal DFT allocation or consumption."""

    jobs: Annotated[int, Field(ge=0)] = 12
    core_hours: Annotated[float, Field(ge=0)] = 192.0
    per_round_jobs: Annotated[int, Field(ge=0)] = 4
    per_round_core_hours: Annotated[float, Field(ge=0)] = 64.0


class AdvancedConfig(Contract):
    schema_version: Literal["pipeline2-advanced-config-1"] = "pipeline2-advanced-config-1"
    mode: Literal["DEMO"] = "DEMO"
    data_origin: Literal["SYNTHETIC"] = "SYNTHETIC"
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)] = 42
    task: TaskSpec = Field(default_factory=demo_task)
    gate_source: GateSource | None = None
    pipeline1_cards: str | None = None
    heights_A: list[Annotated[float, Field(ge=2.5, le=4.0)]] = [2.5, 3.0, 3.5, 4.0]
    rounds: Annotated[int, Field(ge=1, le=10)] = 3
    batch_size: Annotated[int, Field(ge=0, le=16)] = 4
    initial_size: Annotated[int, Field(ge=0, le=64)] = 8
    pool_size: Annotated[int, Field(ge=1, le=128)] = 32
    test_size: Annotated[int, Field(ge=1, le=64)] = 8
    min_training_labels: Annotated[int, Field(ge=8)] = 8
    top_k: Annotated[int, Field(ge=1)] = 5
    teaching_budget: TeachingBudget = Field(default_factory=TeachingBudget)

    @model_validator(mode="after")
    def demo_only(self):
        if self.task.allow_real_vasp_submit or not self.task.dry_run:
            raise ValueError("Advanced Demo has no real submission channel")
        if self.task.dft_budget_jobs != 0 or self.task.dft_budget_core_hours != 0:
            raise ValueError("Formal DFT budget must remain zero; use teaching_budget")
        if self.task.data_source != "fixture":
            raise ValueError("Advanced teaching task must retain fixture lineage")
        if not self.heights_A or len(set(self.heights_A)) != len(self.heights_A):
            raise ValueError("Nonempty unique adsorption heights required")
        return self


def load_advanced_config(path) -> AdvancedConfig:
    return AdvancedConfig.model_validate(load_payload(path))
