"""Teaching inputs and scheduler events, deliberately incapable of execution."""

import json
from pathlib import Path

from ase.io import write

from pipeline2.dft.parser import parse_vasp
from pipeline2.registry import content_hash, sha256_file
from pipeline2.safety import new_directory, write_new
from pipeline2.structure import StructureRecord, validate_structure


class TeachingPBSAdapter:
    def prepare(self, *, cores: int = 16, walltime_hours: float = 1.0) -> dict:
        if isinstance(cores, bool) or not isinstance(cores, int) or cores <= 0:
            raise ValueError("Positive integer cores required")
        import math

        if not math.isfinite(walltime_hours) or walltime_hours <= 0:
            raise ValueError("Positive finite walltime required")
        seconds = int(walltime_hours * 3600)
        if seconds < 1:
            raise ValueError("Walltime below PBS second precision")
        hh, remainder = divmod(seconds, 3600)
        mm, ss = divmod(remainder, 60)
        script = (
            "#!/bin/sh\n# TEACHING ONLY: intentionally cannot execute VASP\n"
            f"#PBS -N agx_teaching\n#PBS -l select=1:ncpus={cores}\n"
            f"#PBS -l walltime={hh:02d}:{mm:02d}:{ss:02d}\n"
            "echo 'SYNTHETIC DEMO: real computation disabled'\nexit 99\n"
        )
        return {
            "status": "DRY_RUN_ONLY",
            "script": script,
            "estimated_core_hours": cores * seconds / 3600,
            "estimate_status": "UNBENCHMARKED_TEACHING_ASSUMPTION",
            "executed": False,
        }

    def submit(self, *args, **kwargs) -> dict:
        return {"status": "REAL_SUBMISSION_DISABLED", "executed": False}

    def status(self, *args, **kwargs) -> dict:
        return {"status": "NO_CLUSTER_QUERY", "executed": False}

    def cancel(self, *args, **kwargs) -> dict:
        return {"status": "NO_CLUSTER_CANCEL", "executed": False}


def prepare_teaching_job(record: StructureRecord, directory: Path, gate: dict) -> dict:
    validate_structure(record.atoms)
    target = new_directory(directory)
    symbols = sorted(set(record.atoms.get_chemical_symbols()))
    # POSCAR and POTCAR references use the same deterministic species order.
    atoms = record.atoms[sorted(range(len(record.atoms)), key=lambda i: record.atoms[i].symbol)]
    with (target / "POSCAR").open("x") as handle:
        write(handle, atoms, format="vasp", direct=True, vasp5=True)
    incar = (
        "# TEACHING ONLY; NOT A CONVERGED OR APPROVED SCIENTIFIC PROFILE\n"
        "SYSTEM = Ag-X synthetic workflow preview\nENCUT = 450\nEDIFF = 1E-5\n"
        "ISMEAR = 1\nSIGMA = 0.1\nNSW = 0\nIBRION = -1\n"
    )
    if "Ni" in symbols:
        incar += (
            "ISPIN = 2\nMAGMOM = "
            + " ".join("1.0" if atom.symbol == "Ni" else "0.0" for atom in atoms)
            + "\n"
        )
    write_new(target / "INCAR", incar)
    write_new(target / "KPOINTS", "Teaching grid; not convergence tested\n0\nGamma\n2 2 1\n0 0 0\n")
    potcar = {
        "status": "MISSING_LICENSED_POTCAR",
        "copied": False,
        "species_order": symbols,
        "references": [{"element": s, "path": None, "sha256": None} for s in symbols],
    }
    write_new(target / "POTCAR.references.json", json.dumps(potcar, indent=2) + "\n")
    pbs = TeachingPBSAdapter().prepare()
    write_new(target / "job.pbs.example", pbs["script"])
    manifest = {
        "schema_version": "pipeline2-teaching-dft-1",
        "data_origin": "SYNTHETIC",
        "structure_id": record.structure_id,
        "gate": gate,
        "status": "TEACHING_INPUTS_ONLY_NOT_RUNNABLE",
        "executed": False,
        "scientific_proposal_status": "PENDING_GATE" if gate["status"] != "RELEASED" else "BLOCKED",
        "blockers": list(gate["blockers"]) + ["formal_budget_zero", "no_potcar", "no_executor"],
        "species_order": symbols,
        "original_atom_order_preserved_in_structure_record": True,
        "vasp_target": "6.1.0 (not runtime verified)",
        "profile_status": "UNVALIDATED_TEACHING_PROFILE",
        "parameters": {"ENCUT": 450, "k_grid": [2, 2, 1], "NSW": 0},
        "calculation_type": "single_point_preview",
        "supported_future_tasks": [
            "baseline",
            "single_point",
            "relaxation",
            "adsorption_references",
        ],
        "pbs": pbs,
        "potcar": potcar,
        "artifacts": {p.name: sha256_file(p) for p in target.iterdir() if p.is_file()},
    }
    write_new(
        target / "job_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    return manifest


def scheduler_events(candidate_id: str, *, round_id: str, gate: dict) -> list[dict]:
    """A simulated completion can never release the scientific proposal."""
    return [
        {
            "event_id": content_hash({"candidate": candidate_id, "round": round_id, "step": i}),
            "candidate_id": candidate_id,
            "round_id": round_id,
            "step": i,
            "state": state,
            "data_origin": "SYNTHETIC",
            "executed": False,
            "scientific_gate_status": gate["status"],
            "label_status": "SYNTHETIC_TEACHING_LABEL" if i == 4 else None,
            "formal_eligible": False,
        }
        for i, state in enumerate(
            ("SIM_PREPARED", "SIM_QUEUED", "SIM_RUNNING", "SIM_FINISHED", "SIM_LABEL_REPLAYED")
        )
    ]


class DFTResultValidator:
    """Read-only bridge to the existing conservative parser, no implicit approval."""

    def validate(
        self,
        path: str | Path | None,
        *,
        reference_profile_id: str,
        source_type: str = "fixture",
        origin_is_fixture: bool = True,
    ) -> dict:
        if path is None:
            return {"status": "NO_REAL_DFT_PROVIDED", "label_eligible": False}
        parsed = parse_vasp(
            path,
            reference_profile_id=reference_profile_id,
            source_type=source_type,
            origin_is_fixture=origin_is_fixture,
        )
        return {
            "status": "PARSED_REQUIRES_FORMAL_REVIEW"
            if parsed["parse_status"] == "PARSED"
            else "INVALID_RESULT",
            "parsed": parsed,
            "label_eligible": False,
            "formal_validation": "NOT_IMPLEMENTED_IN_TEACHING_DEMO",
            "missing_checks": [
                "approved_theory_profile",
                "structure_reference_attestation",
                "human_review",
            ],
        }
