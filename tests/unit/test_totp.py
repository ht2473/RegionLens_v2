"""Одноразовые коды входа (RFC 6238), резервные коды и QR-код ключа."""

from __future__ import annotations

import base64
from urllib.parse import parse_qs, urlparse

import pytest

from apps.accounts import totp

pytestmark = pytest.mark.unit

# Ключ из приложения B RFC 6238 (SHA-1): «12345678901234567890».
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        # Последние шесть цифр восьмизначных кодов таблицы RFC 6238.
        (59, "287082"),
        (1111111109, "081804"),
        (1111111111, "050471"),
        (1234567890, "005924"),
        (2000000000, "279037"),
    ],
)
def test_codes_match_the_standard(moment: int, expected: str) -> None:
    """Коды совпадают с контрольными значениями стандарта."""
    assert totp.code_at(RFC_SECRET, totp.current_step(moment)) == expected


def test_neighbouring_steps_are_accepted() -> None:
    """Код соседнего шага проходит: часы телефона расходятся с сервером."""
    now = 1_700_000_000.0
    step = totp.current_step(now)
    earlier = totp.code_at(RFC_SECRET, step - 1)
    assert totp.matching_step(RFC_SECRET, earlier, now=now) == step - 1
    too_old = totp.code_at(RFC_SECRET, step - 3)
    assert totp.matching_step(RFC_SECRET, too_old, now=now) is None


def test_used_step_is_not_accepted_again() -> None:
    """Код шага не старше последнего принятого отклоняется: повтор перехваченного кода."""
    now = 1_700_000_000.0
    step = totp.current_step(now)
    code = totp.code_at(RFC_SECRET, step)
    assert totp.matching_step(RFC_SECRET, code, after=step, now=now) is None
    assert totp.matching_step(RFC_SECRET, code, after=step - 1, now=now) == step


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef", "12 34 5x"])
def test_malformed_codes_are_rejected(code: str) -> None:
    """Не шесть цифр — не код."""
    assert totp.matching_step(RFC_SECRET, code, now=59) is None


def test_spaces_in_code_are_ignored() -> None:
    """Код, набранный с пробелом, как его показывают приложения, принимается."""
    code = totp.code_at(RFC_SECRET, totp.current_step(59))
    assert totp.matching_step(RFC_SECRET, f"{code[:3]} {code[3:]}", now=59) is not None


def test_new_secret_is_long_base32() -> None:
    """Ключ — 160 бит в base32; два ключа не совпадают."""
    first, second = totp.new_secret(), totp.new_secret()
    assert first != second
    padded = first + "=" * (-len(first) % 8)
    assert len(base64.b32decode(padded)) == totp.SECRET_BYTES


def test_provisioning_uri_carries_issuer_and_secret() -> None:
    """Адрес для приложения называет издателя, учётную запись и ключ."""
    uri = totp.provisioning_uri("ABCDEF", "user@example.com", "RegionLens")
    parsed = urlparse(uri)
    assert parsed.scheme == "otpauth"
    assert parsed.netloc == "totp"
    assert "RegionLens%3Auser%40example.com" in parsed.path
    query = parse_qs(parsed.query)
    assert query["secret"] == ["ABCDEF"]
    assert query["issuer"] == ["RegionLens"]


def test_recovery_codes_are_distinct_and_hashed_by_normal_form() -> None:
    """Резервные коды разные; хэш не зависит от регистра и дефиса."""
    codes = totp.new_recovery_codes()
    assert len(set(codes)) == totp.RECOVERY_COUNT
    code = codes[0]
    assert totp.recovery_hash(code) == totp.recovery_hash(code.upper().replace("-", ""))
    assert totp.recovery_hash(code) != totp.recovery_hash(codes[1])


def test_qr_picture_is_square_path() -> None:
    """QR-код — квадрат с полями и контуром тёмных модулей."""
    picture = totp.qr_picture("otpauth://totp/RegionLens:user?secret=ABCDEF")
    assert picture.size >= 21 + 2 * totp.QR_QUIET_ZONE
    assert picture.path.startswith(f"M{totp.QR_QUIET_ZONE} {totp.QR_QUIET_ZONE}h7")
