"""Alert service: create, query, resolve. All operations are tenant-scoped."""
from infra.db import SessionLocal
from infra.models import Alert
from core.context import get_tenant_id


class AlertService:

    def create_alert(self, resident_id: int, alert_type: str,
                     description: str, severity: str) -> dict:
        tid = get_tenant_id()
        session = SessionLocal()
        try:
            alert = Alert(
                tenant_id=tid,
                resident_id=resident_id,
                alert_type=alert_type,
                description=description,
                severity=severity,
            )
            session.add(alert)
            session.commit()
            return {
                "alert_id": alert.id,
                "severity": severity,
                "created_at": alert.created_at.isoformat(),
            }
        finally:
            session.close()

    def get_unresolved(self, resident_id: int, limit: int = 20) -> list[dict]:
        tid = get_tenant_id()
        session = SessionLocal()
        try:
            alerts = (
                session.query(Alert)
                .filter(
                    Alert.tenant_id == tid,
                    Alert.resident_id == resident_id,
                    Alert.resolved == False,
                )
                .order_by(Alert.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": a.id, "type": a.alert_type,
                    "desc": a.description, "severity": a.severity,
                    "time": a.created_at.isoformat(),
                }
                for a in alerts
            ]
        finally:
            session.close()

    def resolve(self, alert_id: int) -> bool:
        tid = get_tenant_id()
        session = SessionLocal()
        try:
            # Filter on tenant_id + id to prevent cross-tenant resolution.
            alert = session.query(Alert).filter(
                Alert.tenant_id == tid,
                Alert.id == alert_id,
            ).first()
            if not alert:
                return False
            alert.resolved = True
            session.commit()
            return True
        finally:
            session.close()
