"""Небольшие чистые функции: телефоны, госномера, деньги, даты."""

from __future__ import annotations

import re
from datetime import date, datetime

from django.utils import timezone

PHONE_DIGITS_RE = re.compile(r"\D+")
PLATE_RE = re.compile(r"[^0-9A-ZА-ЯЁ]")
CYRILLIC_TO_LATIN = str.maketrans(
    {
        "А": "A",
        "В": "B",
        "Е": "E",
        "К": "K",
        "М": "M",
        "Н": "H",
        "О": "O",
        "Р": "P",
        "С": "C",
        "Т": "T",
        "У": "Y",
        "Х": "X",
        "Ё": "E",
    }
)


def digits_only(value: str) -> str:
    return PHONE_DIGITS_RE.sub("", value or "")


def normalize_phone(raw: str) -> str:
    """Приводит российский номер к виду +7XXXXXXXXXX.

    Иностранные/неполные номера сохраняются как +<digits>, чтобы не терять
    информацию. Пустая строка остаётся пустой.
    """
    digits = digits_only(raw)
    if not digits:
        return ""
    if len(digits) == 11 and digits[0] in {"7", "8"}:
        return "+7" + digits[1:]
    if len(digits) == 10:
        return "+7" + digits
    return "+" + digits


def phone_is_plausible(normalized: str) -> bool:
    digits = digits_only(normalized)
    return len(digits) >= 10


def format_phone(normalized: str) -> str:
    digits = digits_only(normalized)
    if len(digits) == 11 and digits.startswith("7"):
        rest = digits[1:]
        return f"+7 ({rest[0:3]}) {rest[3:6]}-{rest[6:8]}-{rest[8:10]}"
    if normalized:
        return normalized
    return ""


def normalize_plate(raw: str) -> str:
    """Госномер для сравнения: верхний регистр, без пробелов и дефисов.

    Кириллические буквы, похожие на латинские, приводятся к латинице.
    """
    value = (raw or "").upper().replace(" ", "").replace("-", "")
    value = PLATE_RE.sub("", value).translate(CYRILLIC_TO_LATIN)
    return value


def format_money(value) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def format_duration(minutes: int | None) -> str:
    if not minutes:
        return "—"
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours} ч {mins} мин"
    if hours:
        return f"{hours} ч"
    return f"{mins} мин"


def local_date(value: datetime | None) -> date | None:
    if not value:
        return None
    return timezone.localtime(value).date()


def short_datetime(value: datetime | None) -> str:
    if not value:
        return "—"
    local = timezone.localtime(value)
    return local.strftime("%d.%m.%Y %H:%M")


def short_date(value: datetime | None) -> str:
    if not value:
        return "—"
    return timezone.localtime(value).strftime("%d.%m.%Y")


def short_time(value: datetime | None) -> str:
    if not value:
        return "—"
    return timezone.localtime(value).strftime("%H:%M")


def humanize_minutes(minutes: int | None) -> str:
    return format_duration(minutes)
