"""
Database models pre MedicalAI
Podporuje PostgreSQL (Railway/Supabase) aj SQLite (lokálne)
"""
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, Text, Boolean, ForeignKey, JSON, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import os
import logging
from pathlib import Path

_db_logger = logging.getLogger(__name__)

Base = declarative_base()


class Patient(Base):
    __tablename__ = 'patients'
    id = Column(Integer, primary_key=True)
    first_name = Column(String(100))
    last_name = Column(String(100))
    date_of_birth = Column(Date)
    gender = Column(String(10))
    blood_type = Column(String(5))
    height_cm = Column(Float)
    email = Column(String(200))
    phone = Column(String(50))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    health_records = relationship("HealthRecord", back_populates="patient")
    family_members = relationship("FamilyMember", back_populates="patient")


class FamilyMember(Base):
    __tablename__ = 'family_members'
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey('patients.id'))
    first_name = Column(String(100))
    last_name = Column(String(100))
    relationship_type = Column(String(50))
    date_of_birth = Column(Date, nullable=True)
    date_of_death = Column(Date, nullable=True)
    gender = Column(String(10))
    blood_type = Column(String(5), nullable=True)
    chronic_conditions = Column(JSON)
    genetic_conditions = Column(JSON)
    allergies = Column(JSON)
    medications = Column(JSON)
    surgeries = Column(JSON)
    smoking = Column(Boolean, default=False)
    smoking_years = Column(Integer, nullable=True)
    alcohol = Column(Boolean, default=False)
    exercise_frequency = Column(String(50), nullable=True)
    cause_of_death = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    patient = relationship("Patient", back_populates="family_members")


class HealthRecord(Base):
    __tablename__ = 'health_records'
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey('patients.id'))
    record_type = Column(String(50))
    record_date = Column(Date)
    source = Column(String(100))
    source_file = Column(String(500), nullable=True)
    metric_type = Column(String(100))
    value = Column(String(50))
    unit = Column(String(20), nullable=True)
    reference_range = Column(String(100), nullable=True)
    is_normal = Column(Boolean, nullable=True)
    interpretation = Column(String(50), nullable=True)
    doctor_name = Column(String(200), nullable=True)
    facility_name = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    patient = relationship("Patient", back_populates="health_records")


class Document(Base):
    __tablename__ = 'documents'
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey('patients.id'), nullable=True)
    filename = Column(String(500))
    file_path = Column(String(1000))
    file_type = Column(String(50))
    file_size_bytes = Column(Integer)
    ocr_processed = Column(Boolean, default=False)
    ocr_text = Column(Text, nullable=True)
    processing_status = Column(String(50))
    processing_error = Column(Text, nullable=True)
    document_type = Column(String(100), nullable=True)
    document_date = Column(Date, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.now)
    processed_at = Column(DateTime, nullable=True)


class GarminData(Base):
    __tablename__ = 'garmin_data'
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey('patients.id'))
    record_date = Column(Date, unique=True)
    resting_heart_rate = Column(Integer, nullable=True)
    max_heart_rate = Column(Integer, nullable=True)
    min_heart_rate = Column(Integer, nullable=True)
    avg_heart_rate = Column(Integer, nullable=True)
    total_sleep_seconds = Column(Integer, nullable=True)
    deep_sleep_seconds = Column(Integer, nullable=True)
    light_sleep_seconds = Column(Integer, nullable=True)
    rem_sleep_seconds = Column(Integer, nullable=True)
    awake_seconds = Column(Integer, nullable=True)
    sleep_score = Column(Integer, nullable=True)
    avg_stress_level = Column(Integer, nullable=True)
    max_stress_level = Column(Integer, nullable=True)
    total_steps = Column(Integer, nullable=True)
    total_distance_meters = Column(Integer, nullable=True)
    active_calories = Column(Integer, nullable=True)
    weight_kg = Column(Float, nullable=True)
    bmi = Column(Float, nullable=True)
    body_fat_percentage = Column(Float, nullable=True)
    synced_at = Column(DateTime, default=datetime.now)


class CalendarEvent(Base):
    __tablename__ = 'calendar_events'
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey('patients.id'))
    google_event_id = Column(String(200), unique=True)
    summary = Column(String(500))
    description = Column(Text, nullable=True)
    location = Column(String(500), nullable=True)
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    is_all_day = Column(Boolean, default=False)
    category = Column(String(50), nullable=True)
    synced_at = Column(DateTime, default=datetime.now)


class AppleHealthData(Base):
    __tablename__ = 'apple_health_data'
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey('patients.id'), default=1)
    record_type = Column(String(200))
    value = Column(Float)
    unit = Column(String(50))
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    creation_date = Column(DateTime, nullable=True)
    source_name = Column(String(200), nullable=True)
    source_version = Column(String(100), nullable=True)
    device_name = Column(String(200), nullable=True)
    device_manufacturer = Column(String(100), nullable=True)
    device_model = Column(String(100), nullable=True)
    device_hardware = Column(String(100), nullable=True)
    device_software = Column(String(100), nullable=True)
    record_metadata = Column(JSON, nullable=True)
    imported_at = Column(DateTime, default=datetime.now)
    import_batch_id = Column(String(50), nullable=True)
    patient = relationship("Patient")


# ─── Engine singleton ────────────────────────────────────────────────────────

_engine = None
_SessionLocal = None
_db_initialized = False  # ← NOVÝ FLAG


_VALID_DB_SCHEMES = ("postgresql://", "postgresql+", "sqlite://")


def get_database_path():
    """PostgreSQL ak DATABASE_URL env var existuje, inak SQLite."""
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        # Railway/Heroku používajú starý postgres:// prefix
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)

        # A Supabase/REST project URL (https://...) is a common mix-up here. SQLAlchemy
        # would try to load a dialect named after the scheme and fail deep inside the
        # engine, so reject it up front with an actionable message.
        if not db_url.startswith(_VALID_DB_SCHEMES):
            scheme = db_url.split("://", 1)[0] if "://" in db_url else db_url[:20]
            raise ValueError(
                f"DATABASE_URL has an unusable scheme '{scheme}://'. Expected a "
                "PostgreSQL connection string like "
                "'postgresql://user:password@host:5432/dbname'. A project's REST/API "
                "URL (https://...) is not a database connection string."
            )

        _db_logger.info("[DATABASE] Using PostgreSQL: %s", db_url.split("@")[-1][:40])
        return db_url

    # SQLite fallback pre lokálny vývoj
    base_dir = Path(__file__).parent.parent.parent
    db_dir = base_dir / "data" / "database"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "medical_ai.db"
    _db_logger.info("[DATABASE] Using SQLite: %s", db_path)
    return f"sqlite:///{db_path}"


def _get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        db_url = get_database_path()
        is_sqlite = db_url.startswith("sqlite")
        _engine = create_engine(
            db_url,
            echo=False,
            # SQLite potrebuje check_same_thread=False pre FastAPI async
            connect_args={"check_same_thread": False} if is_sqlite else {},
            # PostgreSQL connection pool settings
            pool_pre_ping=True,  # ← overí spojenie pred každým použitím
            pool_recycle=300,    # ← recykluj spojenia každých 5 minút
        )
        _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
        _db_logger.info("[DATABASE] Initialized at: %s", db_url[:50])
    return _engine


def init_database():
    """
    Vytvoriť všetky tabuľky ak neexistujú.
    MUSÍ byť zavolaná pred akýmikoľvek DB queries!
    Volá sa v lifespan() v main.py.
    """
    global _db_initialized
    engine = _get_engine()
    # checkfirst=True = nevyhadzuje error ak tabuľky už existujú
    Base.metadata.create_all(engine, checkfirst=True)
    _ensure_indexes(engine)
    _db_initialized = True
    _db_logger.info("[DATABASE] All tables created/verified")
    return engine


# create_all() only creates indexes alongside a new table, so existing
# deployments never get them. These are idempotent and cheap to re-run.
_INDEXES = (
    ("ix_apple_health_type_start", "apple_health_data", "record_type, start_date"),
    ("ix_apple_health_start", "apple_health_data", "start_date"),
    ("ix_health_records_patient_metric", "health_records", "patient_id, metric_type"),
    ("ix_health_records_date", "health_records", "record_date"),
    ("ix_health_records_source", "health_records", "source"),
)


def _ensure_indexes(engine):
    from sqlalchemy import text

    with engine.connect() as conn:
        for name, table, columns in _INDEXES:
            try:
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})"
                ))
                conn.commit()
            except Exception as e:
                _db_logger.warning("[DATABASE] Could not create index %s: %s", name, e)


def get_session():
    """
    Vráti novú DB session.
    Vždy zavri session po použití: session.close()
    Alebo použi ako context manager: with get_session() as session:
    """
    _get_engine()  # lazy init engine ak ešte neexistuje

    # ── KRITICKÝ FIX ──────────────────────────────────────────────────────────
    # Ak init_database() ešte nebola zavolaná (napr. pri module-level importoch
    # pred lifespan()), bezpečne vytvor tabuľky pred prvou query.
    if not _db_initialized:
        _db_logger.warning(
            "[DATABASE] get_session() called before init_database() — "
            "auto-initializing. Check module import order."
        )
        init_database()
    # ─────────────────────────────────────────────────────────────────────────

    return _SessionLocal()


def create_default_patient():
    """Vytvoriť defaultného pacienta ak žiadny neexistuje."""
    session = get_session()
    try:
        existing = session.query(Patient).first()
        if existing:
            return existing

        patient = Patient(
            first_name="Používateľ",
            last_name="MedicalAI",
            gender="other",
        )
        session.add(patient)
        session.commit()
        session.refresh(patient)
        _db_logger.info("[DATABASE] Created default patient with ID: %s", patient.id)
        return patient
    except Exception as e:
        session.rollback()
        _db_logger.error("[DATABASE] Failed to create default patient: %s", e)
        raise
    finally:
        session.close()


if __name__ == "__main__":
    init_database()
    create_default_patient()
