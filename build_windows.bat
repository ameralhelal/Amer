@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1/3] تثبيت الاعتماديات...
python -m pip install -q -r requirements.txt -r requirements-build.txt
if errorlevel 1 (
  echo فشل pip. تأكد من تثبيت Python 3.10+ وإضافته لـ PATH.
  pause
  exit /b 1
)

echo [2/3] تجميع PyInstaller (قد يستغرق عدة دقائق)...
python -m PyInstaller --noconfirm CryptoTrading.spec
if errorlevel 1 (
  echo فشل PyInstaller.
  pause
  exit /b 1
)

echo.
echo [3/3] تم.
echo المجلد الجاهز للنسخ:  dist\CryptoTrading\
echo شغّل:  dist\CryptoTrading\CryptoTrading.exe
echo يمكنك ضغط مجلد CryptoTrading كاملاً في ZIP لنقله لجهاز آخر.
echo.
pause
