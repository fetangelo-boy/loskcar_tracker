"""Бизнес-правила трекера.

Все проверки статусов, вместимости и прав выполняются здесь, а не в
шаблонах или JavaScript. Это позволит подключить авторизацию на этапе 2
без переписывания сценариев.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import User

from .audit import log_audit
from .models import (
    Client,
    Comment,
    Order,
    OrderEvent,
    OrderService,
    Service,
    Vehicle,
)
from .utils import (
    digits_only,
    normalize_phone,
    normalize_plate,
    phone_is_plausible,
)
from .workflow import (
    CAPACITY_STATUSES,
    OUTCOME_STATUSES,
    OVERRIDE_TARGETS,
    Status,
    action_needs_reason,
    status_label,
)


class WorkflowError(ValidationError):
    """Ошибка бизнес-правила, показывается пользователю."""


class StaleOrderError(WorkflowError):
    """Запись изменена параллельно: не перетираем молча."""


def current_actor(request=None):
    """Реальный автор действия.

    После включения авторизации действия приписываются вошедшему сотруднику.
    Для серверного webhook и системных операций автор остаётся пустым.
    """
    if request is not None and getattr(request, "user", None) is not None:
        if request.user.is_authenticated:
            return request.user
    return None


def require_management(actor, message: str = "Недостаточно прав для этого действия.") -> None:
    """Владелец или администратор. Системные вызовы (actor=None) разрешены."""
    if actor is None:
        return
    if getattr(actor, "role", None) not in {User.Role.OWNER, User.Role.ADMIN}:
        raise WorkflowError(message)


def require_master_or_management(actor) -> None:
    if actor is None:
        return
    if getattr(actor, "role", None) not in {
        User.Role.OWNER,
        User.Role.ADMIN,
        User.Role.MASTER,
    }:
        raise WorkflowError("Недостаточно прав для этого действия.")


# --- Клиенты и автомобили -------------------------------------------------


def find_client_matches(name: str, phone: str):
    """Совпадения по нормализованному телефону или точному имени."""
    conditions = []
    normalized = normalize_phone(phone)
    if normalized:
        conditions.append({"phone": normalized})
    name = (name or "").strip()
    if name:
        conditions.append({"name__iexact": name})

    if not conditions:
        return Client.objects.none()

    from django.db.models import Q

    query = Q()
    for condition in conditions:
        query |= Q(**condition)
    return Client.objects.filter(query).order_by("-is_regular", "name")[:10]


def create_client(
    *,
    name: str,
    phone: str,
    telegram: str = "",
    notes: str = "",
    is_regular: bool = False,
    actor=None,
) -> Client:
    require_management(actor, "Создавать клиентов может владелец или администратор.")
    name = (name or "").strip()
    normalized = normalize_phone(phone)
    if not name:
        raise WorkflowError("Укажите имя клиента.")
    if not normalized or not phone_is_plausible(normalized):
        raise WorkflowError("Укажите корректный телефон клиента.")

    client = Client.objects.create(
        name=name,
        phone=normalized,
        phone_raw=(phone or "").strip(),
        telegram=(telegram or "").strip(),
        notes=(notes or "").strip(),
        is_regular=is_regular,
    )
    log_audit(
        actor=actor,
        action="client_created",
        entity_type="client",
        entity_id=client.pk,
        summary=f"Создан клиент {client.name}",
        details={"phone": normalized},
    )
    return client


@transaction.atomic
def update_client(client: Client, *, actor=None, **fields) -> Client:
    require_management(actor, "Редактировать клиентов может владелец или администратор.")
    allowed = {"name", "telegram", "notes", "is_regular", "phone", "phone_raw"}
    changed = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "phone":
            normalized = normalize_phone(value)
            if not phone_is_plausible(normalized):
                raise WorkflowError("Укажите корректный телефон клиента.")
            client.phone = normalized
            client.phone_raw = (value or "").strip()
            changed.append("phone")
            continue
        old = getattr(client, key)
        if old != value:
            setattr(client, key, value)
            changed.append(key)
    if changed:
        client.save()
        log_audit(
            actor=actor,
            action="client_updated",
            entity_type="client",
            entity_id=client.pk,
            summary=f"Обновлён клиент {client.name}",
            details={"fields": changed},
        )
    return client


@transaction.atomic
def add_vehicle(
    client: Client,
    *,
    make: str,
    model: str,
    plate: str = "",
    actor=None,
) -> tuple[Vehicle, bool]:
    require_management(actor, "Добавлять автомобили может владелец или администратор.")
    make = (make or "").strip()
    model = (model or "").strip()
    plate = (plate or "").strip().upper()
    if not make or not model:
        raise WorkflowError("Укажите марку и модель автомобиля.")

    if plate:
        plate_normalized = normalize_plate(plate)
        existing = Vehicle.objects.filter(
            client=client, plate_normalized=plate_normalized
        ).first()
        if existing:
            return existing, False

    vehicle = Vehicle.objects.create(
        client=client,
        make=make,
        model=model,
        plate=plate,
    )
    log_audit(
        actor=actor,
        action="vehicle_added",
        entity_type="vehicle",
        entity_id=vehicle.pk,
        summary=f"Добавлен автомобиль {vehicle}",
        details={"client_id": client.pk},
    )
    return vehicle, True


# --- Заказы ---------------------------------------------------------------


@transaction.atomic
def create_order(
    *,
    client: Client,
    vehicle: Vehicle | None,
    services: list[Service],
    source: str = Order.Source.CALL,
    comment: str = "",
    desired_at: datetime | None = None,
    planned_start: datetime | None = None,
    planned_end: datetime | None = None,
    master=None,
    administrator=None,
    actor=None,
    external_id: str = "",
) -> Order:
    require_management(actor, "Создавать заявки может владелец или администратор.")
    services = list(services)
    if not services and not (comment or "").strip():
        raise WorkflowError("Выберите услугу или опишите первоначальный запрос.")

    order = Order.objects.create(
        client=client,
        vehicle=vehicle,
        source=source,
        comment=(comment or "").strip(),
        desired_at=desired_at,
        planned_start=planned_start,
        planned_end=planned_end,
        master=master,
        administrator=administrator,
        created_by=actor,
        external_id=(external_id or "").strip(),
    )
    _add_service_rows(order, services, actor=actor)

    OrderEvent.objects.create(
        order=order,
        kind=OrderEvent.Kind.CREATED,
        note=f"Источник: {order.get_source_display()}",
        author=actor,
    )
    log_audit(
        actor=actor,
        action="order_created",
        entity_type="order",
        entity_id=order.pk,
        summary=f"Создан заказ {order.display_number}",
        details={
            "client_id": client.pk,
            "source": source,
            "external_id": order.external_id,
        },
    )
    return order


def _add_service_rows(order: Order, services: list[Service], *, actor=None) -> None:
    existing_ids = set(
        order.services.filter(is_active=True, service__isnull=False).values_list(
            "service_id", flat=True
        )
    )
    position = (
        order.services.order_by("-position").values_list("position", flat=True).first()
        or 0
    )
    added = []
    for service in services:
        if service.pk in existing_ids:
            continue
        position += 1
        OrderService.objects.create(
            order=order,
            service=service,
            name=service.name,
            unit_price=service.base_price,
            price_is_from=service.price_is_from,
            duration_minutes=service.duration_minutes,
            position=position,
        )
        added.append(service.name)
    if added:
        OrderEvent.objects.create(
            order=order,
            kind=OrderEvent.Kind.SERVICE_ADDED,
            note="Добавлены услуги: " + ", ".join(added),
            author=actor,
        )


@transaction.atomic
def add_services(order: Order, services: list[Service], *, actor=None) -> int:
    require_management(actor, "Добавлять услуги может владелец или администратор.")
    locked = Order.objects.select_for_update().get(pk=order.pk)
    _add_service_rows(locked, list(services), actor=actor)
    bump_version(locked)
    log_audit(
        actor=actor,
        action="order_services_added",
        entity_type="order",
        entity_id=locked.pk,
        summary=f"Услуги добавлены в {locked.display_number}",
        details={"services": [service.name for service in services]},
    )
    return len(services)


@transaction.atomic
def remove_service_row(row: OrderService, *, actor=None, reason: str = "") -> None:
    require_management(
        actor, "Убирать услугу из заказа может владелец или администратор."
    )
    locked = OrderService.objects.select_related("order").get(pk=row.pk)
    order = Order.objects.select_for_update().get(pk=locked.order_id)
    if not locked.is_active:
        return
    if locked.is_done and order.status in {Status.READY, Status.ISSUED}:
        raise WorkflowError(
            "Нельзя убрать выполненную услугу из готового или выданного заказа."
        )
    active_count = order.services.filter(is_active=True).count()
    if (
        active_count <= 1
        and order.status
        in {
            Status.BOOKED,
            Status.ACCEPTED,
            Status.IN_PROGRESS,
            Status.READY,
        }
    ):
        raise WorkflowError("В записанном заказе должна остаться хотя бы одна услуга.")
    if not (reason or "").strip():
        raise WorkflowError("Укажите причину, по которой услуга убирается.")

    locked.is_active = False
    locked.save(update_fields=["is_active"])
    OrderEvent.objects.create(
        order=order,
        kind=OrderEvent.Kind.SERVICE_REMOVED,
        note=f"Убрана услуга «{locked.name}». Причина: {reason.strip()}",
        author=actor,
    )
    bump_version(order)
    log_audit(
        actor=actor,
        action="order_service_removed",
        entity_type="order",
        entity_id=order.pk,
        summary=f"Убрана услуга «{locked.name}» из {order.display_number}",
        details={"reason": reason.strip()},
    )


@transaction.atomic
def toggle_service_done(
    row: OrderService, *, done: bool, actor=None
) -> Order:
    require_master_or_management(actor)
    locked = OrderService.objects.select_related("order").get(pk=row.pk)
    order = Order.objects.select_for_update().get(pk=locked.order_id)

    if not locked.is_active:
        raise WorkflowError("Услуга не активна.")
    if order.status not in {Status.ACCEPTED, Status.IN_PROGRESS}:
        raise WorkflowError(
            "Отмечать услуги можно только после приёма автомобиля и до готовности."
        )
    if not done and order.status == Status.IN_PROGRESS and locked.is_done:
        # Снять отметку до готовности можно, это журналируется.
        pass

    locked.is_done = done
    locked.done_at = timezone.now() if done else None
    locked.done_by = actor if done else None
    locked.save(update_fields=["is_done", "done_at", "done_by"])

    OrderEvent.objects.create(
        order=order,
        kind=(
            OrderEvent.Kind.SERVICE_DONE
            if done
            else OrderEvent.Kind.SERVICE_UNDONE
        ),
        note=(
            f"Выполнена услуга «{locked.name}»"
            if done
            else f"Снята отметка выполнения «{locked.name}»"
        ),
        author=actor,
    )
    bump_version(order)
    log_audit(
        actor=actor,
        action="service_done" if done else "service_undone",
        entity_type="order_service",
        entity_id=locked.pk,
        summary=locked.name,
        details={"order_id": order.pk},
    )
    return order


def capacity_conflicts(
    start: datetime | None,
    end: datetime | None,
    *,
    exclude_order_id: int | None = None,
) -> int:
    """Число заказов, пересекающих интервал (конец == начало не конфликтует)."""
    if not start or not end:
        return 0
    query = Order.objects.filter(
        status__in=CAPACITY_STATUSES,
        planned_start__lt=end,
        planned_end__gt=start,
    )
    if exclude_order_id:
        query = query.exclude(pk=exclude_order_id)
    return query.count()


def validate_schedule(
    *,
    start: datetime | None,
    end: datetime | None,
    capacity_required: bool,
    exclude_order_id: int | None = None,
) -> None:
    if capacity_required:
        if not start or not end:
            raise WorkflowError(
                "Для подтверждённой записи обязательны начало и плановое окончание."
            )
    if start and end and end <= start:
        raise WorkflowError("Плановое окончание должно быть позже начала.")
    if start and end:
        conflicts = capacity_conflicts(start, end, exclude_order_id=exclude_order_id)
        free = max(settings.WORKSHOP_CAPACITY - conflicts, 0)
        if free <= 0:
            raise WorkflowError(
                f"На это время занято {conflicts} из {settings.WORKSHOP_CAPACITY} мест. "
                "Выберите другой интервал."
            )


def validate_order_ready_for_booking(order: Order) -> None:
    if not order.services.filter(is_active=True).exists():
        raise WorkflowError("Для записи выберите хотя бы одну услугу.")
    if not order.vehicle_id:
        raise WorkflowError("Для записи укажите автомобиль клиента.")
    if not order.planned_start or not order.planned_end:
        raise WorkflowError(
            "Для записи обязательны подтверждённые начало и окончание."
        )


@transaction.atomic
def set_order_status(
    order: Order,
    target: str,
    *,
    actor=None,
    reason: str = "",
    expected_version: int | None = None,
    allow_override: bool = False,
) -> Order:
    locked = (
        Order.objects.select_for_update()
        .select_related("client", "vehicle")
        .get(pk=order.pk)
    )

    if expected_version is not None and locked.version != expected_version:
        raise StaleOrderError(
            "Заказ уже изменили в другом окне. Обновите страницу и повторите действие."
        )
    if target not in Order.Status.values:
        raise WorkflowError("Неизвестный статус.")

    current = locked.status
    if target == current:
        raise WorkflowError("Заказ уже находится в этом статусе.")

    actor_role = getattr(actor, "role", None)
    if actor is not None:
        if actor_role == User.Role.MASTER:
            master_targets = {
                Status.BOOKED: {Status.ACCEPTED},
                Status.ACCEPTED: {Status.IN_PROGRESS},
                Status.IN_PROGRESS: {Status.READY},
            }.get(current, set())
            if target not in master_targets:
                raise WorkflowError(
                    "Мастер может менять только производственные этапы: "
                    "приём автомобиля, начало работ и готовность."
                )
        elif actor_role not in {User.Role.OWNER, User.Role.ADMIN}:
            raise WorkflowError("Недостаточно прав для изменения статуса.")

    forward = target in _forward_map().get(current, []) or (
        target in OUTCOME_STATUSES
        and current
        in {Status.NEW, Status.ADMIN_WORK, Status.CLARIFYING, Status.BOOKED}
    )
    is_override = False

    if not forward:
        allowed_override = target in OVERRIDE_TARGETS.get(current, [])
        if not allowed_override:
            raise WorkflowError(
                f"Переход «{status_label(current)}» → «{status_label(target)}» "
                "недоступен."
            )
        if not allow_override:
            raise WorkflowError(
                "Обратный переход выполняется командой «Исправить статус» с причиной."
            )
        if actor is not None and getattr(actor, "role", None) not in {
            User.Role.OWNER,
            User.Role.ADMIN,
        }:
            raise WorkflowError("Исправлять статус может только владелец или администратор.")
        is_override = True

    if action_needs_reason(locked, target) and not (reason or "").strip():
        raise WorkflowError("Укажите причину изменения статуса.")

    if target in OUTCOME_STATUSES:
        if current not in {
            Status.NEW,
            Status.ADMIN_WORK,
            Status.CLARIFYING,
            Status.BOOKED,
        }:
            raise WorkflowError(
                "После приёма автомобиля заказ закрывается через исправление статуса."
            )

    if target == Status.BOOKED:
        # Времена могли быть изменены в карточке до перевода в «Записан».
        validate_order_ready_for_booking(locked)
        validate_schedule(
            start=locked.planned_start,
            end=locked.planned_end,
            capacity_required=True,
            exclude_order_id=locked.pk,
        )

    if target == Status.READY:
        rows = list(locked.services.filter(is_active=True))
        if not rows:
            raise WorkflowError("В заказе нет активных услуг — завершать нечего.")
        pending = [row.name for row in rows if not row.is_done]
        if pending:
            raise WorkflowError(
                "Нельзя перевести в «Готово», пока не выполнены услуги: "
                + ", ".join(pending)
            )

    if target in CAPACITY_STATUSES:
        validate_schedule(
            start=locked.planned_start,
            end=locked.planned_end,
            capacity_required=True,
            exclude_order_id=locked.pk,
        )

    from_status = locked.status
    locked.status = target
    locked.version += 1
    if target in OUTCOME_STATUSES:
        locked.problem_flag = False
    locked.save(update_fields=["status", "version", "problem_flag", "updated_at"])

    OrderEvent.objects.create(
        order=locked,
        kind=(
            OrderEvent.Kind.STATUS_OVERRIDE
            if is_override
            else OrderEvent.Kind.STATUS
        ),
        from_status=from_status,
        to_status=target,
        note=(reason or "").strip(),
        author=actor,
    )
    log_audit(
        actor=actor,
        action="order_status_override" if is_override else "order_status",
        entity_type="order",
        entity_id=locked.pk,
        summary=f"{locked.display_number}: {status_label(from_status)} → {status_label(target)}",
        details={
            "from": from_status,
            "to": target,
            "reason": (reason or "").strip(),
        },
    )
    return locked


def _forward_map():
    from .workflow import ALLOWED_TRANSITIONS

    return ALLOWED_TRANSITIONS


@transaction.atomic
def update_order(
    order: Order,
    *,
    actor=None,
    expected_version: int | None = None,
    vehicle=None,
    source: str | None = None,
    comment: str | None = None,
    desired_at=None,
    planned_start=None,
    planned_end=None,
    master=None,
) -> Order:
    require_management(actor, "Редактировать заказ может владелец или администратор.")
    locked = (
        Order.objects.select_for_update()
        .select_related("client", "vehicle")
        .get(pk=order.pk)
    )
    if expected_version is not None and locked.version != expected_version:
        raise StaleOrderError(
            "Заказ уже изменили в другом окне. Обновите страницу и повторите действие."
        )

    if vehicle is not None and vehicle.client_id != locked.client_id:
        raise WorkflowError("Автомобиль принадлежит другому клиенту.")

    if locked.status in {
        Status.BOOKED,
        Status.ACCEPTED,
        Status.IN_PROGRESS,
        Status.READY,
    }:
        if vehicle is not None:
            locked.vehicle = vehicle
        if not locked.vehicle_id:
            raise WorkflowError("У записанного заказа должен быть автомобиль.")

    old_start, old_end = locked.planned_start, locked.planned_end
    locked.vehicle = vehicle if vehicle is not None else locked.vehicle
    if source is not None:
        locked.source = source
    if comment is not None:
        locked.comment = comment.strip()
    locked.desired_at = desired_at
    locked.planned_start = planned_start
    locked.planned_end = planned_end
    locked.master = master

    capacity_required = locked.status in CAPACITY_STATUSES
    validate_schedule(
        start=planned_start,
        end=planned_end,
        capacity_required=capacity_required,
        exclude_order_id=locked.pk,
    )

    if locked.status in {
        Status.BOOKED,
        Status.ACCEPTED,
        Status.IN_PROGRESS,
        Status.READY,
    }:
        if not locked.services.filter(is_active=True).exists():
            raise WorkflowError("В записанном заказе должна остаться хотя бы одна услуга.")

    locked.version += 1
    locked.save()
    if (old_start, old_end) != (locked.planned_start, locked.planned_end):
        OrderEvent.objects.create(
            order=locked,
            kind=OrderEvent.Kind.BOOKING,
            note="Изменено время записи",
            author=actor,
        )
    else:
        OrderEvent.objects.create(
            order=locked,
            kind=OrderEvent.Kind.UPDATE,
            note="Обновлены данные заказа",
            author=actor,
        )
    log_audit(
        actor=actor,
        action="order_updated",
        entity_type="order",
        entity_id=locked.pk,
        summary=f"Обновлён заказ {locked.display_number}",
        details={},
    )
    return locked


def bump_version(order: Order) -> None:
    Order.objects.filter(pk=order.pk).update(
        version=order.version + 1,
        updated_at=timezone.now(),
    )
    order.version += 1


# --- Комментарии и проблемы ----------------------------------------------


@transaction.atomic
def add_comment(
    order: Order,
    *,
    text: str,
    kind: str = Comment.Kind.COMMENT,
    actor=None,
    expected_at=None,
) -> Comment:
    text = (text or "").strip()
    if not text:
        raise WorkflowError("Введите текст комментария.")
    if kind not in Comment.Kind.values:
        raise WorkflowError("Неизвестный тип комментария.")

    locked = Order.objects.select_for_update().get(pk=order.pk)
    comment = Comment.objects.create(
        order=locked,
        kind=kind,
        text=text,
        expected_at=expected_at,
        author=actor,
    )
    if kind == Comment.Kind.PROBLEM:
        locked.problem_flag = True
        locked.save(update_fields=["problem_flag", "updated_at"])
        OrderEvent.objects.create(
            order=locked,
            kind=OrderEvent.Kind.PROBLEM,
            note=text,
            author=actor,
        )
    else:
        OrderEvent.objects.create(
            order=locked,
            kind=OrderEvent.Kind.COMMENT,
            note=text,
            author=actor,
        )
    bump_version(locked)
    log_audit(
        actor=actor,
        action="comment_added",
        entity_type="order",
        entity_id=locked.pk,
        summary=f"Комментарий к {locked.display_number}",
        details={"kind": kind},
    )
    return comment


@transaction.atomic
def resolve_problems(order: Order, *, actor=None) -> int:
    require_management(
        actor, "Закрывать проблемы может владелец или администратор."
    )
    locked = Order.objects.select_for_update().get(pk=order.pk)
    now = timezone.now()
    updated = Comment.objects.filter(
        order=locked, kind=Comment.Kind.PROBLEM, resolved_at__isnull=True
    ).update(resolved_at=now, resolved_by=actor)
    if updated:
        locked.problem_flag = False
        locked.save(update_fields=["problem_flag", "updated_at"])
        OrderEvent.objects.create(
            order=locked,
            kind=OrderEvent.Kind.PROBLEM_RESOLVED,
            note=f"Отмечено решённых проблем: {updated}",
            author=actor,
        )
        bump_version(locked)
        log_audit(
            actor=actor,
            action="problem_resolved",
            entity_type="order",
            entity_id=locked.pk,
            summary=f"Проблемы закрыты по {locked.display_number}",
        )
    return updated


# --- Деньги (только чтение до этапа финансов) ----------------------------


def money_summary(order: Order) -> tuple[Decimal, int]:
    """Известная предварительная сумма и число строк «требует оценки»."""
    total = Decimal("0")
    unknown = 0
    for row in order.services.filter(is_active=True):
        if row.unit_price is None:
            unknown += 1
        else:
            total += row.unit_price
    return total, unknown


def search_orders(query: str, *, include_closed: bool = True):
    """Поиск по номеру, имени, телефону и госномеру."""
    from django.db.models import Q

    queryset = Order.objects.select_related("client", "vehicle", "master")
    query = (query or "").strip()
    if not query:
        return queryset
    digits = digits_only(query)
    lookup = (
        Q(number__icontains=query)
        | Q(client__name__icontains=query)
        | Q(vehicle__plate__icontains=query)
        | Q(vehicle__make__icontains=query)
        | Q(vehicle__model__icontains=query)
    )
    if len(digits) >= 4:
        lookup |= Q(client__phone__contains=digits)
        lookup |= Q(vehicle__plate_normalized__contains=digits)
    return queryset.filter(lookup)
