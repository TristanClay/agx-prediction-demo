"""Conservative VASP final-frame parsing. Parsed data are not human-validated labels."""

import re
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np

from pipeline2.registry import sha256_file


def _xml(path: Path) -> dict:
    root = ET.parse(path).getroot()
    if root.tag != "modeling":
        raise ValueError("Not a VASP modeling document")
    steps = root.findall("calculation")
    if not steps:
        raise ValueError("No calculation frames")
    final = steps[-1]

    def parameter(name):
        node = root.find(f".//parameters//i[@name='{name}']")
        return node.text.strip() if node is not None and node.text else None

    def energy(frame):
        node = frame.find("energy/i[@name='e_0_energy']")
        if node is None or not node.text:
            raise ValueError("Missing e_0_energy; free energy is not silently substituted")
        return float(node.text)

    ions = root.find("atominfo/atoms")
    if ions is None or not ions.text:
        raise ValueError("Missing atom count")
    forces = [
        [float(v) for v in (row.text or "").split()]
        for row in final.findall("varray[@name='forces']/v")
    ]
    if len(forces) != int(ions.text) or any(len(row) != 3 for row in forces):
        raise ValueError("Missing/incomplete final forces")
    nelm, nsw, ediffg = parameter("NELM"), parameter("NSW"), parameter("EDIFFG")
    scsteps = final.findall("scstep")
    electronic = (len(scsteps) < int(nelm)) if nelm is not None and scsteps else None
    ionic = None
    kind = "unknown"
    evidence = ["electronic convergence inferred only from complete XML SCF iteration count < NELM"]
    if nsw is not None and int(nsw) == 0:
        kind = "single_point"
    elif nsw is not None and int(nsw) > 0:
        kind = "relaxation"
        # Without fixed-coordinate metadata the raw max force is conservative.
        if ediffg is not None and float(ediffg) < 0:
            ionic = bool(np.max(np.linalg.norm(np.array(forces), axis=1)) <= -float(ediffg))
            evidence.append("ionic convergence checked against raw max force and negative EDIFFG")
        elif ediffg is not None and float(ediffg) > 0 and len(steps) >= 2:
            ionic = abs(energy(final) - energy(steps[-2])) < float(ediffg)
            evidence.append(
                "ionic convergence checked using final energy difference and positive EDIFFG"
            )
    return {
        "energy_eV": energy(final),
        "forces_eV_per_A": forces,
        "electronic_converged": electronic,
        "ionic_converged": ionic,
        "calculation_type": kind,
        "normal_termination": True,
        "convergence_evidence": evidence,
        "frame_count": len(steps),
    }


def _outcar(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    atom_counts = re.findall(r"NIONS\s*=\s*(\d+)", text)
    if not atom_counts:
        raise ValueError("NIONS missing")
    nions = int(atom_counts[-1])
    blocks = list(re.finditer(r"POSITION\s+TOTAL-FORCE\s*\(eV/Angst\)", text))
    if not blocks:
        raise ValueError("Final force block missing")
    start = blocks[-1].end()
    rows = []
    started = False
    for line in text[start:].splitlines():
        tokens = line.split()
        if len(tokens) == 6:
            try:
                values = [float(v) for v in tokens]
            except ValueError:
                if started:
                    break
                continue
            rows.append(values[3:])
            started = True
        elif started:
            break
    if len(rows) != nions:
        raise ValueError("Final force block truncated")
    tail = text[start:]
    # Match E0 (sigma -> 0), never TOTEN or energy without entropy as a fallback.
    energies = re.findall(r"energy\(sigma->0\)\s*=\s*([-+\d.Ee]+)", tail)
    if not energies:
        raise ValueError("Final energy(sigma->0) missing")
    nsw = re.findall(r"\bNSW\s*=\s*(\d+)", text)
    kind = "single_point" if nsw and int(nsw[-1]) == 0 else "relaxation" if nsw else "unknown"
    before_force = text[: blocks[-1].start()]
    previous_force_end = blocks[-2].end() if len(blocks) > 1 else 0
    electronic = (
        True
        if "aborting loop because EDIFF is reached" in before_force[previous_force_end:]
        else None
    )
    ionic = True if "reached required accuracy" in tail else None
    normal = "General timing and accounting informations for this job" in tail
    return {
        "energy_eV": float(energies[-1]),
        "forces_eV_per_A": rows,
        "electronic_converged": electronic,
        "ionic_converged": ionic,
        "normal_termination": normal,
        "calculation_type": kind,
        "convergence_evidence": ["OUTCAR EDIFF/required-accuracy/timing markers"],
        "frame_count": len(blocks),
    }


def parse_vasp(
    path: str | Path,
    *,
    reference_profile_id: str,
    source_type: str = "fixture",
    origin_is_fixture: bool = True,
) -> dict:
    source = Path(path).resolve()
    result = {
        "schema_version": "pipeline2-dft-label-1",
        "source_path": str(source),
        "reference_profile_id": reference_profile_id,
        "source_type": source_type,
        "origin_is_fixture": origin_is_fixture,
        "review_status": "unreviewed",
        "label_eligible": False,
        "parse_status": "INVALID_RESULT",
        "error_message": None,
    }
    try:
        if not reference_profile_id or source_type not in {"fixture", "real_dft"}:
            raise ValueError("Explicit reference profile and fixture/real_dft source required")
        result["artifact_sha256"] = sha256_file(source)
        if source_type == "real_dft" and (
            origin_is_fixture
            or any(
                word in source.read_text(errors="replace").lower()
                for word in ("fixture", "synthetic", "mock")
            )
        ):
            raise ValueError("Fixture content cannot be relabelled real_dft")
        parsed = _xml(source) if source.suffix.lower() == ".xml" else _outcar(source)
        if not np.isfinite(parsed["energy_eV"]) or not np.isfinite(parsed["forces_eV_per_A"]).all():
            raise ValueError("Non-finite DFT data")
        result.update(parsed)
        result["parse_status"] = "PARSED"
        converged = (
            parsed["electronic_converged"] is True
            and parsed["normal_termination"] is True
            and (parsed["calculation_type"] == "single_point" or parsed["ionic_converged"] is True)
        )
        result["convergence_status"] = (
            "CONVERGED_UNREVIEWED" if converged else "UNCONVERGED_OR_UNKNOWN"
        )
        # Even a converged file is not a validated label until separately reviewed and gate-attested.
    except (OSError, ValueError, TypeError, ET.ParseError) as exc:
        result["error_message"] = str(exc)
    return result
