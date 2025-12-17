# PowerShell startup script for backend
Write-Host "🏥 Starting MedicalAI Backend..." -ForegroundColor Green

# Check if virtual environment exists
if (-Not (Test-Path ".\venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    py -m venv venv
}

# Activate virtual environment
Write-Host "Activating virtual environment..." -ForegroundColor Yellow
.\venv\Scripts\Activate.ps1

# Check if .env exists
if (-Not (Test-Path ".\.env")) {
    Write-Host "Creating .env file from template..." -ForegroundColor Yellow
    Copy-Item ..\.env.example .env
    Write-Host "Please edit .env file with your settings!" -ForegroundColor Red
}

# Install/update dependencies
Write-Host "Checking dependencies..." -ForegroundColor Yellow
pip install -r requirements.txt --quiet

# Start backend
Write-Host "Starting FastAPI server on http://localhost:8000" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop" -ForegroundColor Cyan
py -m app.main
