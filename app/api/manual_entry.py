from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import date, datetime
from app.database import get_session, Patient, FamilyMember, HealthRecord

router = APIRouter(prefix="/api/manual", tags=["manual-entry"])


# Pydantic models pre requesty
class FamilyMemberCreate(BaseModel):
    first_name: str
    last_name: str
    relationship_type: str  # mother, father, sister, brother, grandmother, grandfather
    date_of_birth: Optional[date] = None
    date_of_death: Optional[date] = None
    gender: str
    blood_type: Optional[str] = None
    
    chronic_conditions: Optional[List[str]] = []
    genetic_conditions: Optional[List[str]] = []
    allergies: Optional[List[str]] = []
    medications: Optional[List[Dict[str, str]]] = []
    surgeries: Optional[List[Dict[str, str]]] = []
    
    smoking: bool = False
    smoking_years: Optional[int] = None
    alcohol: bool = False
    exercise_frequency: Optional[str] = None
    
    cause_of_death: Optional[str] = None
    notes: Optional[str] = None


class FamilyMemberUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    relationship_type: Optional[str] = None
    date_of_birth: Optional[date] = None
    date_of_death: Optional[date] = None
    gender: Optional[str] = None
    blood_type: Optional[str] = None
    
    chronic_conditions: Optional[List[str]] = None
    genetic_conditions: Optional[List[str]] = None
    allergies: Optional[List[str]] = None
    medications: Optional[List[Dict[str, str]]] = None
    surgeries: Optional[List[Dict[str, str]]] = None
    
    smoking: Optional[bool] = None
    smoking_years: Optional[int] = None
    alcohol: Optional[bool] = None
    exercise_frequency: Optional[str] = None
    
    cause_of_death: Optional[str] = None
    notes: Optional[str] = None


class HealthRecordCreate(BaseModel):
    record_date: date
    metric_type: str  # glucose, blood_pressure, cholesterol, weight, etc.
    value: str  # "120/80", "5.4", "75.2"
    unit: Optional[str] = None
    reference_range: Optional[str] = None
    is_normal: Optional[bool] = None
    interpretation: Optional[str] = None
    doctor_name: Optional[str] = None
    facility_name: Optional[str] = None
    notes: Optional[str] = None


class PatientUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    blood_type: Optional[str] = None
    height_cm: Optional[float] = None
    email: Optional[str] = None
    phone: Optional[str] = None


# === PATIENT ENDPOINTS ===

@router.get("/patient")
async def get_patient_info():
    """Získať informácie o pacientovi"""
    session = get_session()
    try:
        patient = session.query(Patient).first()
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")
        
        return {
            "id": patient.id,
            "first_name": patient.first_name,
            "last_name": patient.last_name,
            "date_of_birth": patient.date_of_birth.isoformat() if patient.date_of_birth else None,
            "gender": patient.gender,
            "blood_type": patient.blood_type,
            "height_cm": patient.height_cm,
            "email": patient.email,
            "phone": patient.phone,
        }
    finally:
        session.close()


@router.put("/patient")
async def update_patient_info(data: PatientUpdate):
    """Aktualizovať informácie o pacientovi"""
    session = get_session()
    try:
        patient = session.query(Patient).first()
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")
        
        # Update fields
        if data.first_name is not None:
            patient.first_name = data.first_name
        if data.last_name is not None:
            patient.last_name = data.last_name
        if data.date_of_birth is not None:
            patient.date_of_birth = data.date_of_birth
        if data.gender is not None:
            patient.gender = data.gender
        if data.blood_type is not None:
            patient.blood_type = data.blood_type
        if data.height_cm is not None:
            patient.height_cm = data.height_cm
        if data.email is not None:
            patient.email = data.email
        if data.phone is not None:
            patient.phone = data.phone
        
        patient.updated_at = datetime.now()
        session.commit()
        
        return {"success": True, "message": "Patient info updated"}
    finally:
        session.close()


# === FAMILY MEMBER ENDPOINTS ===

@router.get("/family")
async def get_family_members():
    """Získať všetkých rodinných príbuzných"""
    session = get_session()
    try:
        patient = session.query(Patient).first()
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")
        
        members = session.query(FamilyMember).filter_by(patient_id=patient.id).all()
        
        result = []
        for member in members:
            result.append({
                "id": member.id,
                "first_name": member.first_name,
                "last_name": member.last_name,
                "relationship_type": member.relationship_type,
                "date_of_birth": member.date_of_birth.isoformat() if member.date_of_birth else None,
                "date_of_death": member.date_of_death.isoformat() if member.date_of_death else None,
                "gender": member.gender,
                "blood_type": member.blood_type,
                "chronic_conditions": member.chronic_conditions or [],
                "genetic_conditions": member.genetic_conditions or [],
                "allergies": member.allergies or [],
                "medications": member.medications or [],
                "surgeries": member.surgeries or [],
                "smoking": member.smoking,
                "smoking_years": member.smoking_years,
                "alcohol": member.alcohol,
                "exercise_frequency": member.exercise_frequency,
                "cause_of_death": member.cause_of_death,
                "notes": member.notes,
            })
        
        return result
    finally:
        session.close()


@router.post("/family")
async def add_family_member(data: FamilyMemberCreate):
    """Pridať rodinného príbuzného"""
    session = get_session()
    try:
        patient = session.query(Patient).first()
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")
        
        member = FamilyMember(
            patient_id=patient.id,
            first_name=data.first_name,
            last_name=data.last_name,
            relationship_type=data.relationship_type,
            date_of_birth=data.date_of_birth,
            date_of_death=data.date_of_death,
            gender=data.gender,
            blood_type=data.blood_type,
            chronic_conditions=data.chronic_conditions,
            genetic_conditions=data.genetic_conditions,
            allergies=data.allergies,
            medications=data.medications,
            surgeries=data.surgeries,
            smoking=data.smoking,
            smoking_years=data.smoking_years,
            alcohol=data.alcohol,
            exercise_frequency=data.exercise_frequency,
            cause_of_death=data.cause_of_death,
            notes=data.notes,
        )
        
        session.add(member)
        session.commit()
        
        return {
            "success": True,
            "message": f"Family member {data.first_name} {data.last_name} added",
            "id": member.id
        }
    finally:
        session.close()


@router.put("/family/{member_id}")
async def update_family_member(member_id: int, data: FamilyMemberUpdate):
    """Aktualizovať rodinného príbuzného"""
    session = get_session()
    try:
        member = session.query(FamilyMember).filter_by(id=member_id).first()
        if not member:
            raise HTTPException(status_code=404, detail="Family member not found")
        
        # Update fields
        if data.first_name is not None:
            member.first_name = data.first_name
        if data.last_name is not None:
            member.last_name = data.last_name
        if data.relationship_type is not None:
            member.relationship_type = data.relationship_type
        if data.date_of_birth is not None:
            member.date_of_birth = data.date_of_birth
        if data.date_of_death is not None:
            member.date_of_death = data.date_of_death
        if data.gender is not None:
            member.gender = data.gender
        if data.blood_type is not None:
            member.blood_type = data.blood_type
        if data.chronic_conditions is not None:
            member.chronic_conditions = data.chronic_conditions
        if data.genetic_conditions is not None:
            member.genetic_conditions = data.genetic_conditions
        if data.allergies is not None:
            member.allergies = data.allergies
        if data.medications is not None:
            member.medications = data.medications
        if data.surgeries is not None:
            member.surgeries = data.surgeries
        if data.smoking is not None:
            member.smoking = data.smoking
        if data.smoking_years is not None:
            member.smoking_years = data.smoking_years
        if data.alcohol is not None:
            member.alcohol = data.alcohol
        if data.exercise_frequency is not None:
            member.exercise_frequency = data.exercise_frequency
        if data.cause_of_death is not None:
            member.cause_of_death = data.cause_of_death
        if data.notes is not None:
            member.notes = data.notes
        
        member.updated_at = datetime.now()
        session.commit()
        
        return {"success": True, "message": "Family member updated"}
    finally:
        session.close()


@router.delete("/family/{member_id}")
async def delete_family_member(member_id: int):
    """Vymazať rodinného príbuzného"""
    session = get_session()
    try:
        member = session.query(FamilyMember).filter_by(id=member_id).first()
        if not member:
            raise HTTPException(status_code=404, detail="Family member not found")
        
        session.delete(member)
        session.commit()
        
        return {"success": True, "message": "Family member deleted"}
    finally:
        session.close()


# === HEALTH RECORD ENDPOINTS ===

@router.post("/health-record")
async def add_health_record(data: HealthRecordCreate):
    """Manuálne pridať zdravotný záznam"""
    session = get_session()
    try:
        patient = session.query(Patient).first()
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")
        
        record = HealthRecord(
            patient_id=patient.id,
            record_type="manual_entry",
            record_date=data.record_date,
            source="manual",
            metric_type=data.metric_type,
            value=data.value,
            unit=data.unit,
            reference_range=data.reference_range,
            is_normal=data.is_normal,
            interpretation=data.interpretation,
            doctor_name=data.doctor_name,
            facility_name=data.facility_name,
            notes=data.notes,
        )
        
        session.add(record)
        session.commit()
        
        return {
            "success": True,
            "message": f"Health record for {data.metric_type} added",
            "id": record.id
        }
    finally:
        session.close()


@router.get("/health-records")
async def get_health_records(metric_type: Optional[str] = None, limit: int = 100):
    """Získať zdravotné záznamy (s optional filtrom)"""
    session = get_session()
    try:
        patient = session.query(Patient).first()
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")
        
        query = session.query(HealthRecord).filter_by(patient_id=patient.id)
        
        if metric_type:
            query = query.filter_by(metric_type=metric_type)
        
        records = query.order_by(HealthRecord.record_date.desc()).limit(limit).all()
        
        result = []
        for record in records:
            result.append({
                "id": record.id,
                "record_date": record.record_date.isoformat(),
                "metric_type": record.metric_type,
                "value": record.value,
                "unit": record.unit,
                "reference_range": record.reference_range,
                "is_normal": record.is_normal,
                "interpretation": record.interpretation,
                "source": record.source,
                "doctor_name": record.doctor_name,
                "facility_name": record.facility_name,
                "notes": record.notes,
            })
        
        return result
    finally:
        session.close()


@router.delete("/health-record/{record_id}")
async def delete_health_record(record_id: int):
    """Vymazať zdravotný záznam"""
    session = get_session()
    try:
        record = session.query(HealthRecord).filter_by(id=record_id).first()
        if not record:
            raise HTTPException(status_code=404, detail="Health record not found")
        
        session.delete(record)
        session.commit()
        
        return {"success": True, "message": "Health record deleted"}
    finally:
        session.close()


# === UTILITY ENDPOINTS ===

@router.get("/genetic-risk-analysis")
async def analyze_genetic_risks():
    """Analyzovať genetické riziká na základe rodinnej anamnézy"""
    session = get_session()
    try:
        patient = session.query(Patient).first()
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")
        
        members = session.query(FamilyMember).filter_by(patient_id=patient.id).all()
        
        # Počítať výskyt chorôb v rodine
        condition_counts = {}
        genetic_conditions = {}
        
        for member in members:
            # Chronické choroby
            for condition in (member.chronic_conditions or []):
                condition_counts[condition] = condition_counts.get(condition, 0) + 1
            
            # Genetické choroby
            for condition in (member.genetic_conditions or []):
                genetic_conditions[condition] = genetic_conditions.get(condition, 0) + 1
        
        # Vypočítať riziká
        risks = []
        
        # Vysoké riziko = 2+ príbuzní s rovnakou chorobou
        for condition, count in condition_counts.items():
            risk_level = "low"
            if count >= 3:
                risk_level = "high"
            elif count >= 2:
                risk_level = "medium"
            
            risks.append({
                "condition": condition,
                "family_members_affected": count,
                "risk_level": risk_level,
                "type": "chronic"
            })
        
        # Genetické choroby = vždy vysoké riziko
        for condition, count in genetic_conditions.items():
            risks.append({
                "condition": condition,
                "family_members_affected": count,
                "risk_level": "high",
                "type": "genetic"
            })
        
        # Zoradiť podľa rizika
        risk_order = {"high": 0, "medium": 1, "low": 2}
        risks.sort(key=lambda x: (risk_order[x["risk_level"]], -x["family_members_affected"]))
        
        return {
            "total_family_members": len(members),
            "risks": risks,
            "summary": {
                "high_risk": len([r for r in risks if r["risk_level"] == "high"]),
                "medium_risk": len([r for r in risks if r["risk_level"] == "medium"]),
                "low_risk": len([r for r in risks if r["risk_level"] == "low"]),
            }
        }
    finally:
        session.close()
