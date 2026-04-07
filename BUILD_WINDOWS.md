# بناء نسخة ويندوز للتوزيع (تحميل على الكمبيوتر)

## المتطلبات على جهاز **البناء** فقط

- Windows 10/11  
- [Python](https://www.python.org/downloads/) 3.10 أو أحدث (مع خيار **Add python.exe to PATH**)  
- اتصال إنترنت لتثبيت الحزم أول مرة  

## خطوة واحدة

من مجلد المشروع (`trading`) انقر نقراً مزدوجاً على:

`build_windows.bat`

أو من PowerShell / CMD:

```bat
cd مسار\المشروع\trading
build_windows.bat
```

## النتيجة

- يُنشأ المجلد: `dist\CryptoTrading\`
- الملف التنفيذي: `dist\CryptoTrading\CryptoTrading.exe`

**لتوزيع البرنامج:** انسخ **المجلد بأكمله** `CryptoTrading` (وليس الملف `.exe` وحده) إلى الجهاز الآخر. لا حاجة لتثبيت Python على أجهزة المستخدمين.

## ملاحظات

- أول تشغيل قد يكون أبطأ قليلاً (فك المكتبات).  
- مضاد الفيروسات قد يحذر من برامج PyInstaller غير الموقّعة — هذا شائع؛ يمكن لاحقاً توقيع الملف بشهادة **Code Signing**.  
- إذا فشل البناء بسبب مكتبة ناقصة، شغّل يدوياً مع إضافة استيراد مخفي، مثلاً:

  ```bat
  python -m PyInstaller ... --hidden-import اسم_الوحدة main.py
  ```

- لنسخة **ملف واحد** `.exe` (أبطأ عند الفتح):

  ```bat
  python -m PyInstaller --noconfirm --clean --windowed --onefile --name CryptoTrading ^
    --add-data "theme_dark.qss;." --add-data "theme_light.qss;." --collect-all PyQt6 main.py
  ```

## مثبّت رسمي (اختياري لاحقاً)

لتجربة «تثبيت نظامي» مع معالج (Next, Next): استخدم [Inno Setup](https://jrsoftware.org/isinfo.php) أو NSIS ووجّهه إلى محتويات `dist\CryptoTrading\`.
