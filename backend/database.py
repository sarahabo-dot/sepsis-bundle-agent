"""
database.py
Persistent storage layer, replacing the in-memory dictionaries used in the
prototype. Defaults to SQLite for local/dev use; set DATABASE_URL to a
Postgres connection string for real deployment (e.g. on a hospital-approved
server, never on public shared infrastructure for real patient data).
"""

import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, String, Float, Integer, Boolean, DateTime, JSON, ForeignKey
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./sepsis_agent.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    role = Column(String, nullable=False, default="physician")  # physician | nurse | reviewer
    is_active = Column(Boolean, default=True)


class PatientSession(Base):
    __tablename__ = "patient_sessions"
    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(String, index=True, nullable=False)
    baseline_sofa = Column(Integer, nullable=True)
    recognition_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    values = relationship("ClinicalValueRecord", back_populates="session")
    confirmations = relationship("BundleConfirmation", back_populates="session")


class ClinicalValueRecord(Base):
    __tablename__ = "clinical_values"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("patient_sessions.id"))
    domain = Column(String, nullable=False)
    value = Column(Float, nullable=True)
    unit = Column(String, nullable=True)
    source = Column(String, nullable=False)
    status = Column(String, nullable=False)  # confirmed | draft | stale
    flag_reason = Column(String, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)

    session = relationship("PatientSession", back_populates="values")


class BundleConfirmation(Base):
    __tablename__ = "bundle_confirmations"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("patient_sessions.id"))
    item_key = Column(String, nullable=False)
    done = Column(Boolean, default=True)
    confirmed_at = Column(DateTime, default=datetime.utcnow)
    acknowledged_by = Column(String, nullable=False)
    confirmed_by = Column(String, nullable=True)  # required for high-risk items

    session = relationship("PatientSession", back_populates="confirmations")


class AuditEventRecord(Base):
    """Append-only by convention: the application layer must never issue
    UPDATE or DELETE against this table. For real deployment, enforce this
    at the database grant level (REVOKE UPDATE, DELETE for the app role)."""
    __tablename__ = "audit_events"
    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String, unique=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    actor = Column(String, nullable=False)
    action = Column(String, nullable=False)
    detail = Column(JSON, nullable=True)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
