"""Versioned boundary contracts; execution mode never erases data origin."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from pipeline2.contracts import Contract, Nonempty

Mode = Literal["LIVE", "REPLAY", "SYNTHETIC"]
Status = Literal[
    "PENDING", "RUNNING", "COMPLETED", "FAILED", "BLOCKED", "WAITING_FOR_REVIEW", "SKIPPED"
]
Origin = Literal["REPLAY", "SYNTHETIC", "EXPERIMENTAL", "MODEL_PREDICTION"]


class ArtifactRef(Contract):
    path: Nonempty
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Provenance(Contract):
    run_id: Nonempty
    origin: Origin
    producer: Nonempty
    version: Nonempty
    artifacts: list[ArtifactRef] = Field(min_length=1)
    source_run_id: str | None = None
    limitations: list[str] = []


class KnowledgeTask(Contract):
    schema_version: Literal["agx-knowledge-task-1"] = "agx-knowledge-task-1"
    task_id: Nonempty
    run_id: Nonempty
    hypothesis_id: Nonempty
    hypothesis: Nonempty
    candidate_elements: list[Nonempty] = Field(min_length=1)
    evidence_refs: list[Nonempty] = Field(min_length=1)
    scientific_constraints: list[Nonempty] = Field(min_length=1)
    target_properties: list[Nonempty] = Field(min_length=1)
    priority: Literal["low", "medium", "high"]
    confidence: Annotated[float, Field(ge=0, le=1)] | None
    provenance: Provenance

    @model_validator(mode="after")
    def identity(self):
        if self.provenance.run_id != self.run_id:
            raise ValueError("Knowledge task run identity mismatch")
        return self


class Property(Contract):
    name: Literal["adsorption_energy"] = "adsorption_energy"
    value: float
    unit: Literal["eV"] = "eV"
    definition: Literal["E(adsorbed)-E(slab)-E(molecule)"] = "E(adsorbed)-E(slab)-E(molecule)"
    reference_structure_ids: list[Nonempty] = Field(min_length=3, max_length=3)


class ScreeningResult(Contract):
    schema_version: Literal["agx-screening-result-1"] = "agx-screening-result-1"
    run_id: Nonempty
    task_id: Nonempty
    hypothesis_id: Nonempty
    candidate_id: Nonempty
    structure_id: Nonempty
    structure_artifact: ArtifactRef
    composition: dict[Nonempty, Annotated[int, Field(gt=0)]]
    predicted_properties: list[Property] = Field(min_length=1)
    model_name: Nonempty
    model_version: Nonempty
    model_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    uncertainty: Literal["UNCALIBRATED", "HIGH"] = "UNCALIBRATED"
    ood_status: Literal["NOT_ASSESSED", "OUT_OF_DOMAIN"] = "NOT_ASSESSED"
    dft_verification_status: Literal["NOT_RUN"] = "NOT_RUN"
    gate_status: Literal["PENDING_GATE"] = "PENDING_GATE"
    provenance: Provenance
    evidence_refs: list[Nonempty] = Field(min_length=1)

    @model_validator(mode="after")
    def identity(self):
        if self.provenance.run_id != self.run_id or not self.composition:
            raise ValueError("Screening identity or composition missing")
        return self


class ExperimentalFeedback(Contract):
    schema_version: Literal["agx-experimental-feedback-1"] = "agx-experimental-feedback-1"
    run_id: Nonempty
    feedback_id: Nonempty
    candidate_id: Nonempty
    hypothesis_id: Nonempty
    sample_id: Nonempty
    measurement_ids: list[Nonempty] = Field(min_length=1)
    experimental_findings: dict
    qc_status: Literal["PASS", "INCOMPLETE", "FAIL"]
    prediction_deviation: dict
    conclusion: Nonempty
    review_status: Literal["WAITING_FOR_REVIEW"] = "WAITING_FOR_REVIEW"
    recommended_actions: list[dict] = Field(min_length=1)
    provenance: Provenance
    official_publish: Literal[False] = False
    gate_status: Literal["PENDING_GATE"] = "PENDING_GATE"

    @model_validator(mode="after")
    def identity(self):
        if self.provenance.run_id != self.run_id:
            raise ValueError("Feedback run identity mismatch")
        if self.prediction_deviation.get("numeric_delta") is not None:
            raise ValueError("Energy and experimental current are not comparable numeric labels")
        return self


class RunRequest(Contract):
    schema_version: Literal["agx-run-request-1"] = "agx-run-request-1"
    run_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
    mode: Literal["LIVE", "SYNTHETIC"] = "SYNTHETIC"
    scenario: Literal["baseline", "disagreement", "mismatch", "missing", "counter"] = "baseline"
    model: Literal["UMA", "MACE-MH-1", "DPA4"] = "UMA"
    seed: int = 42
    dft_budget_jobs: Annotated[int, Field(ge=0)] = 0
    disagreement_threshold_eV: Annotated[float, Field(gt=0)] = 0.2
    include_cu: bool = True
