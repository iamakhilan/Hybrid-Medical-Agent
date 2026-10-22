Set-Location $PSScriptRoot

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Error "Virtual environment not found at .venv\Scripts\python.exe"
    exit 1
}

# Launch Cloud MCP Server (Port 8000)
Write-Host "Starting Cloud MCP Server on http://127.0.0.1:8000/mcp ..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$PSScriptRoot'; & '.\.venv\Scripts\python.exe' -m cloud_mcp_server.server"

Start-Sleep -Seconds 2

# Launch Hybrid Agent Web API & UI (Port 7860)
Write-Host "Starting MedAgent Web UI on http://127.0.0.1:7860 ..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$PSScriptRoot'; & '.\.venv\Scripts\python.exe' -m hybrid_agent.api"

Start-Sleep -Seconds 3

# Open Web Browser
Start-Process "http://127.0.0.1:7860"
