"""Health-data lookups and severity assessment.

Returns dict/list[dict] (string formatting is the tool layer's job).
All DB queries are tenant-scoped via ContextVar.
"""
from datetime import datetime, timedelta

from infra.db import SessionLocal
from infra.models import Resident, HealthRecord, CarePlan
from core.context import get_tenant_id


THRESHOLDS = {
    "blood_pressure": {
        "high":   {"systolic": 180, "diastolic": 110},
        "medium": {"systolic": 160, "diastolic": 100},
        "low":    {"systolic": 140, "diastolic": 90},
    },
    "heart_rate":     {"high": 120, "medium": 100, "low": 60},
    "blood_glucose":  {"high": 16.7, "medium": 11.1, "low": 3.9},
    "body_temperature": {"high": 39.0, "medium": 38.5, "low": 35.0},
}


class HealthService:

    def list_resident_names(self) -> list[str]:
        """Names of all residents for the current tenant."""
        tid = get_tenant_id()
        session = SessionLocal()
        try:
            rows = session.query(Resident.name).filter(Resident.tenant_id == tid).all()
            return [r[0] for r in rows]
        finally:
            session.close()

    def get_resident_profile(self, name: str) -> dict | None:
        tid = get_tenant_id()
        session = SessionLocal()
        try:
            resident = session.query(Resident).filter(
                Resident.tenant_id == tid,
                Resident.name == name,
            ).first()
            if not resident:
                return None
            care_plan = session.query(CarePlan).filter(
                CarePlan.tenant_id == tid,
                CarePlan.resident_id == resident.id,
            ).first()
            return {
                "id": resident.id, "name": resident.name,
                "age": resident.age, "room": resident.room,
                "chronic_conditions": resident.chronic_conditions,
                "care_level": resident.care_level,
                "emergency_contact": resident.emergency_contact,
                "medication": care_plan.medication if care_plan else None,
                "diet": care_plan.diet if care_plan else None,
                "activity": care_plan.activity if care_plan else None,
            }
        finally:
            session.close()

    def get_recent_records(self, resident_id: int, metric: str = None,
                           days: int = 7) -> list[dict]:
        tid = get_tenant_id()
        session = SessionLocal()
        try:
            since = datetime.now() - timedelta(days=days)
            q = session.query(HealthRecord).filter(
                HealthRecord.tenant_id == tid,
                HealthRecord.resident_id == resident_id,
                HealthRecord.recorded_at >= since,
            )
            if metric:
                q = q.filter(HealthRecord.metric == metric)
            records = q.order_by(HealthRecord.recorded_at.desc()).all()
            return [
                {"metric": r.metric, "value": r.value,
                 "time": r.recorded_at.isoformat()}
                for r in records
            ]
        finally:
            session.close()

    def compute_baseline(self, resident_id: int, metric: str,
                         days: int = 7) -> dict:
        """Baseline mean over the last N days. Blood pressure is split into
        systolic/diastolic before averaging."""
        records = self.get_recent_records(resident_id, metric, days)
        if not records:
            return {"mean": None, "count": 0}

        if metric == "blood_pressure":
            sys_vals, dia_vals = [], []
            for r in records:
                parts = r["value"].split("/")
                if len(parts) == 2:
                    try:
                        sys_vals.append(float(parts[0]))
                        dia_vals.append(float(parts[1]))
                    except ValueError:
                        continue
            if not sys_vals:
                return {"mean": None, "count": 0}
            return {
                "mean_systolic": round(sum(sys_vals) / len(sys_vals), 1),
                "mean_diastolic": round(sum(dia_vals) / len(dia_vals), 1),
                "count": len(sys_vals),
            }

        values = []
        for r in records:
            try:
                values.append(float(r["value"]))
            except ValueError:
                continue
        if not values:
            return {"mean": None, "count": 0}
        return {"mean": round(sum(values) / len(values), 1), "count": len(values)}

    def assess_severity(self, metric: str, current_value: str) -> str:
        """Return one of: high / medium / low / normal."""
        thresholds = THRESHOLDS.get(metric)
        if not thresholds:
            return "normal"

        if metric == "blood_pressure":
            parts = current_value.split("/")
            if len(parts) != 2:
                return "normal"
            try:
                sys_v, dia_v = float(parts[0]), float(parts[1])
            except ValueError:
                return "normal"
            for level in ("high", "medium", "low"):
                if sys_v >= thresholds[level]["systolic"] or dia_v >= thresholds[level]["diastolic"]:
                    return level
            return "normal"

        try:
            val = float(current_value)
        except ValueError:
            return "normal"
        for level in ("high", "medium"):
            if val >= thresholds[level]:
                return level
        if metric == "body_temperature" and val < thresholds["low"]:
            return "low"
        if metric == "heart_rate" and val < thresholds["low"]:
            return "low"
        return "normal"
