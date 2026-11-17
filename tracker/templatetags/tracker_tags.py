from django import template

from ..utils import format_duration, format_money, format_phone, short_datetime
from ..workflow import status_label, status_tone

register = template.Library()


@register.filter
def phone(value):
    return format_phone(value) or "—"


@register.filter
def money(value):
    return format_money(value)


@register.filter
def duration(value):
    return format_duration(value)


@register.filter
def dt(value):
    return short_datetime(value)


@register.filter
def status_text(value):
    return status_label(value)


@register.filter
def tone(value):
    return status_tone(value)


@register.filter
def get_item(mapping, key):
    if hasattr(mapping, "get"):
        return mapping.get(key)
    return None
