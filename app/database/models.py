"""
Database models pre MedicalAI
Lokálna SQLite databáza - všetky dáta zostávajú na vašom PC!
"""
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, Text, Boolean, ForeignKey, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import os
from pathlib import Path

# Base class pre všetky modely
Base = declarative_base()


class Patient(Base):
    """Hlavný pacient (vlastník aplikácie)"""
    __tablename__ = 'patients'
    
    id = Column(Integer, primary_key=True)
    first_name = Column(String(100))
    last_name = Column(String(100))
    date_of_birth = Column(Date)
    gender = Column(String(10))  # male, female, other
    blood_type = Column(String(5))  # A+, B-, AB+, O-, atď.
    height_cm = Column(Float)
    email = Column(String(200))
    phone = Column(String(50))
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    # Relationships
    health_records = relationship("HealthRecord", back_populates="patient")
    family_members = relationship("FamilyMember", back_populates="patient")


class FamilyMember(Base):
    """Rodinní príbuzní (rodičia, súrodenci, deti, prarodičia)"""
    __tablename__ = 'family_members'
    
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey('patients.id'))
    
    # Osobné údaje
    first_name = Column(String(100))
    last_name = Column(String(100))
    relationship_type = Column(String(50))  # mother, father, sister, brother, grandmother, grandfather, child
    date_of_birth = Column(Date, nullable=True)
    date_of_death = Column(Date, nullable=True)
    gender = Column(String(10))
    blood_type = Column(String(5), nullable=True)
    
    # Zdravotná anamnéza
    chronic_conditions = Column(JSON)  # ["diabetes", "hypertension", ...]
    genetic_conditions = Column(JSON)  # ["hemophilia", "sickle cell", ...]
    allergies = Column(JSON)  # ["penicillin", "peanuts", ...]
    medications = Column(JSON)  # [{"name": "...", "dosage": "..."}, ...]
    surgeries = Column(JSON)  # [{"type": "...", "date": "...", "notes": "..."}, ...]
    
    # Životný štýl
    smoking = Column(Boolean, default=False)
    smoking_years = Column(Integer, nullable=True)
    alcohol = Column(Boolean, default=False)
    exercise_frequency = Column(String(50), nullable=True)  # daily, weekly, rarely, never
    
    # Príčina smrti (ak relevantné)
    cause_of_death = Column(String(200), nullable=True)
    
    # Poznámky
    notes = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    # Relationships
    patient = relationship("Patient", back_populates="family_members")


class HealthRecord(Base):
    """Zdravotné záznamy - z dokumentov alebo manuálne"""
    __tablename__ = 'health_records'
    
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey('patients.id'))
    
    # Metadata
    record_type = Column(String(50))  # lab_test, checkup, prescription, imaging, manual_entry
    record_date = Column(Date)
    source = Column(String(100))  # ocr, manual, import
    source_file = Column(String(500), nullable=True)
    
    # Data
    metric_type = Column(String(100))  # glucose, blood_pressure, cholesterol, etc.
    value = Column(String(50))  # Hodnota ako string (môže byť "120/80", "5.4", atď.)
    unit = Column(String(20), nullable=True)
    reference_range = Column(String(100), nullable=True)  # "3.9-6.1" alebo "<5.0"
    
    # Interpretácia
    is_normal = Column(Boolean, nullable=True)
    interpretation = Column(String(50), nullable=True)  # normal, high, low, critical
    
    # Kontext
    doctor_name = Column(String(200), nullable=True)
    facility_name = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    # Relationships
    patient = relationship("Patient", back_populates="health_records")


class Document(Base):
    """Nahrané dokumenty (PDF, obrázky)"""
    __tablename__ = 'documents'
    
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey('patients.id'), nullable=True)
    
    # File info
    filename = Column(String(500))
    file_path = Column(String(1000))
    file_type = Column(String(50))  # pdf, jpg, png
    file_size_bytes = Column(Integer)
    
    # Processing
    ocr_processed = Column(Boolean, default=False)
    ocr_text = Column(Text, nullable=True)
    processing_status = Column(String(50))  # pending, processing, completed, failed
    processing_error = Column(Text, nullable=True)
    
    # Metadata
    document_type = Column(String(100), nullable=True)  # lab_results, prescription, checkup_summary
    document_date = Column(Date, nullable=True)
    
    # Timestamps
    uploaded_at = Column(DateTime, default=datetime.now)
    processed_at = Column(DateTime, nullable=True)


class GarminData(Base):
    """Dáta z Garmin hodinek"""
    __tablename__ = 'garmin_data'
    
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey('patients.id'))
    
    # Dátum merania
    record_date = Column(Date, unique=True)
    
    # Srdce
    resting_heart_rate = Column(Integer, nullable=True)
    max_heart_rate = Column(Integer, nullable=True)
    min_heart_rate = Column(Integer, nullable=True)
    avg_heart_rate = Column(Integer, nullable=True)
    
    # Spánok
    total_sleep_seconds = Column(Integer, nullable=True)
    deep_sleep_seconds = Column(Integer, nullable=True)
    light_sleep_seconds = Column(Integer, nullable=True)
    rem_sleep_seconds = Column(Integer, nullable=True)
    awake_seconds = Column(Integer, nullable=True)
    sleep_score = Column(Integer, nullable=True)
    
    # Stres
    avg_stress_level = Column(Integer, nullable=True)
    max_stress_level = Column(Integer, nullable=True)
    
    # Aktivita
    total_steps = Column(Integer, nullable=True)
    total_distance_meters = Column(Integer, nullable=True)
    active_calories = Column(Integer, nullable=True)
    
    # Telesné zloženie
    weight_kg = Column(Float, nullable=True)
    bmi = Column(Float, nullable=True)
    body_fat_percentage = Column(Float, nullable=True)
    
    # Timestamps
    synced_at = Column(DateTime, default=datetime.now)


class CalendarEvent(Base):
    """Udalosti z Google Calendar"""
    __tablename__ = 'calendar_events'
    
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey('patients.id'))
    
    # Google Calendar ID
    google_event_id = Column(String(200), unique=True)
    
    # Udalosť
    summary = Column(String(500))
    description = Column(Text, nullable=True)
    location = Column(String(500), nullable=True)
    
    # Čas
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    is_all_day = Column(Boolean, default=False)
    
    # Kategória (auto-detekovaná)
    category = Column(String(50), nullable=True)  # work, sport, health, travel, social
    
    # Timestamps
    synced_at = Column(DateTime, default=datetime.now)


class AppleHealthData(Base):
    """Apple Health dáta z iPhone"""
    __tablename__ = 'apple_health_data'
    
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey('patients.id'), default=1)
    
    # Typ záznamu (z Apple Health)
    record_type = Column(String(200))  # HKQuantityTypeIdentifierStepCount, HKQuantityTypeIdentifierHeartRate, atď.
    
    # Hodnota
    value = Column(Float)
    unit = Column(String(50))  # count, bpm, kg, m, atď.
    
    # Čas
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    creation_date = Column(DateTime, nullable=True)
    
    # Zdroj (ktorá aplikácia zaznamenala)
    source_name = Column(String(200), nullable=True)  # "iPhone", "Apple Watch", "Health Mate", atď.
    source_version = Column(String(100), nullable=True)
    device_name = Column(String(200), nullable=True)
    device_manufacturer = Column(String(100), nullable=True)
    device_model = Column(String(100), nullable=True)
    device_hardware = Column(String(100), nullable=True)
    device_software = Column(String(100), nullable=True)
    
    # Metadata (dodatočné info)
    record_metadata = Column(JSON, nullable=True)  # Extra info ako HKMetadataKey*
    
    # Import info
    imported_at = Column(DateTime, default=datetime.now)
    import_batch_id = Column(String(50), nullable=True)  # ID dávky importu (rovnaký export)
    
    # Relationship
    patient = relationship("Patient")


# Database setup funkcie
def get_database_path():
    """Získať cestu k databáze"""
    # Databáza bude v backend/data/medical_ai.db
    base_dir = Path(__file__).parent.parent.parent  # backend/
    db_dir = base_dir / "data" / "database"
    db_dir.mkdir(parents=True, exist_ok=True)
    
    db_path = db_dir / "medical_ai.db"
    return f"sqlite:///{db_path}"


def init_database():
    """Inicializovať databázu - vytvoriť všetky tabuľky"""
    engine = create_engine(get_database_path(), echo=False)
    Base.metadata.create_all(engine)
    print(f"[DATABASE] Initialized at: {get_database_path()}")
    return engine


def get_session():
    """Získať databázovú session"""
    engine = create_engine(get_database_path(), echo=False)
    Session = sessionmaker(bind=engine)
    return Session()


# Convenience funkcie
def create_default_patient():
    """Vytvoriť defaultného pacienta (prvé spustenie)"""
    session = get_session()
    
    # Skontrolovať, či už pacient existuje
    existing = session.query(Patient).first()
    if existing:
        session.close()
        return existing
    
    # Vytvoriť nového
    patient = Patient(
        first_name="Používateľ",
        last_name="MedicalAI",
        gender="other"
    )
    
    session.add(patient)
    session.commit()
    
    patient_id = patient.id
    session.close()
    
    print(f"[DATABASE] Created default patient with ID: {patient_id}")
    return patient


if __name__ == "__main__":
    # Test - vytvoriť databázu
    print("Creating database...")
    init_database()
    create_default_patient()
    print("Done!")
