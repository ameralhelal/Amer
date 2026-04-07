@echo off
REM ASCII only - parentheses in Arabic/numbered echo lines break "if (...)" blocks in cmd.exe
cd /d "%~dp0"
title CryptoWeb Hub
cls
echo.
echo ========================================
echo   CryptoWeb Hub - keep this window OPEN
echo ========================================
echo Folder: %CD%
echo Open in browser: http://127.0.0.1:8000
echo Do not paste URLs into this window - use Chrome or Edge address bar.
echo A browser tab may open automatically after a few seconds.
echo ========================================
echo.

start "" cmd /c "timeout /t 5 /nobreak >nul & start http://127.0.0.1:8000/"

where py >nul 2>&1
if %errorlevel%==0 (
  echo Using: py -3 -m uvicorn
  py -3 -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
  goto after_uv
)
where python >nul 2>&1
if %errorlevel%==0 (
  echo Using: python -m uvicorn
  python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
  goto after_uv
)

echo.
echo ERROR: Neither py nor python found in PATH.
echo Install Python from https://www.python.org/downloads/
echo Enable: Add python.exe to PATH during setup.
echo.
pause
exit /b 1

:after_uv
echo.
echo Server stopped or an error occurred above.
echo If you see "No module named uvicorn", run: install_hub_deps.bat
echo.
pause
