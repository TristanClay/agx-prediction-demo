"""Namespaced, append-only entities and content-addressed raw files."""

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from pipeline3.contracts import ENTITIES, Record


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()


def stable_id(kind, value):
    return f"{kind}_{digest(value)[:24]}"


class Store:
    def __init__(self, root="data/local"):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw = self.root / "raw"
        self.raw.mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / "pipeline3.sqlite", timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS records (
            namespace TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
            document TEXT NOT NULL, PRIMARY KEY(namespace, kind, id))""")
        self.db.execute("CREATE INDEX IF NOT EXISTS record_kind ON records(namespace,kind)")
        self.db.commit()

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        try:
            self.db.execute("BEGIN IMMEDIATE")
            yield
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def add(self, namespace, kind, payload, *, key=None, source_type="experimental", **statuses):
        if kind not in ENTITIES:
            raise ValueError(f"Unknown entity: {kind}")
        if kind == "FeedbackPackage" and (statuses.get("feedback_status") != "STAGED"
                or payload.get("official_publish") is not False or payload.get("upstream_write_performed") is not False):
            raise ValueError("V1 feedback is staged only; no formal publishing channel")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+(?::[a-zA-Z0-9_-]+)?", namespace):
            raise ValueError("Namespace must use letters, numbers, underscores, hyphens, optional colon")
        entity_id = key or stable_id(kind, payload)
        old = self.get(namespace, kind, entity_id)
        if old:
            # Idempotency must never silently replace an entity under an existing ID.
            if old["payload"] != payload or old["source_type"] != source_type:
                raise ValueError(f"Immutable identity conflict: {kind}/{entity_id}")
            return old
        record = Record(id=entity_id, kind=kind, namespace=namespace, payload=payload,
                        created_at=now(), source_type=source_type, **statuses).model_dump()
        self.db.execute("INSERT INTO records VALUES(?,?,?,?)", (namespace, kind, entity_id, canonical(record)))
        return record

    def get(self, namespace, kind, entity_id):
        row = self.db.execute("SELECT document FROM records WHERE namespace=? AND kind=? AND id=?",
                              (namespace, kind, entity_id)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, namespace, kind=None):
        sql = "SELECT document FROM records WHERE namespace=?"
        args = [namespace]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        sql += " ORDER BY rowid"
        return [json.loads(r[0]) for r in self.db.execute(sql, args)]

    def latest(self, namespace, kind):
        rows = self.list(namespace, kind)
        return rows[-1] if rows else None

    def namespaces(self):
        return [r[0] for r in self.db.execute("SELECT DISTINCT namespace FROM records ORDER BY namespace")]

    def artifact(self, namespace, content, filename, source_type):
        sha = digest(content)
        path = self.raw / sha
        if path.exists():
            if digest(path.read_bytes()) != sha:
                raise ValueError("RAW_ARTIFACT_HASH_MISMATCH")
        else:
            with path.open("xb") as handle:
                handle.write(content)
            path.chmod(0o444)
        return self.add(namespace, "RawArtifact", {"sha256": sha, "storage_key": sha,
                        "size_bytes": len(content)}, key=f"raw_{sha}", source_type=source_type)

    def read_artifact(self, namespace, artifact_id):
        row = self.get(namespace, "RawArtifact", artifact_id)
        if not row:
            raise ValueError("Artifact is not in this namespace")
        content = (self.raw / row["payload"]["storage_key"]).read_bytes()
        if digest(content) != row["payload"]["sha256"]:
            raise ValueError("RAW_ARTIFACT_HASH_MISMATCH")
        return content

    def reset_demo(self, namespace):
        if not namespace.startswith("demo:"):
            raise ValueError("Only demo: namespaces may be reset")
        with self.transaction():
            self.db.execute("DELETE FROM records WHERE namespace=?", (namespace,))
        # Blobs are retained: shared and real imports are never deleted by demo reset.
