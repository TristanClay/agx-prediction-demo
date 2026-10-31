"""Small deterministic workflow; LLM planning cannot bypass these tools."""

from pathlib import Path

import numpy as np

from pipeline2.active_learning import sample
from pipeline2.contracts import RunConfig
from pipeline2.dft import VASPAdapter
from pipeline2.models import MockModelAdapter, PredictionResult
from pipeline2.properties import adsorption_energy, doping_energy, segregation_energy
from pipeline2.properties.screening import screen
from pipeline2.registry import DataRegistry, content_hash, sha256_file
from pipeline2.reliability import assess
from pipeline2.reports import finish_report, software_hashes, write_json
from pipeline2.safety import new_directory
from pipeline2.structure import (
    InvalidStructure,
    ag111_template,
    ff_demo,
    geometry_fingerprint,
    place_demo_adsorbate,
    read_structure,
    substitute,
    validate_structure,
    write_structure,
)

from .budget import BudgetGuard
from .gate import GateValidator


def run_demo(
    config: RunConfig, config_path: Path, output_dir: Path, *, command: str = "demo"
) -> dict:
    directory = new_directory(output_dir)
    registry = DataRegistry(directory / "registry.sqlite")
    gate = GateValidator().evaluate(config.task, config.gate_source)
    task = config.task
    source_hashes = {
        **software_hashes(),
        str(config_path.resolve()): sha256_file(config_path),
        **gate.source_hashes,
    }
    registry.append("Task", task.task_id, task.model_dump(), "fixture")
    registry.append("Decision", "gate", gate.to_dict(), "predicted")
    budget = BudgetGuard(registry, task.task_id, task.dft_budget_jobs, task.dft_budget_core_hours)
    dft = VASPAdapter(config, budget)
    predictions: list[dict] = []
    candidates: list[dict] = []
    property_rows: list[dict] = []
    proposals: list[dict] = []
    screening: dict = {"ranked": [], "top_k": [], "rejected": []}
    acquisition: dict = {
        "selected": [],
        "proposed_jobs": 0,
        "proposed_core_hours": 0.0,
        "actual_core_hours": 0.0,
    }
    seen = {}
    fingerprints = set()
    error, status = None, "COMPLETED_OFFLINE"

    def save(record):
        if record.structure_id in seen:
            return
        validate_structure(
            record.atoms,
            supported_elements=config.structure.supported_elements,
            min_distance_A=config.structure.min_distance_A,
            require_slab=bool(any(record.atoms.pbc)),
        )
        fingerprint = geometry_fingerprint(record.atoms)
        if fingerprint in fingerprints:
            raise InvalidStructure("Duplicate geometry with a different structure identity")
        payload = write_structure(record, directory / "structures")
        registry.append("Structure", record.structure_id, payload, payload["source_type"])
        seen[record.structure_id] = record
        fingerprints.add(fingerprint)
        if record.metadata.get("source_path"):
            source_hashes[record.metadata["source_path"]] = record.metadata["source_sha256"]

    try:
        if task.substrate != "Ag" or (not config.structure.input_path and task.surface != "111"):
            raise InvalidStructure(
                "Built-in demo is Ag(111); other surfaces require an explicit structure"
            )
        if task.adsorbates != ["FF"] or (
            not config.structure.input_path and abs(task.coverage - 1 / 9) > 1e-8
        ):
            raise InvalidStructure(
                "Demo supports one neutral FF per 3x3 surface cell; provide defined inputs for other tasks"
            )
        if task.model_candidates != ["mock-v1"]:
            raise InvalidStructure(
                "Offline demo requires model_candidates=[mock-v1]; inject real calculators through Python API"
            )
        supported_targets = {
            "adsorption_energy",
            "furfural_adsorption_energy",
            "doping_energy",
            "segregation_energy",
        }
        if not set(task.prediction_targets) <= supported_targets:
            raise InvalidStructure(
                "Requested target is outside the implemented property interfaces"
            )
        parent = (
            read_structure(config.structure.input_path, fixed=config.structure.fixed_indices)
            if config.structure.input_path
            else ag111_template()
        )
        molecule = (
            read_structure(config.structure.adsorbate_path, role="molecule")
            if config.structure.adsorbate_path
            else ff_demo()
        )
        save(parent)
        save(molecule)
        if task.dopants:
            if not task.dopant_sites:
                raise InvalidStructure("Specify atom indices for substitution")
            slabs = [substitute(parent, task.dopants[0], i) for i in task.dopant_sites]
        else:
            slabs = [parent]
        model = MockModelAdapter(config.mock_seed, task.reference_profile_id)
        model.comparison_context_id = content_hash(
            {
                "surface": task.surface,
                "coverage": task.coverage,
                "reference_profile_id": task.reference_profile_id,
            }
        )
        registry.append("ModelVersion", model.model_id, model.capabilities(), "mock")
        cached_predictions: dict[str, PredictionResult] = {}

        def predict(record, role):
            save(record)
            if record.structure_id in cached_predictions:
                return cached_predictions[record.structure_id]
            result = model.predict(record.atoms, task="energy_forces")
            payload = {
                **result.model_dump(),
                "role": role,
                **assess(
                    result.prediction_status,
                    gate_status=gate.status,
                    source_type=result.source_type,
                ),
            }
            predictions.append(payload)
            registry.append(
                "Prediction",
                "P_" + content_hash({"sid": record.structure_id, "model_hash": model.model_hash}),
                payload,
                "mock",
            )
            if result.prediction_status != "SUCCESS":
                raise InvalidStructure(result.error_message or "Prediction rejected")
            cached_predictions[record.structure_id] = result
            return result

        mol_pred = predict(molecule, "isolated_neutral_FF")
        for slab in slabs:
            slab_pred = predict(slab, "clean_slab_reference")
            if config.structure.adsorbed_paths:
                if len(slabs) != 1:
                    raise InvalidStructure(
                        "Existing adsorption geometries require exactly one clean slab reference"
                    )
                combined_records = [
                    read_structure(p, fixed=config.structure.fixed_indices)
                    for p in config.structure.adsorbed_paths
                ]
            else:
                combined_records = [
                    place_demo_adsorbate(slab, molecule, height)
                    for height in config.structure.heights_A
                ]
            for combined in combined_records:
                if any(c["candidate_id"] == combined.structure_id for c in candidates):
                    registry.append(
                        "Event",
                        f"dedup:{combined.structure_id}",
                        {"reason": "duplicate_structure_skipped"},
                        "fixture",
                    )
                    continue
                predicted = predict(combined, "slab_plus_FF")
                prop = adsorption_energy(predicted, slab_pred, mol_pred)
                property_rows.append({"candidate_id": combined.structure_id, **prop.to_dict()})
                for name, fn in (
                    ("doping_energy", doping_energy),
                    ("segregation_energy", segregation_energy),
                ):
                    if name in task.prediction_targets:
                        property_rows.append(
                            {"candidate_id": combined.structure_id, **fn().to_dict()}
                        )
                candidate = {
                    "candidate_id": combined.structure_id,
                    "properties": {"adsorption_energy_eV": prop.value},
                    "status": prop.status,
                    "gate_status": gate.status,
                    "calibration_status": "UNCALIBRATED",
                    "source_type": prop.source_type,
                    "comparison_key": {
                        "model_hash": model.model_hash,
                        "profile": task.reference_profile_id,
                        "context": model.comparison_context_id,
                    },
                    "features": [
                        float(slab.metadata.get("dopant_site", -1)),
                        float(combined.metadata.get("height_A", 0)),
                    ],
                    "potential": -prop.value if prop.value is not None else None,
                    "uncertainty": None,
                    "diversity": None,
                    "estimated_cost": dft.estimate_cost()["estimated_core_hours"],
                    "split": "exploration",
                }
                candidates.append(candidate)
        screening = screen(candidates, config.screening)
        ranked = screening["ranked"]
        if ranked:
            features = np.array([c["features"] for c in ranked])
            std = np.std(features, axis=0)
            normalized = (features - features.mean(axis=0)) / np.where(std > 0, std, 1)
            primary = config.screening.objectives[0]
            for i, candidate in enumerate(ranked):
                value = candidate["properties"].get(primary.property)
                candidate["potential"] = (
                    value * (1 if primary.direction == "max" else -1) if value is not None else None
                )
                candidate["diversity"] = min(
                    (
                        float(np.linalg.norm(normalized[i] - normalized[j]))
                        for j in range(len(ranked))
                        if j != i
                    ),
                    default=0.0,
                )
                candidate["diversity_definition"] = (
                    "nearest-neighbor distance in standardized demo [site_index,height_A] features"
                )
        acquisition = sample(
            screening["ranked"],
            config.acquisition,
            remaining_jobs=task.dft_budget_jobs,
            remaining_core_hours=task.dft_budget_core_hours,
        )
        # Scientific suggestions survive a zero computational budget, explicitly blocked.
        selected_ids = {c["candidate_id"] for c in acquisition["selected"]}
        proposal_candidates = {c["candidate_id"]: c for c in screening["top_k"]}
        proposal_candidates.update({c["candidate_id"]: c for c in acquisition["selected"]})
        for candidate in proposal_candidates.values():
            proposal = dft.propose(candidate["candidate_id"])
            if proposal["status"] == "PROPOSED" and candidate["candidate_id"] not in selected_ids:
                proposal["status"] = "NOT_SELECTED"
                proposal["selection_reason"] = "not selected within this round's proposal budget"
            proposals.append(proposal)
            registry.append("DFTJob", proposal["job_id"], proposal, "predicted")
        registry.append("Event", "active-learning-round-1", acquisition, "mock")
    except (InvalidStructure, ValueError, OSError) as exc:
        error, status = str(exc), "INVALID_OUT_OF_SCOPE"
        registry.append("Event", "failure", {"status": status, "reason": error}, "fixture")
    write_json(directory / "properties.json", property_rows)
    write_json(directory / "screening.json", screening)
    write_json(directory / "active_learning.json", acquisition)
    write_json(directory / "dft_proposals.json", proposals)
    write_json(directory / "budget.json", budget.snapshot())
    return finish_report(
        directory,
        command=command,
        status=status,
        gate=gate.to_dict(),
        predictions=predictions,
        screening=screening,
        source_hashes=source_hashes,
        config_payload=config.model_dump(),
        error=error,
        details={
            "structures": len(seen),
            "predictions": len(predictions),
            "ranked_candidates": len(screening["ranked"]),
        },
    )
