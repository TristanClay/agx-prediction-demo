from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ExecutionStatus = Literal["DRAFT", "PLANNED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]
DataStatus = Literal["UNREVIEWED", "NEEDS_INFO", "VALID", "INVALID", "PARTIAL"]
HypothesisOutcome = Literal["SUPPORTED", "REFUTED", "INCONCLUSIVE", "MIXED"]
FeedbackStatus = Literal["STAGED", "REVIEWED", "ACCEPTED", "RETURNED"]
ENTITIES = {
    "Task", "CandidateLink", "ExperimentPlan", "Protocol", "Sample", "Batch",
    "SampleState", "TreatmentEvent", "Measurement", "Calibration", "RawArtifact",
    "AnalysisRun", "Recommendation", "ReviewEvent", "FeedbackPackage",
}


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str
    kind: str
    namespace: str
    source_type: Literal["synthetic", "experimental", "upstream_snapshot", "replay"]
    validation_status: str = "UNREVIEWED"
    execution_status: ExecutionStatus = "DRAFT"
    data_status: DataStatus = "UNREVIEWED"
    hypothesis_outcome: HypothesisOutcome | None = None
    feedback_status: FeedbackStatus | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class AdapterResult(BaseModel):
    schema_version: str = "pipeline3-adapter-1"
    records: list[dict] = Field(default_factory=list)
    source_refs: list[dict] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    gate_status: str = "unresolved"
    integration_status: str = "LOCAL_FILE_MAPPING_ONLY"
