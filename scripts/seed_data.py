"""Seed mock data: 3 residents + 7 days of health records + care plans + alerts.

    python -m scripts.seed_data
"""
from datetime import datetime, timedelta
import random

import config
from infra.db import SessionLocal
from infra.models import Resident, HealthRecord, CarePlan, Alert


TENANT_ID = config.DEFAULT_TENANT_ID


RESIDENTS_SEED = [
    {
        "name": "Mr. Zhang", "age": 78, "room": "301",
        "chronic_conditions": '["hypertension","coronary_heart_disease"]',
        "emergency_contact": "Zhang Xiaoming 13800001111",
        "care_level": "level_2",
        "bp_base": (150, 95),
        "hr_base": 78,
        "medication": "Nifedipine sustained-release 30mg once daily; Aspirin 100mg once daily",
        "diet": "Low salt, low fat; daily salt intake under 5g",
        "activity": "30 min walk daily; avoid vigorous exercise",
        "notes": "Blood pressure persistently elevated; monitor closely",
    },
    {
        "name": "Mrs. Li", "age": 82, "room": "205",
        "chronic_conditions": '["diabetes","osteoporosis"]',
        "emergency_contact": "Li Jianguo 13900002222",
        "care_level": "level_3",
        "bp_base": (135, 82),
        "hr_base": 72,
        "medication": "Metformin 500mg twice daily; Calcium 600mg once daily",
        "diet": "Diabetic diet; limit carbs; small frequent meals",
        "activity": "Bedside activity; fall precautions",
        "notes": "History of falls; activity requires assistance",
    },
    {
        "name": "Mr. Wang", "age": 71, "room": "402",
        "chronic_conditions": '["COPD"]',
        "emergency_contact": "Wang Fang 13700003333",
        "care_level": "level_1",
        "bp_base": (128, 78),
        "hr_base": 85,
        "medication": "Budesonide/formoterol inhaler twice daily",
        "diet": "Regular diet",
        "activity": "1 hour outdoor activity daily",
        "notes": "Overall stable",
    },
]


def seed():
    session = SessionLocal()
    try:
        # Idempotent: wipe existing rows before re-inserting.
        session.query(Alert).delete()
        session.query(HealthRecord).delete()
        session.query(CarePlan).delete()
        session.query(Resident).delete()
        session.commit()

        now = datetime.now()
        for spec in RESIDENTS_SEED:
            resident = Resident(
                tenant_id=TENANT_ID,
                name=spec["name"], age=spec["age"], room=spec["room"],
                chronic_conditions=spec["chronic_conditions"],
                emergency_contact=spec["emergency_contact"],
                care_level=spec["care_level"],
            )
            session.add(resident)
            session.flush()

            sys_base, dia_base = spec["bp_base"]
            hr_base = spec["hr_base"]
            for i in range(7):
                day = now - timedelta(days=6 - i)
                session.add(HealthRecord(
                    tenant_id=TENANT_ID,
                    resident_id=resident.id, metric="blood_pressure",
                    value=f"{sys_base + random.randint(-3, 3)}/{dia_base + random.randint(-2, 2)}",
                    recorded_at=day.replace(hour=8, minute=0),
                ))
                session.add(HealthRecord(
                    tenant_id=TENANT_ID,
                    resident_id=resident.id, metric="heart_rate",
                    value=str(hr_base + random.randint(-4, 4)),
                    recorded_at=day.replace(hour=8, minute=5),
                ))

            session.add(CarePlan(
                tenant_id=TENANT_ID,
                resident_id=resident.id,
                medication=spec["medication"], diet=spec["diet"],
                activity=spec["activity"], notes=spec["notes"],
            ))

            session.add(Alert(
                tenant_id=TENANT_ID,
                resident_id=resident.id,
                alert_type="vital_signs_abnormal",
                description=f"{spec['name']}: mild blood pressure fluctuation (observed)",
                severity="low", resolved=True,
                created_at=now - timedelta(days=3),
            ))

        session.commit()
        print("seed done:", [s["name"] for s in RESIDENTS_SEED])
    finally:
        session.close()


if __name__ == "__main__":
    seed()
