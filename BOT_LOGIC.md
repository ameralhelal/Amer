# منطق البوت — ترتيب الفلاتر والقرارات

القرار النهائي للتنفيذ من **توصية اللوحة** يمر بـ `bot_logic.decide()` فقط. التنفيذ الفعلي في `trading_panel.on_ai_recommendation`.

**مسارات أخرى (لا تمرّ بـ `decide`):** تحديث السعر كل ~500ms يشغّل `_check_stop_loss`، `_check_limit_sell`، `_check_trailing_stop`، `_check_sell_at_peak`، `_check_sell_at_overbought`، `_check_limit_buy` — تعتمد على `bot_auto_sl` / `bot_auto_sell` / `_bot_enabled` حسب كل دالة. إن كانت `sell_conditions` **غير فارغة** في `config`، يُنفَّذ فقط المسار المقابل لكل معرّف في القائمة (قائمة بيضاء).

## ترتيب `bot_logic.decide()` (حسب الكود الحالي)

| # | الخطوة | الشرط | النتيجة |
|---|--------|--------|---------|
| — | MTF | توصية BUY/SELL | تعديل طفيف على الثقة حسب `mtf_bias` |
| — | مركّب | `bot_merge_composite` + `composite_score` | فيتو أو تعديل ثقة (افتراضي الإعداد: دمج معطّل) |
| — | تثبيت | — | ضبط الثقة بين 0 و 100 |
| 1 | نوع التوصية | ليست BUY ولا SELL | `skip`: انتظار — نحتاج شراء/بيع |
| 2 | حد الثقة | الثقة < `bot_confidence_min` | `skip` |
| 3 | حد المراكز | BUY وعدد الصفوف ≥ `bot_max_open_trades` | `skip` |
| 3b | حد للرمز | BUY و`max_trades_per_symbol` ووصل الحد | `skip` |
| 4 | بيع بلا مركز | SELL ولا يوجد مركز | `skip` |
| 5 | بيع تلقائي | SELL و`bot_auto_sell` = false | `skip` مع رسالة واضحة (لم يعد صمتاً) |
| 5.0 | حاجز هدف صف | SELL و`take_profit_barrier` | `skip`: هدف صف في الجدول لم يتحقق |
| 5.1 | انتظار حد بيع مع SELL | `limit_sell_blocks_until_target` والسعر تحت الهدف | `skip` |
| 6 | سعر | لا سعر صالح للشراء | `skip` (ينطبق قبل فلاتر الشراء؛ للبيع يُفضّل وجود سعر أيضاً) |
| 7 | شروط شراء | BUY وقائمة الشروط/الشموع/VWAP/MTF | `skip` حسب الشرط |
| 8 | خسارة يومية | تجاوز الحد | `skip` |
| 9 | حد شراء يومي | BUY وتجاوز `max_trades_per_day` | `skip` |

نجاح كل الخطوات → `(BUY أو SELL, confidence, None)`.

## إعدادات شائعة

- `bot_auto_sell`: **معطّل** = لا بيع من **توصية** SELL؛ قد يبقى البيع من **حد بيع السعر** / **تتبع** / **SL** إن كانت مفعّلة وممرّرة من مسار السعر.
- `limit_sell_blocks_until_target`: يربط **توصية** SELL بتحقق **حد البيع العام** في الإعدادات؛ إن كان `limit_sell_value = 0` فلا هدف لحجب الإشارة (يُسجّل تحذير في السجل).

## قاعدة التعديل

- منطق «متى يمرّر البوت توصية اللوحة» → `bot_logic.py`.
- تنفيذ الأوامر، المؤقت 500ms، eToro، الجداول → `trading_panel.py` وغيره.

## تناقضات تم توضيحها في الواجهة

- **`sell_conditions`:** تُقرأ في `trading_panel` مع مؤقت السعر — قائمة فارغة = كل المسارات؛ غير فارغة = فقط `take_profit` / `limit_sell` / `trailing_stop` / `sell_at_peak` / `sell_at_overbought` / `stop_loss` المدرَجة. لا تمرّ بـ `decide`. انظر تلميح «شروط البيع» في المخاطر.
- **`load_config()`:** كان يتجاهل أي مفتاح في الملف غير موجود في `DEFAULTS` (مثل `first_real_order_done`) فيُفقد عند كل تشغيل — **تم الإصلاح** بدمج المفاتيح الإضافية من الملف.
