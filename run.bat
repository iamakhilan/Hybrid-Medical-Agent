@echo off
title Hybrid Medical Agent Launcher
echo ====================================================
echo Starting Hybrid Medical Agent...
echo ====================================================

echo [1/2] Starting Cloud MCP Server on port 8000...
start "Cloud MCP Server (Port 8000)" cmd /k ""%~dp0.venv\Scripts\python.exe" -m cloud_mcp_server.server"

timeout /t 2 /nobreak >nul

echo [2/2] Starting MedAgent Web API and UI on port 7860...
start "MedAgent Web App (Port 7860)" cmd /k ""%~dp0.venv\Scripts\python.exe" -m hybrid_agent.api"

timeout /t 3 /nobreak >nul

echo Opening browser to http://127.0.0.1:7860 ...
start http://127.0.0.1:7860
