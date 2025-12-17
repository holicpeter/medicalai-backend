# MedicalAI - Daily Health Auto Import
# Automaticky kontroluje a importuje nové Apple Health dáta

# Nastavte kódovanie konzoly na UTF-8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Cesta k iCloud priečinku
$icloudFolder = "C:\Users\holic\iCloudDrive\MedicalAI\exports"
$logFile = "C:\Users\holic\.gemini\antigravity\scratch\medical-ai\MedicalAI\backend\health_auto_import.log"

# Log funkcia
function Write-Log {
    param($Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$timestamp - $Message" | Out-File -FilePath $logFile -Append -Encoding UTF8
    Write-Host "$timestamp - $Message"
}

Write-Log "=========================================="
Write-Log "Health Auto Import Check Started"
Write-Log "=========================================="

# Skontroluj, či existuje iCloud priečinok
if (-not (Test-Path $icloudFolder)) {
    Write-Log "ERROR: iCloud folder not found: $icloudFolder"
    Write-Log "Creating folder..."
    New-Item -ItemType Directory -Path $icloudFolder -Force | Out-Null
    Write-Log "OK: Folder created"
}

# Nájdi dnešné súbory (vytvorené dnes)
$today = Get-Date -Format "yyyy-MM-dd"
$todaysFiles = Get-ChildItem -Path $icloudFolder -Filter "*.xml" | Where-Object {
    $_.LastWriteTime.Date -eq (Get-Date).Date
}

# Ak nie sú dnešné, skús včerajšie (pre prípad, že export beží neskoro)
if ($todaysFiles.Count -eq 0) {
    $todaysFiles = Get-ChildItem -Path $icloudFolder -Filter "*.xml" | Where-Object {
        $_.LastWriteTime.Date -eq (Get-Date).AddDays(-1).Date
    }
}

if ($todaysFiles.Count -eq 0) {
    Write-Log "INFO: No new files for today ($today)"
    Write-Log "WARN: Expected health export file not found. Check iPhone Health Auto Export app."
    Write-Log "=========================================="
    exit 0
}

Write-Log "INFO: Found $($todaysFiles.Count) file(s) to process"

# Skontroluj, či backend beží
try {
    $backendCheck = Invoke-WebRequest -Uri "http://localhost:8000/docs" -Method Get -TimeoutSec 5 -UseBasicParsing
    Write-Log "OK: Backend is running"
}
catch {
    Write-Log "ERROR: Backend is NOT running!"
    Write-Log "Please start backend: cd backend && uvicorn app.main:app --reload"
    Write-Log "=========================================="
    exit 1
}

foreach ($file in $todaysFiles) {
    Write-Log "INFO: Processing: $($file.Name) ($([math]::Round($file.Length / 1MB, 2)) MB)"
    
    # Zavolaj backend API na import
    try {
        $apiUrl = "http://localhost:8000/api/apple-health/import"
        
        # Vytvor multipart form data
        Add-Type -AssemblyName System.Net.Http
        
        $httpClient = New-Object System.Net.Http.HttpClient
        $httpClient.Timeout = [TimeSpan]::FromMinutes(30)  # 30 minút timeout
        
        $multipart = New-Object System.Net.Http.MultipartFormDataContent
        $fileStream = [System.IO.File]::OpenRead($file.FullName)
        $fileContent = New-Object System.Net.Http.StreamContent($fileStream)
        $fileContent.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse("application/xml")
        $multipart.Add($fileContent, "file", $file.Name)
        
        Write-Log "INFO: Uploading to backend..."
        
        $response = $httpClient.PostAsync($apiUrl, $multipart).Result
        $responseContent = $response.Content.ReadAsStringAsync().Result
        
        $fileStream.Close()
        $httpClient.Dispose()
        
        if ($response.IsSuccessStatusCode) {
            $result = $responseContent | ConvertFrom-Json
            
            Write-Log "SUCCESS: Import completed"
            Write-Log "  - Total records: $($result.stats.total_records)"
            Write-Log "  - New records imported: $($result.stats.saved)"
            Write-Log "  - Duplicates skipped: $($result.stats.duplicates)"
            Write-Log "  - Date range: $($result.stats.date_range.start) to $($result.stats.date_range.end)"
            
            # Voliteľne: Vymaž súbor po úspešnom importe (ušetrí miesto)
            # Remove-Item $file.FullName -Force
            # Write-Log "INFO: File deleted: $($file.Name)"
        }
        else {
            Write-Log "ERROR: Import failed (HTTP $($response.StatusCode))"
            Write-Log "Response: $responseContent"
        }
    }
    catch {
        Write-Log "ERROR: Exception during import"
        Write-Log "  $($_.Exception.Message)"
        if ($_.Exception.InnerException) {
            Write-Log "  Inner: $($_.Exception.InnerException.Message)"
        }
    }
}

Write-Log "=========================================="
Write-Log "Health Auto Import Check Completed"
Write-Log "=========================================="
