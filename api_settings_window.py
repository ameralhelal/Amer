# api_settings_window.py — إعدادات API مع حماية بكلمة مرور وتشفير الحفظ
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QMessageBox, QGroupBox,
)

from ui_messages import show_auto_close_message

log = logging.getLogger("trading.settings")

# تخزين مؤقت للمفاتيح المفكوكة خلال الجلسة — لا يُطلب كلمة المرور في كل أمر
_session_credentials: tuple[str, str, str, str] | None = None  # (main_key, main_secret, test_key, test_secret)


def clear_credentials_cache():
    """مسح التخزين المؤقت (بعد حفظ إعدادات API جديدة)."""
    global _session_credentials
    _session_credentials = None


def _settings_path():
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or "."
    folder = os.path.join(base, "CryptoTrading")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "api_settings.json")


def _get_salt_hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000, dklen=32)


def _encrypt(plain: str, password: str, salt: bytes) -> str:
    key = _get_salt_hash(password, salt)
    data = plain.encode("utf-8")
    out = bytes(data[i] ^ key[i % 32] for i in range(len(data)))
    return base64.b64encode(out).decode("ascii")


def _decrypt(b64: str, password: str, salt: bytes) -> str:
    key = _get_salt_hash(password, salt)
    data = base64.b64decode(b64.encode("ascii"))
    out = bytes(data[i] ^ key[i % 32] for i in range(len(data)))
    return out.decode("utf-8")


def _read_raw():
    path = _settings_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _is_encrypted(data: dict) -> bool:
    return data is not None and "salt_b64" in data and "hash_b64" in data and "data_b64" in data


def _get_mainnet_testnet_from_raw(raw: dict) -> tuple[str, str, str, str]:
    """استخراج مفاتيح Mainnet و Testnet من ملف غير مشفّر. توافق مع الملفات القديمة (api_key فقط = mainnet)."""
    main_key = raw.get("api_key", "")
    main_secret = raw.get("api_secret", "")
    test_key = raw.get("testnet_api_key", "")
    test_secret = raw.get("testnet_api_secret", "")
    return main_key, main_secret, test_key, test_secret


def load_api_settings(mode: str = "mainnet"):
    """تحميل المفتاح حسب الوضع: mainnet = تداول حقيقي، testnet = تداول وهمي. إن كان الملف مشفّراً تُرجع قيم فارغة."""
    raw = _read_raw()
    if raw is None:
        return "", ""
    if _is_encrypted(raw):
        return "", ""
    mk, ms, tk, ts = _get_mainnet_testnet_from_raw(raw)
    if mode == "testnet":
        return tk or "", ts or ""
    return mk or "", ms or ""


def load_api_settings_all() -> tuple[str, str, str, str] | None:
    """تحميل كل المفاتيح (mainnet_key, mainnet_secret, testnet_key, testnet_secret). إن كان مشفّراً تُرجع None."""
    raw = _read_raw()
    if raw is None or _is_encrypted(raw):
        return None
    return _get_mainnet_testnet_from_raw(raw)


def save_api_settings(
    api_key: str,
    api_secret: str,
    testnet_api_key: str = "",
    testnet_api_secret: str = "",
    etoro_user_key: str | None = None,
    etoro_api_key: str | None = None,
):
    """حفظ بدون تشفير. يدعم مفاتيح Mainnet و Testnet و eToro."""
    path = _settings_path()
    try:
        raw = _read_raw() if os.path.isfile(path) else None
        if raw and _is_encrypted(raw):
            raw = None
        data = dict(raw) if raw else {}
        data["api_key"] = api_key
        data["api_secret"] = api_secret
        if testnet_api_key or testnet_api_secret:
            data["testnet_api_key"] = testnet_api_key
            data["testnet_api_secret"] = testnet_api_secret
        if etoro_user_key is not None:
            data["etoro_user_key"] = etoro_user_key
        if etoro_api_key is not None:
            data["etoro_api_key"] = etoro_api_key
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        log.info("API settings saved (plain)")
    except Exception:
        log.exception("Could not save API settings")


def load_etoro_settings() -> tuple[str, str]:
    """تحميل مفاتيح eToro (User Key، API Key). من الإعدادات > Trading > API Key Management."""
    raw = _read_raw()
    if raw is None or _is_encrypted(raw):
        return "", ""
    return (
        (raw.get("etoro_user_key") or "").strip(),
        (raw.get("etoro_api_key") or "").strip(),
    )


def save_api_settings_encrypted(
    mainnet_key: str, mainnet_secret: str,
    testnet_key: str, testnet_secret: str,
    password: str,
):
    """حفظ مشفّر: مفاتيح التداول الحقيقي والوهمي."""
    path = _settings_path()
    salt = secrets.token_bytes(16)
    payload = json.dumps({
        "api_key": mainnet_key,
        "api_secret": mainnet_secret,
        "testnet_api_key": testnet_key,
        "testnet_api_secret": testnet_secret,
    })
    stored_hash = _get_salt_hash(password, salt)
    encrypted = _encrypt(payload, password, salt)
    data = {
        "salt_b64": base64.b64encode(salt).decode("ascii"),
        "hash_b64": base64.b64encode(stored_hash).decode("ascii"),
        "data_b64": encrypted,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        log.info("API settings saved (encrypted)")
    except Exception:
        log.exception("Could not save API settings")


def verify_and_decrypt(password: str) -> tuple[str, str, str, str] | None:
    """التحقق من كلمة المرور وفك التشفير. تُرجع (mainnet_key, mainnet_secret, testnet_key, testnet_secret) أو None."""
    raw = _read_raw()
    if not raw or not _is_encrypted(raw):
        return None
    salt = base64.b64decode(raw["salt_b64"].encode("ascii"))
    stored = base64.b64decode(raw["hash_b64"].encode("ascii"))
    current = _get_salt_hash(password, salt)
    if current != stored:
        return None
    try:
        payload = _decrypt(raw["data_b64"], password, salt)
        d = json.loads(payload)
        return (
            d.get("api_key", ""),
            d.get("api_secret", ""),
            d.get("testnet_api_key", ""),
            d.get("testnet_api_secret", ""),
        )
    except Exception:
        return None


def request_unlock_or_set_password(parent) -> tuple[str, str, str, str, str] | None:
    """إن كان الملف مشفّراً: طلب كلمة المرور لفتحه. غير مشفّر: تحميل المفاتيح مباشرة. تُرجع (password, mk, ms, tk, ts) أو None."""
    raw = _read_raw()
    if raw is not None and _is_encrypted(raw):
        for _ in range(3):
            dlg = _UnlockDialog(parent, title="الإعدادات مشفّرة — أدخل كلمة المرور لفتحها")
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return None
            pwd = dlg.password()
            result = verify_and_decrypt(pwd)
            if result is not None:
                return (pwd, result[0], result[1], result[2], result[3])
            show_auto_close_message(
                parent,
                "كلمة المرور",
                "كلمة المرور غير صحيحة. جرّب مرة أخرى.",
                icon=QMessageBox.Icon.Warning,
                timeout_ms=5000,
            )
        return None
    all_keys = load_api_settings_all()
    if all_keys is None:
        return ("", "", "", "", "")
    return ("", all_keys[0] or "", all_keys[1] or "", all_keys[2] or "", all_keys[3] or "")


def get_decrypted_credentials(parent, testnet: bool = False) -> tuple[str, str]:
    """
    يرجع (api_key, api_secret) حسب الوضع (testnet/mainnet).
    إن كان الملف مشفّراً يستخدم التخزين المؤقت إن وُجد (بعد فتح إعدادات API وإدخال كلمة المرور).
    """
    global _session_credentials
    key, secret = load_api_settings("testnet" if testnet else "mainnet")
    if (key and secret) or _session_credentials is None:
        return key or "", secret or ""
    # استخدام التخزين المؤقت بعد فك التشفير (فتح إعدادات API وكلمة المرور)
    mk, ms, tk, ts = _session_credentials
    return (tk, ts) if testnet else (mk, ms)


# ---------- حوار فتح الإعدادات (إدخال كلمة المرور) ----------
class _UnlockDialog(QDialog):
    def __init__(self, parent=None, title="كلمة مرور إعدادات API"):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("أدخل كلمة المرور للوصول إلى إعدادات API:"))
        self._pwd = QLineEdit()
        self._pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self._pwd.setPlaceholderText("كلمة المرور")
        layout.addWidget(self._pwd)
        btn = QPushButton("موافق")
        btn.clicked.connect(self._ok)
        layout.addWidget(btn)

    def _ok(self):
        if self._pwd.text().strip():
            self.accept()

    def password(self) -> str:
        return self._pwd.text().strip()


# ---------- نافذة إعدادات API: تداول حقيقي + تداول وهمي ----------
class APISettingsWindow(QDialog):
    def __init__(
        self,
        parent=None,
        password: str = "",
        mainnet_key: str = "",
        mainnet_secret: str = "",
        testnet_key: str = "",
        testnet_secret: str = "",
        etoro_user_key: str = "",
        etoro_api_key: str = "",
    ):
        super().__init__(parent)
        self._password = password
        self.setWindowTitle("إعدادات API — تداول حقيقي ووهمي و eToro")
        self.setMinimumSize(420, 480)

        layout = QVBoxLayout(self)

        grp_main = QGroupBox("تداول حقيقي (Mainnet — أموال حقيقية)")
        form_main = QVBoxLayout()
        form_main.addWidget(QLabel("API Key (من binance.com):"))
        self.mainnet_key_input = QLineEdit()
        self.mainnet_key_input.setPlaceholderText("مفتاح التداول الحقيقي")
        form_main.addWidget(self.mainnet_key_input)
        form_main.addWidget(QLabel("API Secret:"))
        self.mainnet_secret_input = QLineEdit()
        self.mainnet_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.mainnet_secret_input.setPlaceholderText("سرّ التداول الحقيقي")
        form_main.addWidget(self.mainnet_secret_input)
        grp_main.setLayout(form_main)
        layout.addWidget(grp_main)

        grp_test = QGroupBox("تداول وهمي (Testnet — أموال تجريبية)")
        form_test = QVBoxLayout()
        form_test.addWidget(QLabel("API Key (من testnet.binance.vision):"))
        self.testnet_key_input = QLineEdit()
        self.testnet_key_input.setPlaceholderText("مفتاح التداول الوهمي")
        form_test.addWidget(self.testnet_key_input)
        form_test.addWidget(QLabel("API Secret:"))
        self.testnet_secret_input = QLineEdit()
        self.testnet_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.testnet_secret_input.setPlaceholderText("سرّ التداول الوهمي")
        form_test.addWidget(self.testnet_secret_input)
        grp_test.setLayout(form_test)
        layout.addWidget(grp_test)

        grp_etoro = QGroupBox("eToro (اختياري — من الإعدادات > Trading > API Key Management)")
        form_etoro = QVBoxLayout()
        form_etoro.addWidget(QLabel("User Key (مفتاح المستخدم):"))
        self.etoro_user_key_input = QLineEdit()
        self.etoro_user_key_input.setPlaceholderText("User Key من eToro")
        form_etoro.addWidget(self.etoro_user_key_input)
        form_etoro.addWidget(QLabel("API Key (المفتاح العام):"))
        self.etoro_api_key_input = QLineEdit()
        self.etoro_api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.etoro_api_key_input.setPlaceholderText("Public API Key من eToro")
        form_etoro.addWidget(self.etoro_api_key_input)
        grp_etoro.setLayout(form_etoro)
        layout.addWidget(grp_etoro)

        row = QHBoxLayout()
        save_btn = QPushButton("حفظ")
        save_btn.clicked.connect(self.save_settings)
        close_btn = QPushButton("إغلاق")
        close_btn.clicked.connect(self.close)
        row.addWidget(save_btn)
        row.addWidget(close_btn)
        layout.addLayout(row)

        self.mainnet_key_input.setText(mainnet_key)
        self.mainnet_secret_input.setText(mainnet_secret)
        self.testnet_key_input.setText(testnet_key)
        self.testnet_secret_input.setText(testnet_secret)
        self.etoro_user_key_input.setText(etoro_user_key)
        self.etoro_api_key_input.setText(etoro_api_key)

    def save_settings(self):
        mk = self.mainnet_key_input.text().strip()
        ms = self.mainnet_secret_input.text().strip()
        tk = self.testnet_key_input.text().strip()
        ts = self.testnet_secret_input.text().strip()
        eu = self.etoro_user_key_input.text().strip()
        ea = self.etoro_api_key_input.text().strip()
        save_api_settings(mk, ms, tk, ts, etoro_user_key=eu, etoro_api_key=ea)
        clear_credentials_cache()
        self.close()


def open_api_settings_window(parent) -> None:
    """
    فتح إعدادات API. إن كان الملف مشفّراً يُطلب كلمة المرور لفتحه مرة واحدة؛ الحفظ يكون دائماً بدون تشفير.
    """
    raw = _read_raw()
    if raw is not None and _is_encrypted(raw):
        result = request_unlock_or_set_password(parent)
        if result is None:
            return
        _, main_k, main_s, test_k, test_s = result
        global _session_credentials
        _session_credentials = (main_k or "", main_s or "", test_k or "", test_s or "")
    else:
        all_keys = load_api_settings_all()
        if all_keys is None:
            main_k = main_s = test_k = test_s = ""
        else:
            main_k, main_s, test_k, test_s = all_keys
    etoro_user, etoro_api = load_etoro_settings()
    win = APISettingsWindow(
        parent, "", main_k or "", main_s or "", test_k or "", test_s or "",
        etoro_user_key=etoro_user, etoro_api_key=etoro_api,
    )
    win.setWindowTitle("إعدادات API — عدّل المفاتيح ثم اضغط «حفظ»")
    win.raise_()
    win.activateWindow()
    win.exec()
