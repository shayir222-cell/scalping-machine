@echo off
echo Starting Scalping Machine...
cd /d %~dp0
.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8002 --reload
