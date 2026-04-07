"""
طبقة تنفيذ الأوامر على المنصات — منفصلة عن واجهة التداول.

المرحلة الحالية: عمال الخيوط (OrderWorker / ClosePositionWorker / EtoroResolvePositionWorker).
لاحقاً: بوابة موحّدة يستدعيها trading_panel فقط (بدون تكرار منطق المنصة هناك).
"""

from .workers import (
    ClosePositionWorker,
    EtoroResolvePositionWorker,
    OrderWorker,
)

__all__ = [
    "ClosePositionWorker",
    "EtoroResolvePositionWorker",
    "OrderWorker",
]
