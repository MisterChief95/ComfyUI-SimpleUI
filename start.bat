@echo off
rem Starts the backend (FastAPI, :8000) and the frontend dev server (Vite, :5173) in their own windows.
cd /d "%~dp0"
start "SimpleUI backend" cmd /k "backend\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --port 8000 --app-dir backend"
start "SimpleUI frontend" cmd /k "npm --prefix frontend run dev"
echo Backend:  http://localhost:8000
echo Frontend: http://localhost:5173
