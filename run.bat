@echo off
cd /d %~dp0
if exist .env (for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do if not "%%b"=="" set "%%a=%%b")
if exist mima.txt set "DENGJIE_DEMO_CREDS=%~dp0mima.txt"
python -m uvicorn app.main:app --reload --port 8000
