"""Local immutable artifacts, strict JSON and verified replay packages."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline2.contracts import load_payload


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    raw = (
        value
        if isinstance(value, bytes)
        else json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    )
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return load_payload(Path(path))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError(f"Immutable artifact conflict: {path}")
    else:
        with path.open("xb") as f:
            f.write(raw)
    return path


def ref(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    return {"path": path.relative_to(root).as_posix(), "sha256": digest(path.read_bytes())}


def verify(root, refs):
    root = Path(root).resolve()
    for r in refs:
        relative = Path(r["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"ARTIFACT_HASH_OR_PATH_INVALID:{r['path']}")
        path = (root / r["path"]).resolve()
        if (
            not path.is_relative_to(root)
            or not path.is_file()
            or digest(path.read_bytes()) != r["sha256"]
        ):
            raise ValueError(f"ARTIFACT_HASH_OR_PATH_INVALID:{r['path']}")


def files(root):
    return [
        ref(p, root)
        for p in sorted(Path(root).rglob("*"))
        if p.is_file()
        and p != Path(root) / "manifest.json"
        and p.name != ".lock"
        and not p.name.endswith(("-wal", "-shm"))
    ]


def load_replay(root):
    root = Path(root).resolve()
    manifest = read(root / "manifest.json")
    if manifest.get("schema_version") != "agx-run-manifest-1" or not manifest.get("artifacts"):
        raise ValueError("Unsupported or empty replay manifest")
    verify(root, manifest["artifacts"])
    declared = {r["path"] for r in manifest["artifacts"]}
    if "result.json" not in declared or "request.json" not in declared:
        raise ValueError("Replay result and request must be covered by hashes")
    result = read(root / "result.json")
    if result["run_id"] != manifest["run_id"]:
        raise ValueError("Replay run mismatch")
    return {**result, "view_mode": "REPLAY", "replay_of": result["run_id"]}
