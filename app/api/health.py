from fastapi import APIRouter

router = APIRouter()

@router.get("/status")
async def health_status():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "MedicalAI Backend"
    }
