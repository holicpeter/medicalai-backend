"""First steps for a new account: which of them the user has already done.

The dashboard shows a "Get started" checklist until all three are done:
a device or Apple Health feeding data in, results from a doctor (an uploaded
report or a value typed in by hand), and a first meal in the food diary.
"""
from fastapi import APIRouter, Depends

from app.auth.dependencies import get_current_patient_id, get_current_user
from app.database import (
    AppleHealthData, Document, HealthRecord, NutritionEntry, User, WearableConnection, get_session,
)
from app.integrations.withings_sync import SOURCE as WITHINGS_SOURCE

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

# Health records that come from a device rather than from a doctor.
_DEVICE_SOURCES = [WITHINGS_SOURCE]


@router.get("")
def onboarding_status(
    user: User = Depends(get_current_user),
    patient_id: int = Depends(get_current_patient_id),
):
    session = get_session()
    try:
        def exists(query) -> bool:
            return session.query(query.exists()).scalar()

        device = (
            exists(session.query(WearableConnection).filter(
                WearableConnection.user_id == user.id,
                WearableConnection.refresh_token_enc.isnot(None)))
            or exists(session.query(AppleHealthData).filter(AppleHealthData.patient_id == patient_id))
            or exists(session.query(HealthRecord).filter(
                HealthRecord.patient_id == patient_id, HealthRecord.source.in_(_DEVICE_SOURCES)))
        )
        results = (
            exists(session.query(HealthRecord).filter(
                HealthRecord.patient_id == patient_id, HealthRecord.source.notin_(_DEVICE_SOURCES)))
            or exists(session.query(Document).filter(Document.patient_id == patient_id))
        )
        meals = exists(session.query(NutritionEntry).filter(NutritionEntry.patient_id == patient_id))
        return {"device": device, "results": results, "meals": meals}
    finally:
        session.close()
