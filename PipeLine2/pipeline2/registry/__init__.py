"""Append-only records and content-addressed provenance."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline2.safety import writable_path

ENTITY_TYPES = {
    "Task",
    "Structure",
    "Prediction",
    "DFTJob",
    "DFTLabel",
    "ModelVersion",
    "Decision",
    "Event",
}
SOURCE_TYPES = {"fixture", "mock", "predicted", "real_dft", "experimental"}


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_formal_sources(records: list[dict]) -> None:
    for row in records:
        # Prediction provenance is distinct from its reference-label provenance.
        if row.get("source_type") not in {"predicted", "real_dft", "experimental"}:
            raise ValueError("Mock/fixture/unknown sources cannot enter formal evaluation")
        if row.get("source_type") in {"real_dft", "experimental"} and (
            row.get("review_status") != "approved" or not row.get("artifact_sha256")
        ):
            raise ValueError("Formal labels require reviewed artifact provenance")
        if row.get("origin_is_fixture") is True or set(row.get("lineage_source_types", [])) & {
            "mock",
            "fixture",
        }:
            raise ValueError("Mock/fixture lineage cannot enter formal evaluation")
        if row.get("source_type") in {"real_dft", "experimental"}:
            if (
                not row.get("source_path")
                or sha256_file(row["source_path"]) != row["artifact_sha256"]
            ):
                raise ValueError("Formal raw artifact hash mismatch")


class DataRegistry:
    def __init__(self, path: str | Path):
        self.path = writable_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    entity_type TEXT NOT NULL, record_id TEXT NOT NULL,
                    payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                    source_type TEXT NOT NULL, schema_version TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(entity_type,record_id)
                );
                CREATE TRIGGER IF NOT EXISTS records_no_update BEFORE UPDATE ON records
                    BEGIN SELECT RAISE(ABORT,'append-only'); END;
                CREATE TRIGGER IF NOT EXISTS records_no_delete BEFORE DELETE ON records
                    BEGIN SELECT RAISE(ABORT,'append-only'); END;
                CREATE TABLE IF NOT EXISTS budget_events (
                    event_id INTEGER PRIMARY KEY, budget_id TEXT NOT NULL,
                    job_id TEXT NOT NULL, event TEXT NOT NULL,
                    core_hours REAL NOT NULL CHECK(core_hours>=0), created_at TEXT NOT NULL,
                    UNIQUE(budget_id,job_id,event)
                );
                CREATE TRIGGER IF NOT EXISTS budget_no_update BEFORE UPDATE ON budget_events
                    BEGIN SELECT RAISE(ABORT,'append-only'); END;
                CREATE TRIGGER IF NOT EXISTS budget_no_delete BEFORE DELETE ON budget_events
                    BEGIN SELECT RAISE(ABORT,'append-only'); END;
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(writable_path(self.path), timeout=20)
        try:
            with db:
                yield db
        finally:
            db.close()

    def append(self, entity_type: str, record_id: str, payload: dict, source_type: str) -> str:
        if entity_type not in ENTITY_TYPES or source_type not in SOURCE_TYPES or not record_id:
            raise ValueError("Invalid registry entity, id or source type")
        if payload.get("source_type", source_type) != source_type:
            raise ValueError("Conflicting source types")
        if entity_type == "DFTLabel" and source_type in {"mock", "predicted"}:
            raise ValueError("Predictions cannot be registered as DFT labels")
        if source_type in {"real_dft", "experimental"}:
            artifact = payload.get("source_path")
            if not artifact or sha256_file(artifact) != payload.get("artifact_sha256"):
                raise ValueError("Scientific raw data require an accessible matching artifact")
            if payload.get("origin_is_fixture") is not False:
                raise ValueError("Scientific raw data require explicit non-fixture provenance")
        encoded = canonical_json(payload)
        digest = content_hash(payload)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT payload_sha256,source_type FROM records WHERE entity_type=? AND record_id=?",
                (entity_type, record_id),
            ).fetchone()
            if old:
                if old != (digest, source_type):
                    raise ValueError("Immutable record conflict")
                return digest
            db.execute(
                "INSERT INTO records VALUES (?,?,?,?,?,?,?)",
                (
                    entity_type,
                    record_id,
                    encoded,
                    digest,
                    source_type,
                    payload.get("schema_version", "pipeline2-record-1"),
                    utc_now(),
                ),
            )
        return digest

    def records(self, entity_type: str) -> list[dict]:
        with self.connect() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM records WHERE entity_type=? ORDER BY record_id",
                    (entity_type,),
                )
            ]
