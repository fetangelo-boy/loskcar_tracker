"""Серверный API заявок с сайта и health-check.

Секрет не публикуется в браузере: сайт вызывает endpoint со своего сервера.
Повторная доставка события с тем же external_id не создаёт дубль.
"""

from __future__ import annotations

import hmac
import json
from datetime import datetime

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, connection
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import Client, Order, Service
from .services import WorkflowError, add_vehicle, create_client, create_order
from .utils import normalize_phone, phone_is_plausible
from .workflow import Status


def _json_error(message: str, *, status: int = 400) -> JsonResponse:
    return JsonResponse({"ok": False, "error": message}, status=status)


def _api_key_from_request(request) -> str:
    key = (request.headers.get("X-API-Key") or "").strip()
    if key:
        return key
    authorization = (request.headers.get("Authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _rate_limited(ip: str) -> bool:
    if not ip:
        return False
    key = f"lead-api:{ip}"
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=600)
        count = 1
    return count > 60


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _resolve_services(raw) -> tuple[list[Service], list[str]]:
    if not raw:
        return [], []
    names = raw if isinstance(raw, list) else [raw]
    catalog = {
        service.name.strip().lower(): service
        for service in Service.objects.filter(is_active=True)
    }
    found: list[Service] = []
    unknown: list[str] = []
    for item in names:
        name = str(item or "").strip()
        if not name:
            continue
        service = catalog.get(name.lower())
        if service:
            if service not in found:
                found.append(service)
        else:
            unknown.append(name)
    return found, unknown


@csrf_exempt
@require_POST
def lead_webhook(request):
    if not settings.LEAD_API_KEY:
        return _json_error("API заявок не настроен.", status=503)

    provided = _api_key_from_request(request)
    if not provided or not hmac.compare_digest(provided, settings.LEAD_API_KEY):
        return _json_error("Неверный ключ API.", status=403)

    ip = (request.META.get("REMOTE_ADDR") or "").strip()
    if _rate_limited(ip):
        return _json_error("Слишком много запросов. Повторите позже.", status=429)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _json_error("Ожидается корректный JSON.")
    if not isinstance(payload, dict):
        return _json_error("JSON должен быть объектом.")

    external_id = str(payload.get("external_id") or "").strip()
    if not external_id:
        return _json_error(
            "external_id обязателен: он защищает от дублей при повторной доставке."
        )

    existing = Order.objects.filter(
        source=Order.Source.SITE, external_id=external_id
    ).first()
    if existing:
        return JsonResponse(
            {
                "ok": True,
                "duplicate": True,
                "id": existing.pk,
                "number": existing.display_number,
            }
        )

    name = str(payload.get("name") or "").strip()
    phone_raw = str(payload.get("phone") or "").strip()
    phone = normalize_phone(phone_raw)
    if not name:
        return _json_error("Укажите имя.")
    if not phone or not phone_is_plausible(phone):
        return _json_error("Укажите корректный телефон.")

    desired_at = None
    if payload.get("desired_at"):
        desired_at = parse_datetime(str(payload["desired_at"]))
        if desired_at is None:
            return _json_error("desired_at должен быть в формате ISO 8601.")

    services, unknown_services = _resolve_services(
        payload.get("services") or payload.get("service")
    )

    comment_parts: list[str] = []
    message = str(payload.get("comment") or payload.get("message") or "").strip()
    if message:
        comment_parts.append(message)
    if unknown_services:
        comment_parts.append(
            "Услуги с сайта (нет в справочнике): " + ", ".join(unknown_services)
        )

    vehicle_data = payload.get("vehicle") or {}
    if not isinstance(vehicle_data, dict):
        vehicle_data = {}
    make = str(vehicle_data.get("make") or "").strip()
    model = str(vehicle_data.get("model") or "").strip()
    plate = str(vehicle_data.get("plate") or "").strip()
    if (make or model) and not (make and model):
        comment_parts.append("Автомобиль с сайта указан неполно.")
    if plate and not (make and model):
        comment_parts.append(f"Госномер с сайта: {plate}")

    if not services and not comment_parts:
        return _json_error("Нужна услуга или текст обращения.")

    try:
        client = Client.objects.filter(phone=phone).order_by("id").first()
        if client is None:
            client = create_client(
                name=name,
                phone=phone_raw,
                telegram=str(payload.get("telegram") or "").strip(),
                actor=None,
            )
        vehicle = None
        if make and model:
            vehicle, _ = add_vehicle(
                client, make=make, model=model, plate=plate, actor=None
            )
        try:
            order = create_order(
                client=client,
                vehicle=vehicle,
                services=services,
                source=Order.Source.SITE,
                comment="\n".join(comment_parts),
                desired_at=_aware(desired_at),
                external_id=external_id,
                actor=None,
            )
        except IntegrityError:
            # Параллельная доставка того же события.
            existing = Order.objects.filter(
                source=Order.Source.SITE, external_id=external_id
            ).first()
            if existing is None:
                raise
            return JsonResponse(
                {
                    "ok": True,
                    "duplicate": True,
                    "id": existing.pk,
                    "number": existing.display_number,
                }
            )
    except WorkflowError as exc:
        return _json_error("; ".join(exc.messages))

    return JsonResponse(
        {
            "ok": True,
            "duplicate": False,
            "id": order.pk,
            "number": order.display_number,
            "status": order.status,
            "status_label": Status(order.status).label,
        },
        status=201,
    )


@require_GET
def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "error"}, status=503)
    return JsonResponse({"status": "ok"})
