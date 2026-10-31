"""Evidence-bound Ni/Bi hackathon data. No autonomous LLM or real DFT execution."""

import json
import platform
from pathlib import Path
from time import perf_counter

from ase import Atom
from ase.build import add_adsorbate
from ase.data import chemical_symbols

from pipeline2.contracts import RunConfig
from pipeline2.dft import VASPAdapter
from pipeline2.models import MockModelAdapter
from pipeline2.orchestrator import BudgetGuard
from pipeline2.orchestrator.gate import GateValidator
from pipeline2.properties import adsorption_energy
from pipeline2.registry import DataRegistry, content_hash, sha256_file, utc_now
from pipeline2.reports import software_hashes, write_csv, write_json
from pipeline2.safety import new_directory, protect_sources
from pipeline2.showcase import HEIGHTS, algorithm_demo, demo_config
from pipeline2.structure import (
    InvalidStructure,
    StructureRecord,
    ag111_template,
    ff_demo,
    fixed_indices,
    place_demo_adsorbate,
    substitute,
    validate_structure,
    write_structure,
)

FAMILIES = {"Ni": "CF_AG_NI_OH_AFFINITY_V1", "Bi": "CF_AG_BI_POISONING_V1"}
MODELS = {"Ni": "SM_AG_NI_SURFACE_SUB_V1", "Bi": "SM_AG_BI_SURFACE_DECORATION_V1"}


def import_cards(path: Path) -> list[dict]:
    """Read snapshot as context, never as a gate attestation or approved result."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Expected Pipeline1 candidate_cards.json list")
    result = []
    for element, family in FAMILIES.items():
        rows = [c for c in data if c.get("candidate_family_id") == family]
        if len(rows) != 1:
            raise ValueError(f"Missing or duplicate Pipeline1 family: {family}")
        card = rows[0]
        if card.get("element") != element or not any(
            m.get("structure_model_id") == MODELS[element] for m in card.get("structure_models", [])
        ):
            raise ValueError("Pipeline1 identity / allowed model mismatch")
        result.append(card)
    return result


def decorate_bi(parent: StructureRecord, site: str, height_A: float = 2.5) -> StructureRecord:
    if site not in {"fcc", "hcp"}:
        raise InvalidStructure("Only explicit fcc/hcp hollow demo sites supported")
    atoms = parent.atoms.copy()
    # ASE supplies the deterministic terrace-hollow coordinates from its fcc111 template.
    add_adsorbate(atoms, Atom("Bi"), height_A, position=site)
    atoms.info.update(charge=0, source_type="fixture")
    validate_structure(atoms, expected_fixed=fixed_indices(parent.atoms))
    return StructureRecord(
        atoms,
        {
            "source_type": "fixture",
            "origin": "unrelaxed isolated Bi adatom demonstration",
            "parent_structure_id": parent.structure_id,
            "dopant": "Bi",
            "site": site,
            "incorporation_mode": "submonolayer_surface_decoration",
            "Bi_coverage_ML": 1 / 9,
            "initial_Bi_height_above_Ag_A": height_A,
            "Bi_position_A": atoms[-1].position.tolist(),
            "geometry_relaxed": False,
        },
    )


def create_hackathon(source: str | Path, output_dir: str | Path, *, seed: int = 42) -> dict:
    input_started = perf_counter()
    path = Path(source).resolve(strict=True)
    if path.is_dir():
        path = path / "candidate_cards.json"
    # Import and validate before creating any output directory.
    cards = import_cards(path)
    source_hash = sha256_file(path)
    input_elapsed = perf_counter() - input_started
    with protect_sources((path.parent,)):
        return _generate(path, source_hash, cards, output_dir, seed, input_elapsed)


def _generate(
    path: Path, source_hash: str, cards: list[dict], output_dir, seed: int, input_elapsed: float
) -> dict:
    started = perf_counter() - input_elapsed
    directory = new_directory(output_dir)
    code_hashes = software_hashes()
    registry = DataRegistry(directory / "registry.sqlite")
    events: list[dict] = []

    def call(stage, operation, inputs, function, summarize):
        tick = perf_counter()
        value = function()
        event = {
            "event_id": f"E{len(events) + 1:03d}",
            "stage": stage,
            "operation": operation,
            "inputs": inputs,
            "outputs": summarize(value),
            "status": "RETURNED",
            "elapsed_seconds": perf_counter() - tick,
            "recorded_at": utc_now(),
            "implementation": "deterministic_tool" if stage != "model" else "mock_model",
        }
        events.append(event)
        registry.append("Event", event["event_id"], event, "fixture")
        return value

    write_json(directory / "pipeline1_candidate_cards.json", cards)
    source_record = {
        "path": str(path),
        "sha256": source_hash,
        "imported_family_ids": list(FAMILIES.values()),
        "use": "read_only_hypothesis_context_not_gate_attestation",
        "formal_release": False,
    }
    write_json(directory / "pipeline1_source.json", source_record)
    events.append(
        {
            "event_id": "E000",
            "stage": "input",
            "operation": "import_cards",
            "inputs": source_record,
            "outputs": {"families": list(FAMILIES.values())},
            "status": "RETURNED",
            "elapsed_seconds": input_elapsed,
            "recorded_at": utc_now(),
            "implementation": "deterministic_tool",
        }
    )
    configs: dict[str, RunConfig] = {}
    gates, adapters = {}, {}
    for name, element in [("Ag", None), ("Ni", "Ni"), ("Bi", "Bi")]:
        payload = demo_config("ag", seed).model_dump()
        payload["task"].update(task_id=f"hackathon-{name}", dopants=[element] if element else [])
        if element:
            payload["task"]["hypothesis_id"] = FAMILIES[element]
            payload["task"]["evidence_refs"] = [f"pipeline1_snapshot_sha256:{source_hash}"]
        config = call(
            "planning",
            "RunConfig.model_validate",
            payload,
            lambda payload=payload: RunConfig.model_validate(payload),
            lambda r: r.model_dump(),
        )
        configs[name] = config
        registry.append("Task", config.task.task_id, config.task.model_dump(), "fixture")
        write_json(directory / f"{name}_config.json", config.model_dump())
        gate = call(
            "gate",
            "GateValidator.evaluate",
            {"task": config.task.model_dump(), "gate_source": None},
            lambda: GateValidator().evaluate(config.task, None),
            lambda r: r.to_dict(),
        )
        gates[name] = gate.to_dict()
        budget = BudgetGuard(registry, config.task.task_id, 0, 0.0)
        adapters[name] = VASPAdapter(config, budget)
    parent = call(
        "structure",
        "ag111_template",
        {},
        ag111_template,
        lambda r: {"structure_id": r.structure_id},
    )
    molecule = ff_demo()
    variants = [("Ag", "Ag 基线", parent)]
    for site, label in [(27, "表面替位"), (18, "次表面替位")]:
        r = call(
            "structure",
            "substitute",
            {"parent": parent.structure_id, "element": "Ni", "index": site},
            lambda site=site: substitute(parent, "Ni", site),
            lambda r: {"structure_id": r.structure_id},
        )
        r.metadata.update(candidate_family_id=FAMILIES["Ni"], structure_model_id=MODELS["Ni"])
        variants.append(("Ni", label, r))
    for hollow_site in ["fcc", "hcp"]:
        r = call(
            "structure",
            "decorate_bi",
            {"parent": parent.structure_id, "site": hollow_site, "Bi_height_A": 2.5},
            lambda site=hollow_site: decorate_bi(parent, site),
            lambda r: r.to_dict(),
        )
        r.metadata.update(candidate_family_id=FAMILIES["Bi"], structure_model_id=MODELS["Bi"])
        variants.append(("Bi", f"{hollow_site} hollow 表面修饰", r))
    model = MockModelAdapter(seed)
    model.comparison_context_id = "hackathon-neutral-FF-same-cell-v1"
    registry.append("ModelVersion", model.model_id, model.capabilities(), "mock")
    records, predictions = {}, {}

    def save_predict(record):
        sid = record.structure_id
        if sid not in records:
            validate_structure(record.atoms, require_slab=bool(any(record.atoms.pbc)))
            records[sid] = write_structure(record, directory / "structures")
            registry.append("Structure", sid, records[sid], "fixture")
            prediction = call(
                "model",
                "MockModelAdapter.predict",
                {"structure_id": sid, "model_hash": model.model_hash},
                lambda: model.predict(record.atoms, task="energy_forces"),
                lambda r: r.model_dump(),
            )
            if prediction.prediction_status != "SUCCESS":
                raise InvalidStructure(prediction.error_message or "Mock prediction rejected")
            predictions[sid] = prediction
            registry.append("Prediction", "P_" + sid, prediction.model_dump(), "mock")
        return predictions[sid]

    isolated = save_predict(molecule)
    rows, atom_rows, props, proposals = [], [], [], []
    for material, layer, slab in variants:
        reference = save_predict(slab)
        for height in HEIGHTS:
            combined = call(
                "structure",
                "place_demo_adsorbate",
                {"slab_id": slab.structure_id, "height_A": height},
                lambda slab=slab, height=height: place_demo_adsorbate(slab, molecule, height),
                lambda r: {"structure_id": r.structure_id},
            )
            combined.metadata.update(
                material=material,
                layer=layer,
                height_reference="highest_slab_atom_plane",
                candidate_family_id=FAMILIES.get(material, "Ag_control"),
                structure_model_id=MODELS.get(material, "Ag_111_control"),
            )
            prediction = save_predict(combined)
            prop = call(
                "properties",
                "adsorption_energy",
                {
                    "combined": combined.structure_id,
                    "slab": slab.structure_id,
                    "adsorbate": molecule.structure_id,
                },
                lambda: adsorption_energy(prediction, reference, isolated),
                lambda r: r.to_dict(),
            )
            props.append({"candidate_id": combined.structure_id, **prop.to_dict()})
            payload = records[combined.structure_id]
            rows.append(
                {
                    "candidate_id": combined.structure_id,
                    "label": f"Ag–{material} / {layer} / {height:.1f} Å"
                    if material != "Ag"
                    else f"Ag / {height:.1f} Å",
                    "material": "Ag" if material == "Ag" else f"Ag–{material}",
                    "element": material,
                    "layer": layer,
                    "height_A": height,
                    "height_reference": "highest_slab_atom_plane",
                    "adsorption_energy_eV": prop.value,
                    "source_type": "mock",
                    "status": prop.status,
                    "gate_status": "PENDING_GATE",
                    "calibration_status": "UNCALIBRATED",
                    "candidate_family_id": FAMILIES.get(material, "Ag_control"),
                    "structure_model_id": MODELS.get(material, "Ag_111_control"),
                    "model_id": prediction.model_id,
                    "model_hash": prediction.model_hash,
                    "model_branch": prediction.model_branch,
                    "reference_profile_id": prediction.reference_profile_id,
                    "structure_hash": payload["structure_hash"],
                    "reference_structure_ids": prop.reference_structure_ids,
                    "artifact_refs": [
                        f"structures/{combined.structure_id}.json",
                        "predictions.csv",
                        "properties.json",
                    ],
                }
            )
            fixed = set(fixed_indices(combined.atoms))
            for i, atom in enumerate(combined.atoms):
                atom_rows.append(
                    {
                        "candidate_id": combined.structure_id,
                        "atom_index": i,
                        "element": chemical_symbols[atom.number],
                        "x_A": float(atom.x),
                        "y_A": float(atom.y),
                        "z_A": float(atom.z),
                        "fixed": i in fixed,
                        "source_type": "fixture",
                    }
                )
            proposal = call(
                "gate",
                "VASPAdapter.propose",
                {"candidate_id": combined.structure_id},
                lambda: adapters[material].propose(combined.structure_id),
                lambda r: r,
            )
            proposals.append(proposal)
            registry.append("DFTJob", proposal["job_id"], proposal, "predicted")
    algorithms = call(
        "sampling",
        "algorithm_demo",
        {"seed": seed, "source_type": "fixture"},
        lambda: algorithm_demo(seed),
        lambda r: {"screening": r["screening"], "rounds": r["rounds"]},
    )
    invalid = parent.atoms.copy()
    invalid.positions[1] = invalid.positions[0]

    def collision():
        try:
            validate_structure(invalid)
        except InvalidStructure as exc:
            return {
                "status": exc.status,
                "reason": str(exc),
                "energy_eV": model.predict(invalid, task="energy_forces").energy_eV,
            }
        raise AssertionError("Collision accepted")

    collision_result = call(
        "guardrail",
        "validate_structure",
        {"perturbation": "duplicate_atom"},
        collision,
        lambda r: r,
    )
    first = props[0]["reference_structure_ids"]
    charged = call(
        "guardrail",
        "adsorption_energy",
        {"perturbation": "isolated_reference_charge_minus_one"},
        lambda: adsorption_energy(
            predictions[first[0]], predictions[first[1]], isolated.model_copy(update={"charge": -1})
        ),
        lambda r: r.to_dict(),
    )
    check = call(
        "guardrail",
        "validate_gate_and_budget",
        {"formal_budget_jobs": 0},
        adapters["Ag"].validate_gate_and_budget,
        lambda r: r,
    )
    guards = [
        {"case_id": "collision", "title": "原子碰撞", "source_type": "fixture", **collision_result},
        {
            "case_id": "charged_reference",
            "title": "带电参考不相容",
            "source_type": "fixture",
            "status": charged.status,
            "reason": charged.error_message,
            "value": charged.value,
        },
        {
            "case_id": "pending_gate",
            "title": "零预算与待门控",
            "source_type": "fixture",
            "status": check["gate"]["status"],
            "reason": "; ".join(check["blockers"]),
            "allowed": check["allowed"],
            "executed": False,
        },
    ]
    references = sorted(
        {sid for row in rows for sid in row["reference_structure_ids"]}
        - {row["candidate_id"] for row in rows}
    )
    assumptions = {
        "candidate_count": len(rows),
        "review_count": 3,
        "reference_count": len(references),
        "dft_hours": 1,
        "cores": 16,
        "concurrency": 1,
        "overhead_hours": 0.1,
        "queue_hours": 0,
        "retry_attempts": 0,
        "basis": "UNBENCHMARKED_SCENARIO_NOT_OBSERVED_SAVINGS",
        "equivalent_scientific_quality_verified": False,
    }
    recipe = {
        "seed": seed,
        "heights_A": HEIGHTS,
        "Ni_indices": [27, 18],
        "Bi_sites": ["fcc", "hcp"],
        "Bi_height_above_Ag_A": 2.5,
        "FF_height_reference": "highest atom of each decorated/substituted slab, not relaxed adsorption distance",
        "formal_budget_jobs": 0,
        "formal_budget_core_hours": 0,
        "dry_run": True,
        "allow_real_vasp_submit": False,
        "unimplemented_interfaces": ["Ag|Ni(OH)x_interface", "Ag|BiOx_interface"],
    }
    for name, value in [
        ("recipe.json", recipe),
        ("scenario_assumptions.json", assumptions),
        ("properties.json", props),
        ("guardrail_cases.json", guards),
        ("dft_proposals.json", proposals),
        ("algorithm_fixture.json", algorithms),
    ]:
        write_json(directory / name, value)
    write_csv(directory / "predictions.csv", [p.model_dump() for p in predictions.values()])
    write_csv(directory / "candidates.csv", rows)
    call(
        "registry",
        "DataRegistry.records_summary",
        {"database": "registry.sqlite"},
        lambda: {
            name: len(registry.records(name))
            for name in ["Task", "Structure", "Prediction", "DFTJob"]
        },
        lambda r: r,
    )
    elapsed = perf_counter() - started
    timing = {
        "elapsed_seconds": elapsed,
        "recorded_at": utc_now(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.machine(),
        "measured_scope": "data generation before final bundle/manifest serialization; includes mock CPU calculations, excludes Node build/browser/PPT",
        "stage_seconds": {
            stage: sum(e["elapsed_seconds"] or 0 for e in events if e["stage"] == stage)
            for stage in {e["stage"] for e in events}
        },
    }
    write_json(directory / "timing.json", timing)
    (directory / "events.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False, allow_nan=False) + "\n" for e in events),
        encoding="utf-8",
    )
    semantic = {
        "recipe": recipe,
        "structure_candidates": rows,
        "atom_rows": atom_rows,
        "algorithms": algorithms,
        "guardrail_cases": guards,
        "pipeline1_cards": cards,
        "pipeline1_source": source_record,
        "reference_structure_ids": references,
        "scenario_assumptions": assumptions,
    }
    artifacts = [
        {"artifact_ref": str(p.relative_to(directory)), "sha256": sha256_file(p)}
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    ]
    bundle = {
        "schema_version": "pipeline2-hackathon-1",
        "created_at": utc_now(),
        "status": "COMPLETED_OFFLINE",
        "gate_status": "PENDING_GATE",
        "actual_dft_jobs": 0,
        "actual_dft_core_hours": 0,
        "semantic_sha256": content_hash(semantic),
        **semantic,
        "events": events,
        "timing": timing,
        "gates": gates,
        "source_artifacts": artifacts,
    }
    write_json(directory / "hackathon.json", bundle)
    if sha256_file(path) != source_hash or any(sha256_file(p) != h for p, h in code_hashes.items()):
        raise RuntimeError("Source changed during generation")
    manifest = {
        "schema_version": "pipeline2-hackathon-manifest-1",
        "status": "COMPLETED_OFFLINE",
        "gate_status": "PENDING_GATE",
        "semantic_sha256": bundle["semantic_sha256"],
        "source_hashes": {str(path): source_hash, **code_hashes},
        "actual_dft_jobs": 0,
        "actual_dft_core_hours": 0,
        "artifacts": {
            str(p.relative_to(directory)): sha256_file(p)
            for p in sorted(directory.rglob("*"))
            if p.is_file()
        },
    }
    write_json(directory / "hackathon_manifest.json", manifest)
    return manifest
