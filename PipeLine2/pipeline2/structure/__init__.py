"""Deterministic Ag(111) demonstrations and single-substitution structures."""

import json
from dataclasses import dataclass, field
from importlib.resources import files
from numbers import Integral
from pathlib import Path

import numpy as np
from ase import Atoms
from ase.build import fcc111
from ase.constraints import FixAtoms
from ase.io import read, write

from pipeline2.registry import canonical_json, content_hash, sha256_file
from pipeline2.safety import writable_path, write_new


class InvalidStructure(ValueError):
    status = "INVALID_OUT_OF_SCOPE"


def fixed_indices(atoms: Atoms) -> list[int]:
    indices: set[int] = set()
    for constraint in atoms.constraints:
        if not isinstance(constraint, FixAtoms):
            raise InvalidStructure("MVP supports FixAtoms constraints only")
        indices.update(int(i) for i in constraint.get_indices())
    return sorted(indices)


def canonical_structure(atoms: Atoms) -> dict:
    positions = atoms.get_positions(wrap=True)
    # Quantization tolerates text serialization, not physical geometry changes.
    positions = np.round(positions, 6)
    positions[positions == 0] = 0
    return {
        "schema_version": "pipeline2-structure-1",
        "numbers": atoms.numbers.tolist(),
        "positions_A": positions.tolist(),
        "cell_A": np.round(atoms.cell.array, 8).tolist(),
        "pbc": atoms.pbc.tolist(),
        "fixed_indices": fixed_indices(atoms),
        "charge": int(atoms.info.get("charge", 0)),
    }


def structure_id(atoms: Atoms) -> str:
    return "S_" + content_hash(canonical_structure(atoms))


def geometry_fingerprint(atoms: Atoms) -> str:
    """Atom-order independent exact-geometry fingerprint; not full symmetry matching."""
    p = canonical_structure(atoms)
    fixed = set(p["fixed_indices"])
    sites = sorted(
        (z, *xyz, i in fixed) for i, (z, xyz) in enumerate(zip(p["numbers"], p["positions_A"]))
    )
    return content_hash(
        {"sites": sites, "cell_A": p["cell_A"], "pbc": p["pbc"], "charge": p["charge"]}
    )


def validate_structure(
    atoms: Atoms,
    *,
    supported_elements: list[str] | None = None,
    min_distance_A: float = 0.6,
    require_slab: bool = True,
    expected_fixed: list[int] | None = None,
) -> None:
    if (
        not len(atoms)
        or not np.isfinite(atoms.positions).all()
        or not np.isfinite(atoms.cell.array).all()
    ):
        raise InvalidStructure("Empty structure or non-finite geometry")
    if not np.isfinite(min_distance_A) or min_distance_A <= 0:
        raise InvalidStructure("Invalid minimum distance")
    if supported_elements is not None and not set(atoms.get_chemical_symbols()) <= set(
        supported_elements
    ):
        raise InvalidStructure("Unsupported element")
    if any(atoms.pbc) and abs(np.linalg.det(atoms.cell.array)) < 1e-8:
        raise InvalidStructure("Periodic structure needs a non-singular cell")
    if require_slab and not np.array_equal(atoms.pbc, [True, True, False]):
        raise InvalidStructure("Slab requires pbc=[true,true,false]; normalize POSCAR explicitly")
    fixed = fixed_indices(atoms)
    if any(i < 0 or i >= len(atoms) for i in fixed):
        raise InvalidStructure("Fixed atom index out of range")
    if require_slab and not fixed:
        raise InvalidStructure("Slab requires explicit fixed-layer constraints")
    if expected_fixed is not None and fixed != sorted(expected_fixed):
        raise InvalidStructure("Fixed-layer constraints changed")
    if len(atoms) > 1:
        distances = atoms.get_all_distances(mic=bool(any(atoms.pbc)))
        np.fill_diagonal(distances, np.inf)
        if np.min(distances) < min_distance_A:
            raise InvalidStructure("Duplicate or unphysically close atoms")
    if isinstance(atoms.info.get("charge", 0), bool) or not isinstance(
        atoms.info.get("charge", 0), Integral
    ):
        raise InvalidStructure("Explicit integer charge required")


@dataclass
class StructureRecord:
    atoms: Atoms
    metadata: dict = field(default_factory=dict)

    @property
    def structure_id(self) -> str:
        return structure_id(self.atoms)

    def to_dict(self) -> dict:
        return {
            **self.metadata,
            "structure_id": self.structure_id,
            "structure_hash": content_hash(canonical_structure(self.atoms)),
            "geometry_fingerprint": geometry_fingerprint(self.atoms),
            "geometry": canonical_structure(self.atoms),
        }


def ag111_template() -> StructureRecord:
    atoms = fcc111("Ag", size=(3, 3, 4), a=4.09, vacuum=15.0, periodic=False)
    atoms.set_pbc([True, True, False])
    atoms.set_constraint(FixAtoms(mask=atoms.get_tags() > 2))
    atoms.info.update(charge=0, source_type="fixture")
    validate_structure(atoms)
    return StructureRecord(
        atoms,
        {
            "source_type": "fixture",
            "origin": "ASE fcc111 demo",
            "template": {
                "size": [3, 3, 4],
                "a_A": 4.09,
                "vacuum_A": 15.0,
                "fixed_bottom_layers": 2,
            },
        },
    )


def read_structure(
    path: str | Path, *, role: str = "slab", fixed: list[int] | None = None
) -> StructureRecord:
    source = Path(path).resolve()
    atoms = read(source)
    if not isinstance(atoms, Atoms):
        raise InvalidStructure("Expected a single structure frame")
    # POSCAR cannot encode non-periodic surface direction; this role declaration is explicit.
    if role == "slab":
        atoms.set_pbc([True, True, False])
    elif role == "molecule":
        atoms.set_pbc(False)
    else:
        raise InvalidStructure("Unknown structure role")
    if fixed is not None:
        atoms.set_constraint(FixAtoms(indices=fixed))
    atoms.info.setdefault("charge", 0)
    validate_structure(atoms, require_slab=role == "slab")
    return StructureRecord(
        atoms,
        {
            "source_type": "fixture",
            "source_path": str(source.relative_to(Path(__file__).resolve().parents[3])) if source.is_relative_to(Path(__file__).resolve().parents[3]) else str(source),
            "source_sha256": sha256_file(source),
            "declared_role": role,
        },
    )


def substitute(parent: StructureRecord, dopant: str, index: int) -> StructureRecord:
    atoms = parent.atoms.copy()
    if isinstance(index, bool) or index < 0 or index >= len(atoms):
        raise InvalidStructure("Dopant index out of range")
    if index in fixed_indices(atoms):
        raise InvalidStructure("Cannot substitute a fixed-layer atom in this MVP")
    if atoms[index].symbol != "Ag" or dopant == "Ag":
        raise InvalidStructure("Expected one Ag site replaced by a different element")
    try:
        atoms[index].symbol = dopant
    except (KeyError, ValueError) as exc:
        raise InvalidStructure("Unknown dopant") from exc
    validate_structure(atoms, expected_fixed=fixed_indices(parent.atoms))
    return StructureRecord(
        atoms,
        {
            **parent.metadata,
            "parent_structure_id": parent.structure_id,
            "dopant": dopant,
            "dopant_site": index,
        },
    )


def ff_demo() -> StructureRecord:
    record = read_structure(
        str(files(__package__).joinpath("assets/ff_demo.extxyz")), role="molecule"
    )
    record.metadata["origin"] = "Synthetic neutral C5H4O2 geometry; not a relaxed reference"
    return record


def place_demo_adsorbate(
    slab: StructureRecord, molecule: StructureRecord, height_A: float
) -> StructureRecord:
    if molecule.atoms.info.get("charge", 0) != 0:
        raise InvalidStructure("Demo placement only supports neutral molecules")
    if not np.isfinite(height_A) or height_A < 2.0:
        raise InvalidStructure("Demo height must be at least 2 A")
    ads = molecule.atoms.copy()
    ads.positions -= ads.positions.mean(axis=0)
    center = (slab.atoms.cell[0] + slab.atoms.cell[1]) / 2
    ads.positions[:, :2] += center[:2]
    ads.positions[:, 2] += slab.atoms.positions[:, 2].max() + height_A
    combined = slab.atoms.copy() + ads
    combined.info.update(charge=0, source_type="fixture")
    validate_structure(combined, expected_fixed=fixed_indices(slab.atoms))
    return StructureRecord(
        combined,
        {
            "source_type": "fixture",
            "parent_structure_id": slab.structure_id,
            "adsorbate_structure_id": molecule.structure_id,
            "coverage": 1 / 9,
            "height_A": height_A,
            "origin": "demo placement",
        },
    )


def write_structure(record: StructureRecord, directory: str | Path) -> dict:
    target = writable_path(directory)
    target.mkdir(parents=True, exist_ok=True)
    sid = record.structure_id
    paths = {"extxyz": target / f"{sid}.extxyz", "POSCAR": target / f"{sid}.POSCAR"}
    for fmt, path in paths.items():
        writable_path(path)
        with path.open("x", encoding="utf-8") as handle:
            atoms = record.atoms.copy()
            atoms.calc = None
            atoms.info = {"charge": int(atoms.info.get("charge", 0))}
            atoms.info["pipeline2_metadata"] = canonical_json(record.metadata)
            if fmt == "extxyz":
                write(handle, atoms, format="extxyz", write_results=False)
            else:
                write(handle, atoms, format="vasp", direct=True, vasp5=True)
    payload = record.to_dict()
    payload["artifacts"] = {k: {"path": v.name, "sha256": sha256_file(v)} for k, v in paths.items()}
    # POSCAR's sidecar retains metadata and pbc information it cannot represent.
    write_new(target / f"{sid}.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload
