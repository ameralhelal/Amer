"""نسخ احتياطي للمشروع.

- إذا BACKUP_DISK = None: الحفظ داخل المشروع في _backups/<تاريخ>
- إذا حددت مساراً (مثل D:\\disk): الحفظ هناك كما لو نسخت على «قرص» منفصل
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

# مجلد على قرص آخر أو USB — مثال: Path(r"D:\disk") أو Path(r"E:\TradingBackup")
# اتركه None ليحفظ داخل المشروع: .../trading/_backups/
BACKUP_DISK: Path | None = None


def main() -> None:
    src = Path(__file__).resolve().parent
    if BACKUP_DISK is not None:
        backup_root = Path(BACKUP_DISK).expanduser().resolve()
    else:
        backup_root = src / "_backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = backup_root / f"trading_backup_{stamp}"

    def ignore(_path: str, names: list[str]) -> set[str]:
        out: set[str] = set()
        for n in names:
            if n == "_backups" or n == "__pycache__" or n.endswith(".pyc"):
                out.add(n)
        return out

    shutil.copytree(src, dest, ignore=ignore, dirs_exist_ok=False)
    print("تم النسخ إلى:", dest)


if __name__ == "__main__":
    main()
