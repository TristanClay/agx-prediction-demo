"""Verified read-only bundles and isolated V1.1 writers."""

import os
from dataclasses import dataclass
from pathlib import Path

from agx_integration.contracts import (
    ExperimentalFeedback,
    KnowledgeTask,
    RunRequest,
    ScreeningResult,
)
from agx_integration.io import digest, files, load_replay, read, verify, write
from agx_integration.workflow import execute
from pipeline2.safety import writable_path

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ("baseline", "disagreement", "missing", "mismatch", "counter")
DEFAULT_RUNS = ROOT / "runs/integration_v1_1"
DEFAULT_WORKSPACE = ROOT / "runs/integration_workspace_v1_1"
LEGACY_RUNS = ROOT / "runs/integration"


def output_path(path, pipeline3_root=None):
    """Refuse overlap in either direction, including symlink aliases."""
    target = writable_path(path)
    if target.is_relative_to(ROOT) and not any(
        target == base or target.is_relative_to(base)
        for base in (DEFAULT_RUNS, DEFAULT_WORKSPACE)
    ):
        raise ValueError("Project-local V1.1 writes must use its dedicated runs/workspace trees")
    protected = [
        LEGACY_RUNS,
        ROOT / "runs/integration_workspace",
        ROOT / "agx_integration",
        ROOT / "pipeline2",
        ROOT / "apps",
        ROOT / "tools",
        ROOT / "tests",
        ROOT / "configs",
        ROOT / "integration",
        ROOT / "presentation",
        ROOT / "docs",
        ROOT.parent / "PipeLine3",
    ]
    if pipeline3_root:
        protected.append(Path(pipeline3_root))
    for path in protected:
        source = path.resolve()
        if target == source or target.is_relative_to(source) or source.is_relative_to(target):
            raise ValueError(f"V1.1 output overlaps protected V1.0/source: {target}")
    return target


@dataclass
class Bundle:
    root: Path
    result: dict
    request: dict
    manifest: dict

    def artifact(self, relative):
        relative = Path(relative)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Artifact path must stay inside its run")
        key = relative.as_posix()
        refs = [r for r in self.manifest["artifacts"] if r["path"] == key]
        if len(refs) != 1:
            raise ValueError(f"Artifact is not declared by this run: {key}")
        verify(self.root, refs)
        return self.root / relative

    def node_file(self, node, relative):
        directory = self.result.get("node_directories", {}).get(node)
        if directory is None:
            raise ValueError(f"Node has no committed artifacts: {node}")
        return self.artifact(Path(directory) / relative)

    def json(self, node, relative):
        return read(self.node_file(node, relative))


def validate_result(result):
    """UI joins must preserve the original run, task and hypothesis identity."""
    run_id, outputs = result["run_id"], result["outputs"]
    task = KnowledgeTask.model_validate(outputs["knowledge"]) if "knowledge" in outputs else None
    if task and task.run_id != run_id:
        raise ValueError("Knowledge belongs to another run")
    candidates = outputs.get("screening", {}).get("results", [])
    for payload in candidates:
        candidate = ScreeningResult.model_validate(payload)
        if not task or (candidate.run_id, candidate.task_id, candidate.hypothesis_id) != (
            run_id,
            task.task_id,
            task.hypothesis_id,
        ):
            raise ValueError("Cross-run/task/hypothesis candidate join rejected")
    if "feedback" in outputs:
        feedback = ExperimentalFeedback.model_validate(outputs["feedback"]["feedback"])
        if not task or feedback.run_id != run_id or feedback.hypothesis_id != task.hypothesis_id:
            raise ValueError("Cross-run feedback rejected")
        if feedback.candidate_id not in {c["candidate_id"] for c in candidates}:
            raise ValueError("Feedback candidate is not in this run")
    for event in result["events"]:
        if event.get("input", {}).get("run_id", run_id) != run_id:
            raise ValueError("Event belongs to another run")


def open_bundle(path):
    root = Path(path).expanduser().resolve()
    result = load_replay(root)  # Always reverify; no stale result cache.
    request = read(root / "request.json")["request"]
    if request["run_id"] != result["run_id"]:
        raise ValueError("Request and result run IDs differ")
    validate_result(result)
    return Bundle(root, result, request, read(root / "manifest.json"))


def run_new(request: RunRequest, runs_root=None, pipeline3_root=None):
    root = output_path(
        runs_root or os.environ.get("AGX_V1_1_RUNS_ROOT", DEFAULT_RUNS), pipeline3_root
    )
    result = execute(request, root, pipeline3_root=pipeline3_root)
    directory = root / request.run_id
    if (directory / "manifest.json").exists():
        return open_bundle(directory)
    # Failed / blocked attempts have no sealed manifest. Their snapshots stay explicit.
    validate_result(result)
    return Bundle(directory, result, request.model_dump(), {"artifacts": files(directory)})


def scenario_paths(scenario, runs_root=None, replay_root=None):
    if scenario not in SCENARIOS:
        raise ValueError("Unknown scenario")
    legacy = Path(replay_root or os.environ.get("AGX_V1_1_REPLAY_ROOT", LEGACY_RUNS))
    newer = Path(runs_root or os.environ.get("AGX_V1_1_RUNS_ROOT", DEFAULT_RUNS))
    paths = []
    for base in dict.fromkeys([legacy, newer]):
        if not base.exists():
            continue
        for manifest in sorted(base.glob("*/manifest.json")):
            try:
                if read(manifest.parent / "request.json")["request"]["scenario"] == scenario:
                    paths.append(manifest.parent.resolve())
            except (ValueError, OSError, KeyError):
                continue
    return paths


def save_draft(bundle, action, workspace=None):
    """A new sidecar proposal, never a modification of the sealed source run."""
    from .view import next_task_drafts

    drafts = next_task_drafts(bundle)
    if action not in drafts:
        raise ValueError("Draft is not derived from this run's saved recommendations")
    root = output_path(workspace or os.environ.get("AGX_V1_1_WORKSPACE_ROOT", DEFAULT_WORKSPACE))
    payload = {**action, "source_manifest_sha256": digest(bundle.manifest)}
    target = root / "drafts" / f"{action['draft_id']}.json"
    write(target, payload)
    return target
