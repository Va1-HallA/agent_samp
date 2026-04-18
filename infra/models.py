from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

import config

Base = declarative_base()


# Every business table carries tenant_id. Services enforce tenant scoping on
# all queries; database-level Row-Level Security can be layered on later.
_TENANT_DEFAULT = config.DEFAULT_TENANT_ID


class Resident(Base):
    __tablename__ = "residents"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(50), nullable=False, default=_TENANT_DEFAULT, index=True)
    name = Column(String(50), nullable=False)
    age = Column(Integer)
    room = Column(String(20))
    chronic_conditions = Column(Text)
    emergency_contact = Column(String(100))
    care_level = Column(String(20))

    health_records = relationship("HealthRecord", back_populates="resident")
    care_plan = relationship("CarePlan", back_populates="resident", uselist=False)
    alerts = relationship("Alert", back_populates="resident")

    __table_args__ = (
        Index("ix_residents_tenant_name", "tenant_id", "name"),
    )


class HealthRecord(Base):
    __tablename__ = "health_records"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(50), nullable=False, default=_TENANT_DEFAULT, index=True)
    resident_id = Column(Integer, ForeignKey("residents.id"), nullable=False)
    metric = Column(String(30))
    value = Column(String(50))
    recorded_at = Column(DateTime, default=datetime.now)

    resident = relationship("Resident", back_populates="health_records")

    __table_args__ = (
        Index("ix_health_tenant_resident_time", "tenant_id", "resident_id", "recorded_at"),
    )


class CarePlan(Base):
    __tablename__ = "care_plans"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(50), nullable=False, default=_TENANT_DEFAULT, index=True)
    resident_id = Column(Integer, ForeignKey("residents.id"), nullable=False)
    medication = Column(Text)
    diet = Column(Text)
    activity = Column(Text)
    notes = Column(Text)
    updated_at = Column(DateTime, default=datetime.now)

    resident = relationship("Resident", back_populates="care_plan")


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(50), nullable=False, default=_TENANT_DEFAULT, index=True)
    resident_id = Column(Integer, ForeignKey("residents.id"), nullable=False)
    alert_type = Column(String(30))
    description = Column(Text)
    severity = Column(String(10))
    created_at = Column(DateTime, default=datetime.now)
    resolved = Column(Boolean, default=False)

    resident = relationship("Resident", back_populates="alerts")

    __table_args__ = (
        Index("ix_alerts_tenant_resident_time", "tenant_id", "resident_id", "created_at"),
    )
