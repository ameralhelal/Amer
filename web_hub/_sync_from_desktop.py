"""اختياري: مزامنة static/ و app/ من نسخة Desktop\\CryptoWebHub إن وُجدت (النسخة الرسمية الآن داخل trading/web_hub)."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DESKTOP_HUB = Path.home() / "Desktop" / "CryptoWebHub"
ALT = Path(r"c:\Users\amera\Desktop\CryptoWebHub")


def main() -> int:
    src = DESKTOP_HUB if DESKTOP_HUB.is_dir() else ALT
    if not src.is_dir():
        print("المصدر غير موجود:", src, file=sys.stderr)
        return 1
    static_src = src / "static"
    if not static_src.is_dir():
        print("لا يوجد static في", src, file=sys.stderr)
        return 1
    dst_static = HERE / "static"
    dst_static.mkdir(parents=True, exist_ok=True)
    shutil.copytree(static_src, dst_static, dirs_exist_ok=True)
    app_dst = HERE / "app"
    app_dst.mkdir(parents=True, exist_ok=True)
    for name in ("main.py", "__init__.py"):
        p = src / "app" / name
        if p.is_file():
            shutil.copy2(p, app_dst / name)
    req = src / "requirements.txt"
    if req.is_file():
        shutil.copy2(req, HERE / "requirements.txt")
    print("تم النسخ من", src, "إلى", HERE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
