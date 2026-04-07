# تنظيم المشروع — كل ملف له عمل واحد

## الوضع الحالي (ملفات في جذر واحد)

| الملف | الدور الرئيسي | ملاحظة |
|-------|----------------|--------|
| **main.py** | تشغيل التطبيق | ✓ واضح |
| **main_window.py** | النافذة الرئيسية وتجميع اللوحات | ✓ |
| **center_panel.py** | التبويبات السفلية (الشارت، المراكز، الملخص، إلخ) وربطها | ✓ لوحة واحدة |
| **trading_panel.py** | لوحة التداول: أزرار، سوق، توصية، إعدادات، أوامر، WebSocket... | ⚠ ملف كبير جداً — يجمع عدة مهام |
| **chart_panel.py** | لوحة الشارت + ربط المؤشرات | ✓ |
| **candlestick_widget.py** | رسم الشموع + أدوات الرسم (خط، قناة، فيبو) | لوحة شارت + رسم |
| **ai_dashboard.py** | ملخص الذكاء (التوصية، المخاطر، الشموع، إلخ) | ✓ |
| **ai_panel.py** | لوحة الذكاء (التوصية، الثقة، التحليل) | ✓ |
| **indicators_panel.py** | عرض المؤشرات (MACD, RSI, إلخ) | ✓ |
| **market_info_panel.py** | معلومات السوق | ✓ |
| **open_positions.py** | جدول المراكز المفتوحة | ✓ |
| **trade_history.py** | سجل الصفقات + السجل اليومي | سجلان في ملف واحد |
| **error_log_panel.py** | سجل الأخطاء | ✓ |
| **api_settings_window.py** | نافذة إعدادات API | ✓ |
| **risk_settings_window.py** | نافذة إعدادات المخاطر | ✓ |
| **quick_settings_dialogs.py** | نوافذ المبلغ، الرافعة، TP، SL، إلخ | ✓ |
| **translations.py** | النصوص عربي/إنجليزي | ✓ |
| **config.py** | الإعدادات (قراءة/حفظ) | ✓ |
| **exchange_binance.py** | اتصال Binance | ✓ |
| **exchange_bitget.py** | اتصال Bitget | ✓ |
| **websocket_manager.py** | WebSocket + شموع + مؤشرات + توصية أولية | ⚠ بيانات + منطق + مؤشرات |
| **websocket_client.py** | عميل WebSocket (إن وُجد) | ✓ |
| **format_utils.py** | تنسيق الأرقام والعملات | ✓ |
| **recommendation_log.py** | تسجيل التوصيات والتعلم | ✓ |
| **ml_model.py** | نموذج التعلم الآلي | ✓ |
| **candlestick_patterns.py** | أنماط الشموع | ✓ |
| **prediction_dot_widget.py** | ودجت نقطة التوقع | ✓ |
| **PredictionRing.py** | دائرة التوقع | ✓ |
| **trend_graph_widget.py** | رسم توقّع الاتجاه | ✓ |
| **symbol_fetcher.py** | جلب الرموز | ✓ |
| **symbol_selector.py** | اختيار الرمز | ✓ |
| **telegram_notifier.py** | إشعارات تيليجرام | ✓ |
| **theme_loader.py** | تحميل السمة | ✓ |
| **mode_toggle.py** | تبديل الوضع (وهمي/حقيقي) | ✓ |
| **searchable_combobox.py** | قائمة بحث | ✓ |

---

## لماذا يحدث الخلط؟

1. **trading_panel.py** يضم: واجهة التداول، قوائم، أوامر، ربط WebSocket، تحديث الأسعار، المراكز، التوصية، العدّادات... فكل تعديل فيه قد يمس أشياء أخرى.
2. **لا توجد مجلدات** — كل الملفات في جذر واحد، فلا يوجد فصل واضح بين "واجهة" و"منطق" و"اتصال".
3. **بعض الملفات تجمع مسؤوليتين** (مثل trade_history: سجل الصفقات + السجل اليومي).

---

## اقتراح: هيكل بحيث كل ملف له عمل واحد

```
trading/
├── main.py
├── config.py
├── translations.py
│
├── ui/                      # واجهة فقط
│   ├── main_window.py
│   ├── center_panel.py      # تبويبات سفلية فقط
│   ├── trading_panel.py     # يُخفَّف لاحقاً (انظر تحت)
│   ├── chart_panel.py
│   ├── panels/              # كل لوحة في ملف
│   │   ├── ai_dashboard.py
│   │   ├── ai_panel.py
│   │   ├── indicators_panel.py
│   │   ├── market_info_panel.py
│   │   ├── open_positions.py
│   │   ├── trade_history.py
│   │   ├── daily_log_panel.py   # فصل السجل اليومي
│   │   └── error_log_panel.py
│   ├── widgets/
│   │   ├── candlestick_widget.py
│   │   ├── prediction_dot_widget.py
│   │   ├── trend_graph_widget.py
│   │   └── searchable_combobox.py
│   └── windows/
│       ├── api_settings_window.py
│       ├── risk_settings_window.py
│       └── quick_settings_dialogs.py
│
├── core/                    # منطق التداول والبيانات
│   ├── exchange_binance.py
│   ├── exchange_bitget.py
│   ├── websocket_manager.py
│   ├── recommendation_log.py
│   └── ml_model.py
│
├── lib/                     # أدوات مشتركة
│   ├── format_utils.py
│   ├── candlestick_patterns.py
│   ├── symbol_fetcher.py
│   ├── symbol_selector.py
│   ├── telegram_notifier.py
│   └── theme_loader.py
│
└── PredictionRing.py        # أو نقله إلى ui/widgets/
```

ثم **تقسيم trading_panel.py** لاحقاً إلى مثلاً:
- `trading_panel.py` — الهيكل والأزرار والربط فقط
- `trading_orders.py` — منطق تنفيذ الأوامر (شراء/بيع/إغلاق)
- أو ترك الملف كما هو لكن توثيق أقسامه بوضوح داخل الملف

---

## ماذا نفعله الآن؟

- **بدون نقل ملفات:** يمكننا الاكتفاء بهذا المستند كمرجع، وعند أي تعديل نراجع الجدول أعلاه حتى لا نمس ملفاً أو مسؤولية غير مطلوبة.
- **مع تنظيم تدريجي:** نقل الملفات إلى مجلدات (ui/, core/, lib/) خطوة بخطوة مع تصحيح مسارات الاستيراد فقط، دون تغيير المنطق؛ ثم لاحقاً فصل المسؤوليات داخل الملفات الكبيرة (مثل trading_panel و websocket_manager).

إذا أردت، الخطوة التالية تكون: إنشاء مجلدات `ui/`, `core/`, `lib/` ونقل ملف واحد أو اثنين كتجربة (مثلاً `format_utils.py` → `lib/` وتحديث الـ imports في الملفات التي تستخدمه).
