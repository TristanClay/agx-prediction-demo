"""Transactional reservation ledger. Proposals are not real resource consumption."""

import math

from pipeline2.registry import DataRegistry, utc_now


class BudgetExceeded(ValueError):
    pass


class BudgetGuard:
    def __init__(
        self, registry: DataRegistry, budget_id: str, max_jobs: int, max_core_hours: float
    ):
        if (
            isinstance(max_jobs, bool)
            or not isinstance(max_jobs, int)
            or max_jobs < 0
            or not math.isfinite(max_core_hours)
            or max_core_hours < 0
        ):
            raise ValueError("Invalid budget")
        self.registry, self.budget_id = registry, budget_id
        self.max_jobs, self.max_core_hours = max_jobs, max_core_hours
        registry.append(
            "Event",
            f"budget:{budget_id}",
            {"max_jobs": max_jobs, "max_core_hours": max_core_hours, "kind": "budget_limit"},
            "predicted",
        )

    def reserve(self, job_id: str, core_hours: float) -> dict:
        if not job_id or not math.isfinite(core_hours) or core_hours <= 0:
            raise ValueError("Positive, finite cost and job id required")
        with self.registry.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT core_hours FROM budget_events WHERE budget_id=? AND job_id=? AND event='reserved'",
                (self.budget_id, job_id),
            ).fetchone()
            if old:
                if old[0] != core_hours:
                    raise ValueError("Reservation cost conflict")
                return {"status": "RESERVED", "idempotent": True}
            count, used = db.execute(
                "SELECT count(*),coalesce(sum(core_hours),0) FROM budget_events WHERE budget_id=? AND event='reserved'",
                (self.budget_id,),
            ).fetchone()
            if count + 1 > self.max_jobs or used + core_hours > self.max_core_hours:
                raise BudgetExceeded("DFT jobs or core-hour budget exceeded")
            db.execute(
                "INSERT INTO budget_events(budget_id,job_id,event,core_hours,created_at) VALUES (?,?,?,?,?)",
                (self.budget_id, job_id, "reserved", core_hours, utc_now()),
            )
        return {"status": "RESERVED", "idempotent": False}

    def record_actual(self, job_id: str, core_hours: float) -> None:
        if not math.isfinite(core_hours) or core_hours < 0:
            raise ValueError("Invalid actual cost")
        with self.registry.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            reserved = db.execute(
                "SELECT core_hours FROM budget_events WHERE budget_id=? AND job_id=? AND event='reserved'",
                (self.budget_id, job_id),
            ).fetchone()
            if not reserved or core_hours > reserved[0]:
                raise BudgetExceeded("Actual cost exceeds reservation; stop and audit")
            old = db.execute(
                "SELECT core_hours FROM budget_events WHERE budget_id=? AND job_id=? AND event='actual'",
                (self.budget_id, job_id),
            ).fetchone()
            if old:
                if old[0] != core_hours:
                    raise ValueError("Actual cost conflict")
                return
            db.execute(
                "INSERT INTO budget_events(budget_id,job_id,event,core_hours,created_at) VALUES (?,?,?,?,?)",
                (self.budget_id, job_id, "actual", core_hours, utc_now()),
            )

    def snapshot(self) -> dict:
        with self.registry.connect() as db:
            reserved_jobs, hours = db.execute(
                "SELECT count(*),coalesce(sum(core_hours),0) FROM budget_events WHERE budget_id=? AND event='reserved'",
                (self.budget_id,),
            ).fetchone()
            actual = db.execute(
                "SELECT coalesce(sum(core_hours),0) FROM budget_events WHERE budget_id=? AND event='actual'",
                (self.budget_id,),
            ).fetchone()[0]
        return {
            "max_jobs": self.max_jobs,
            "max_core_hours": self.max_core_hours,
            "reserved_jobs": reserved_jobs,
            "reserved_core_hours": hours,
            "actual_core_hours": actual,
        }
