"""
Simplified main file for testing without OCR
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="MedicalAI API - Test Mode",
    description="Simplified API for testing",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {
        "message": "MedicalAI API - Test Mode",
        "version": "1.0.0",
        "status": "running"
    }

@app.get("/api/health/status")
async def health_status():
    return {
        "status": "healthy",
        "service": "MedicalAI Backend"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
