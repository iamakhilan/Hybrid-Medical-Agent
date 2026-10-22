@echo off
setlocal
cd /d "%~dp0"
title Hybrid Medical Agent Launcher

echo ====================================================
echo Starting Hybrid Medical Agent...
echo ====================================================

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found at .venv\Scripts\python.exe!
    echo Please make sure .venv is installed.
    pause
    exit /b 1
)

echo [1/2] Starting Cloud MCP Server on http://127.0.0.1:8000/mcp ...
start "Cloud MCP Server (Port 8000)" /D "%~dp0" cmd /k "title Cloud MCP Server (Port 8000) && .venv\Scripts\python.exe -m cloud_mcp_server.server"

ping -n 3 127.0.0.1 >nul

echo [2/2] Starting MedAgent Web API and UI on http://127.0.0.1:7860 ...
start "MedAgent Web App (Port 7860)" /D "%~dp0" cmd /k "title MedAgent Web App (Port 7860) && .venv\Scripts\python.exe -m hybrid_agent.api"

ping -n 4 127.0.0.1 >nul

echo Opening browser to http://127.0.0.1:7860 ...
start http://127.0.0.1:7860
