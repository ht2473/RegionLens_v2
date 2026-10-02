"""
Одноразовые коды входа по времени (RFC 6238), резервные коды и QR-код для приложения.

Шаг — 30 с, шесть цифр, HMAC-SHA1: так их понимают все приложения-генераторы. Принимается
код текущего шага и соседних (часы телефона расходятся), но не старше последнего принятого.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from dataclasses import dataclass
from urllib.parse import quote, urlencode

from django.utils.crypto import constant_time_compare, salted_hmac

STEP_SECONDS = 30
DIGITS = 6
# Сколько соседних шагов принимается в каждую сторону.
DRIFT_STEPS = 1
# Ключ — 160 бит, как рекомендует RFC 4226.
SECRET_BYTES = 20

RECOVERY_COUNT = 10
# Резервный код — две четвёрки знаков без похожих друг на друга (0/o, 1/l).
RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
RECOVERY_HALF = 4

# Поля вокруг QR-кода, в модулях: меньше четырёх приложения читают хуже.
QR_QUIET_ZONE = 4


def new_secret() -> str:
    """Новый ключ в base32 без знаков выравнивания."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def current_step(now: float | None = None) -> int:
    """Номер шага времени."""
    return int((time.time() if now is None else now) // STEP_SECONDS)


def code_at(secret: str, step: int) -> str:
    """Код шага по ключу (RFC 4226, динамическое усечение)."""
    padded = secret.upper() + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 10**DIGITS:0{DIGITS}d}"


def normalise(code: str) -> str:
    """Код без пробелов и дефисов, строчными буквами."""
    return "".join(code.split()).replace("-", "").lower()


def matching_step(
    secret: str, code: str, *, after: int = 0, now: float | None = None
) -> int | None:
    """Шаг, которому соответствует код, если он позже ``after``; иначе ``None``."""
    code = normalise(code)
    if not secret or len(code) != DIGITS or not code.isdigit():
        return None
    centre = current_step(now)
    for step in range(centre - DRIFT_STEPS, centre + DRIFT_STEPS + 1):
        if step > after and constant_time_compare(code_at(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    """Адрес ``otpauth://`` для QR-кода и ссылки на телефоне."""
    label = quote(f"{issuer}:{account}")
    query = urlencode(
        {"secret": secret, "issuer": issuer, "digits": DIGITS, "period": STEP_SECONDS}
    )
    return f"otpauth://totp/{label}?{query}"


def grouped(secret: str) -> str:
    """Ключ четвёрками — для ручного ввода."""
    return " ".join(secret[index : index + 4] for index in range(0, len(secret), 4))


# ---------------------------------------------------------------------------------------
# Резервные коды
# ---------------------------------------------------------------------------------------


def new_recovery_codes() -> list[str]:
    """Резервные коды для показа один раз: «abcd-efgh»."""
    codes = []
    for _index in range(RECOVERY_COUNT):
        text = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_HALF * 2))
        codes.append(f"{text[:RECOVERY_HALF]}-{text[RECOVERY_HALF:]}")
    return codes


def recovery_hash(code: str) -> str:
    """Хэш резервного кода с ключом приложения; в базе лежит только он."""
    return salted_hmac(
        "apps.accounts.totp.recovery", normalise(code), algorithm="sha256"
    ).hexdigest()


# ---------------------------------------------------------------------------------------
# QR-код
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QrPicture:
    """QR-код одним контуром SVG: размер стороны в модулях и путь тёмных модулей."""

    size: int
    path: str


def qr_picture(text: str) -> QrPicture:
    """QR-код текста; кодировщик — из reportlab, рисунок — путь из прямоугольников."""
    from reportlab.graphics.barcode import qrencoder

    code = qrencoder.QRCode(None, qrencoder.QRErrorCorrectLevel.M)
    code.addData(text)
    code.make()
    count = code.getModuleCount()
    parts = []
    for row in range(count):
        column = 0
        while column < count:
            if not code.isDark(row, column):
                column += 1
                continue
            start = column
            while column < count and code.isDark(row, column):
                column += 1
            x, y = start + QR_QUIET_ZONE, row + QR_QUIET_ZONE
            parts.append(f"M{x} {y}h{column - start}v1h-{column - start}z")
    return QrPicture(size=count + QR_QUIET_ZONE * 2, path="".join(parts))
