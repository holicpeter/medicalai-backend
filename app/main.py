from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import List, Optional
import uvicorn
from pathlib import Path

from app.api import health, upload, analysis, predictions, chat, integrations, manual_entry, apple_health
from app.config import settings
from app.database import init_database, create_default_patient

app = FastAPI(
    title="MedicalAI API",
    description="API for medical health analysis and predictions",
    version="1.0.0"
)

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    """Initialize database and create default patient"""
    try:
        init_database()
        create_default_patient()
        print("[STARTUP] Database initialized successfully")
    except Exception as e:
        print(f"[STARTUP ERROR] Failed to initialize database: {e}")

# CORS middleware pre React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:4173",
        "https://medicalai.peterholic.com",
        "https://medicalai-app-lime.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, prefix="/api/health", tags=["health"])
app.include_router(upload.router, prefix="/api/upload", tags=["upload"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["analysis"])
app.include_router(predictions.router, prefix="/api/predictions", tags=["predictions"])
app.include_router(chat.router)
app.include_router(integrations.router)
app.include_router(manual_entry.router)
app.include_router(apple_health.router)

@app.get("/")
async def root():
    return {
        "message": "MedicalAI API",
        "version": "1.0.0",
        "status": "running"
    }

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.BACKEND_HOST,
        port=settings.BACKEND_PORT,
        reload=True
    )
