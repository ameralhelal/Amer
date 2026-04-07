@echo off
cd /d "%~dp0"
echo Installing packages from requirements.txt ...
echo.
where py >nul 2>&1
if %errorlevel%==0 (
  py -3 -m pip install -r requirements.txt
  goto done
)
where python >nul 2>&1
if %errorlevel%==0 (
  python -m pip install -r requirements.txt
  goto done
)
echo ERROR: Neither py nor python found in PATH.
:done
echo.
pause
