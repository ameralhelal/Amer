"""
ملف توافق (compatibility):
كان يوجد MiddlePanel سابقاً، وتم توحيد النظام الآن على CenterPanel.
نُبقي هذا الملف لتجنب كسر أي استيرادات قديمة.
"""

from center_panel import CenterPanel


class MiddlePanel(CenterPanel):
    pass
