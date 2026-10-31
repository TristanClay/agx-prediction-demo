"""Versioned, strict user-facing task and result contracts."""

import json
from pathlib import Path
from typing import Annotated, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, model_validator

SourceType = Literal["fixture", "mock", "predicted", "real_dft", "experimental"]
Nonempty = Annotated[str, Field(min_length=1)]
Nonnegative = Annotated[float, Field(ge=0)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class TaskSpec(Contract):
    schema_version: Literal["pipeline2-task-1"] = "pipeline2-task-1"
    task_id: Nonempty
    source_pipeline: Nonempty
    hypothesis_id: Nonempty
    evidence_refs: list[Nonempty]
    substrate: Nonempty
    surface: Nonempty
    dopants: list[Nonempty]
    dopant_sites: list[Annotated[int, Field(ge=0)]]
    adsorbates: list[Nonempty]
    coverage: Annotated[float, Field(ge=0, le=1)]
    prediction_targets: list[Nonempty]
    reference_profile_id: Nonempty
    model_candidates: list[Nonempty]
    dft_budget_jobs: Annotated[int, Field(ge=0)]
    dft_budget_core_hours: Nonnegative
    gate_status: Literal["PENDING_GATE", "RELEASED", "VALIDATED"] = "PENDING_GATE"
    human_review_required: bool = True
    allow_real_vasp_submit: bool = False
    dry_run: bool = True
    data_source: SourceType

    @model_validator(mode="after")
    def scope_checks(self):
        if len(self.dopants) > 1:
            raise ValueError("MVP supports at most one substitutional dopant per task")
        if len(set(self.dopant_sites)) != len(self.dopant_sites):
            raise ValueError("Duplicate dopant sites")
        if not self.model_candidates or not self.prediction_targets:
            raise ValueError("Models and targets must not be empty")
        return self


class GateSource(Contract):
    feedback_run: Nonempty
    pipeline1_run: Nonempty
    validation_run: Nonempty


class StructureConfig(Contract):
    input_path: str | None = None
    adsorbate_path: str | None = None
    adsorbed_paths: list[str] = []
    fixed_indices: list[int] | None = None
    supported_elements: list[str] = ["Ag", "Cu", "Ni", "Bi", "C", "H", "O"]
    min_distance_A: Annotated[float, Field(gt=0)] = 0.6
    heights_A: list[Annotated[float, Field(ge=2)]] = [3.0, 3.5]


class Objective(Contract):
    property: Nonempty
    direction: Literal["min", "max"]


class Bound(Contract):
    property: Nonempty
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def bounds_check(self):
        if self.minimum is None and self.maximum is None:
            raise ValueError("A constraint needs at least one bound")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Reversed constraint bounds")
        return self


class ScreeningConfig(Contract):
    objectives: list[Objective] = [Objective(property="adsorption_energy_eV", direction="min")]
    constraints: list[Bound] = []
    top_k: Annotated[int, Field(gt=0)] = 5


class AcquisitionConfig(Contract):
    strategy: Literal["random", "diversity", "score"] = "diversity"
    seed: int = 42
    batch_size: Annotated[int, Field(ge=0)] = 2
    weights: dict[str, Nonnegative] = {
        "potential": 1.0,
        "uncertainty": 0.0,
        "diversity": 1.0,
        "estimated_cost": 1.0,
    }

    @model_validator(mode="after")
    def known_weights(self):
        if set(self.weights) != {"potential", "uncertainty", "diversity", "estimated_cost"}:
            raise ValueError("Specify exactly the four acquisition weights")
        return self


class Resources(Contract):
    cores: Annotated[int, Field(gt=0)] = 16
    walltime_hours: Annotated[float, Field(gt=0)] = 1.0
    available_cores: Annotated[int, Field(ge=0)] = 16


class RunConfig(Contract):
    task: TaskSpec
    gate_source: GateSource | None = None
    structure: StructureConfig = StructureConfig()
    screening: ScreeningConfig = ScreeningConfig()
    acquisition: AcquisitionConfig = AcquisitionConfig()
    resources: Resources = Resources()
    mock_seed: int = 42


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate configuration key:{key}")
        result[key] = value
    return result


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node):
    return _unique_pairs(
        [(loader.construct_object(k), loader.construct_object(v)) for k, v in node.value]
    )


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def load_payload(path: str | Path):
    source = Path(path).resolve()
    raw = source.read_text(encoding="utf-8")
    try:
        return (
            json.loads(raw, object_pairs_hook=_unique_pairs)
            if source.suffix.lower() == ".json"
            else yaml.load(raw, Loader=_UniqueLoader)
        )
    except (yaml.YAMLError, TypeError) as exc:
        raise ValueError(f"Invalid configuration syntax:{exc}") from exc


def load_config(path: str | Path) -> RunConfig:
    source = Path(path).resolve()
    config = RunConfig.model_validate(load_payload(source))
    # Relative input paths are always relative to the configuration file, never cwd.
    for field in ("input_path", "adsorbate_path"):
        value = getattr(config.structure, field)
        if value:
            setattr(config.structure, field, str((source.parent / value).resolve()))
    config.structure.adsorbed_paths = [
        str((source.parent / p).resolve()) for p in config.structure.adsorbed_paths
    ]
    if config.gate_source:
        for field in ("feedback_run", "pipeline1_run", "validation_run"):
            value = getattr(config.gate_source, field)
            setattr(config.gate_source, field, str((source.parent / value).resolve()))
    return config
