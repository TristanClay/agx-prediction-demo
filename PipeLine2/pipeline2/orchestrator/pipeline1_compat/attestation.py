# Migrated from Pipeline1 without modifying its source.
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping
from urllib.parse import unquote, urlparse

ATTESTATION_SCHEMA_VERSION = "validation-artifact-attestation-v2.3-1"
COMPARABILITY_SCHEMA_VERSION = "validation-comparability-v2.3-1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_sha256(value: Any) -> bool:
    token = str(value or "").strip().lower()
    return len(token) == 64 and all(character in "0123456789abcdef" for character in token)


def _external_uri(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme and parsed.scheme not in {"file"})


def _resolve_local_path(value: str, base_dir: Path) -> Path:
    parsed = urlparse(value)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path)).resolve()
    path = Path(value)
    return (path if path.is_absolute() else base_dir / path).resolve()


def attest_artifact(
    declared_path: Any,
    declared_sha256: Any,
    base_dir: str | Path,
    artifact_kind: str,
    validation_id: str = "",
) -> Dict[str, Any]:
    """Verify an accessible local artifact without fetching external locations.

    External URIs are deliberately retained as traceable references but are never
    calibration eligible.  This keeps the feedback layer append-only and avoids
    silently treating a user-provided URL as verified experimental evidence.
    """

    path_value = str(declared_path or "").strip()
    expected = str(declared_sha256 or "").strip().lower()
    row: Dict[str, Any] = {
        "schema_version": ATTESTATION_SCHEMA_VERSION,
        "validation_id": validation_id,
        "artifact_kind": artifact_kind,
        "declared_path": path_value,
        "declared_sha256": expected,
        "status": "",
        "resolved_path": "",
        "actual_sha256": "",
        "attested_at": datetime.now().isoformat(timespec="seconds"),
    }
    if not path_value:
        row["status"] = "path_missing"
        return row
    if not expected:
        row["status"] = "sha256_missing"
        return row
    if not is_sha256(expected):
        row["status"] = "sha256_invalid"
        return row
    if _external_uri(path_value):
        row["status"] = "external_unchecked"
        return row

    resolved = _resolve_local_path(path_value, Path(base_dir))
    row["resolved_path"] = str(resolved)
    if not resolved.is_file():
        row["status"] = "local_file_missing"
        return row
    actual = sha256_file(resolved)
    row["actual_sha256"] = actual
    row["status"] = "verified_local" if actual == expected else "sha256_mismatch"
    return row


def attest_result_artifacts(
    result: Mapping[str, Any], base_dir: str | Path
) -> List[Dict[str, Any]]:
    validation_id = str(result.get("validation_id", ""))
    return [
        attest_artifact(
            result.get("artifact_path"),
            result.get("artifact_sha256"),
            base_dir,
            "result",
            validation_id,
        ),
        attest_artifact(
            result.get("verification_artifact_path"),
            result.get("verification_artifact_sha256"),
            base_dir,
            "structure_verification",
            validation_id,
        ),
    ]


def build_comparability_template() -> Dict[str, Any]:
    return {
        "schema_version": COMPARABILITY_SCHEMA_VERSION,
        "purpose": (
            "Human-approved run-context registry. It records comparability and "
            "does not itself establish scientific equivalence or release a gate."
        ),
        "approved_by": "",
        "approved_at": "",
        "contexts": [
            {
                "run_context_id": "",
                "conditions_hash": "",
                "comparability_scope_id": "",
                "candidate_ids": [],
                "comparator_candidate_ids": [],
                "deviation_explanation": "",
                "approved_by": "",
                "approved_at": "",
            }
        ],
    }


def _is_iso8601(value: Any) -> bool:
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return True


def load_comparability_manifest(path: str | Path | None) -> Dict[str, Any]:
    """Load an optional human-approved comparability registry.

    An absent registry is represented explicitly rather than inferred from
    matching free-text fields in the results table.
    """

    if path is None:
        return {"present": False, "path": "", "contexts": {}, "errors": ["manifest_missing"]}
    manifest_path = Path(path)
    if not manifest_path.is_file():
        return {
            "present": False,
            "path": str(manifest_path.resolve()),
            "contexts": {},
            "errors": ["manifest_missing"],
        }
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "present": True,
            "path": str(manifest_path.resolve()),
            "contexts": {},
            "errors": [f"manifest_invalid_json: {exc}"],
        }
    errors: List[str] = []
    if not isinstance(payload, dict):
        errors.append("manifest_not_object")
        payload = {}
    if payload.get("schema_version") != COMPARABILITY_SCHEMA_VERSION:
        errors.append("manifest_schema_unsupported")
    contexts: Dict[str, Dict[str, Any]] = {}
    for item in payload.get("contexts", []) if isinstance(payload.get("contexts"), list) else []:
        if not isinstance(item, dict):
            errors.append("context_not_object")
            continue
        context_id = str(item.get("run_context_id", "")).strip()
        conditions_hash = str(item.get("conditions_hash", "")).strip().lower()
        scope_id = str(item.get("comparability_scope_id", "")).strip()
        approved_by = str(item.get("approved_by") or payload.get("approved_by") or "").strip()
        approved_at = str(item.get("approved_at") or payload.get("approved_at") or "").strip()
        if not context_id or not conditions_hash or not scope_id:
            errors.append(f"context_incomplete:{context_id or 'unknown'}")
            continue
        if not is_sha256(conditions_hash):
            errors.append(f"context_conditions_hash_invalid:{context_id}")
            continue
        if not approved_by or not _is_iso8601(approved_at):
            errors.append(f"context_not_human_approved:{context_id}")
            continue
        if context_id in contexts:
            errors.append(f"context_duplicate:{context_id}")
            continue
        contexts[context_id] = {
            **item,
            "run_context_id": context_id,
            "conditions_hash": conditions_hash,
            "comparability_scope_id": scope_id,
            "approved_by": approved_by,
            "approved_at": approved_at,
        }
    return {
        "present": True,
        "path": str(manifest_path.resolve()),
        "contexts": contexts,
        "errors": errors,
        "schema_version": payload.get("schema_version", ""),
    }


def comparability_attestation(
    result: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> Dict[str, Any]:
    context_id = str(result.get("run_context_id", "")).strip()
    conditions_hash = str(result.get("conditions_hash", "")).strip().lower()
    scope_id = str(result.get("comparability_scope_id", "")).strip()
    row = {
        "validation_id": str(result.get("validation_id", "")),
        "run_context_id": context_id,
        "conditions_hash": conditions_hash,
        "comparability_scope_id": scope_id,
        "status": "",
        "manifest_path": str(registry.get("path", "")),
    }
    if not registry.get("present"):
        row["status"] = "manifest_missing"
        return row
    if registry.get("errors"):
        row["status"] = "manifest_invalid"
        return row
    if not context_id or not conditions_hash or not scope_id:
        row["status"] = "result_context_incomplete"
        return row
    approved = (registry.get("contexts") or {}).get(context_id)
    if not approved:
        row["status"] = "context_not_registered"
        return row
    if approved.get("conditions_hash") != conditions_hash:
        row["status"] = "conditions_hash_not_approved"
        return row
    if approved.get("comparability_scope_id") != scope_id:
        row["status"] = "scope_not_approved"
        return row
    row["status"] = "human_approved"
    return row


def artifact_status_reasons(attestations: Iterable[Mapping[str, Any]]) -> List[str]:
    reasons: List[str] = []
    for row in attestations:
        kind = str(row.get("artifact_kind", "artifact"))
        status = str(row.get("status", ""))
        if status == "verified_local":
            continue
        if status == "external_unchecked":
            reasons.append(f"{kind}_external_unchecked")
        else:
            reasons.append(f"{kind}_{status or 'attestation_failed'}")
    return reasons
