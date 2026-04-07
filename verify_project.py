#!/usr/bin/env python3
"""
تحقق سريع: لا أخطاء نحو، Ruff نظيف، واستيراد الوحدات الأساسية.
تشغيل من جذر المشروع: python verify_project.py
"""
from __future__ import annotations

import py_compile
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _compile_all_py() -> bool:
    for path in ROOT.rglob("*.py"):
        if "dist" in path.parts:
            continue
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as e:
            print(f"compile error {path}: {e}", file=sys.stderr)
            return False
    return True


def main() -> int:
    errors: list[str] = []

    # 1) Ruff
    try:
        r = subprocess.run(
            [sys.executable, "-m", "ruff", "check", str(ROOT)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            errors.append("ruff check failed:\n" + (r.stdout or r.stderr or ""))
    except FileNotFoundError:
        errors.append("ruff غير مثبت — نفّذ: pip install -r requirements-dev.txt")

    # 2) تجميع كل .py ما عدا dist
    if not _compile_all_py():
        errors.append("py_compile: فشل تجميع بعض الملفات")

    # 3) استيراد حرج
    sys.path.insert(0, str(ROOT))
    try:
        import main  # noqa: F401
        import trading_panel  # noqa: F401
        import websocket_manager  # noqa: F401
        import bot_logic  # noqa: F401
    except Exception as e:
        errors.append(f"استيراد فشل: {e}")

    if errors:
        print("\n--- verify_project: فشل ---\n", file=sys.stderr)
        for e in errors:
            print(e, file=sys.stderr)
        return 1

    print("verify_project: OK — ruff + compile + imports")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
