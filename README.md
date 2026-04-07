# Crypto Trading Platform

منصة تداول عملات رقمية بواجهة PyQt6 مع تحليل ذكي ومؤشرات فنية وربط Binance (Spot / Testnet).

## التثبيت

1. تثبيت Python 3.9 أو أحدث.
2. استنساخ المشروع أو فك الضغط ثم فتح مجلد المشروع في الطرفية:

```bash
cd trading
pip install -r requirements.txt
```

## التشغيل

```bash
python main.py
```

## الإعدادات الأساسية

- **API Settings:** من زر "API Settings" أدخل مفتاح API و Secret من [Binance](https://www.binance.com/) (أو Testnet للتجربة). يتم حفظها محلياً مع حماية بكلمة مرور.
- **Risk Settings:** حدد المبلغ لكل صفقة، الرافعة، وحد الخسارة اليومية (0 = معطّل).
- **وضع التداول:**
  - **REAL:** أوامر على المنصة الرئيسية (mainnet).
  - **TESTNET:** أوامر على testnet.binance (أموال تجريبية).

لا تضَع مفاتيح API في الكود أو الملفات المشتركة؛ استخدم دائماً نافذة الإعدادات.

## الملفات المحفوظة محلياً

تُحفظ الإعدادات وسجل الصفقات في مجلد المستخدم:

- Windows: `%APPDATA%\CryptoTrading`
- Linux/macOS: `~/CryptoTrading` (أو من متغير `HOME`)

## الترخيص

للاستخدام الشخصي والتعليمي. التداول يحمل مخاطر — استخدم Testnet للتجربة أولاً.
