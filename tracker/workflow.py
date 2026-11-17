"""Статусная модель заказа и правила переходов.

Правила живут на backend; канбан, карточка и календарь используют одни
и те же функции, чтобы не расходиться.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Order

Status = Order.Status

BOARD_STATUSES: list[str] = [
    Status.NEW,
    Status.ADMIN_WORK,
    Status.CLARIFYING,
    Status.BOOKED,
    Status.ACCEPTED,
    Status.IN_PROGRESS,
    Status.READY,
    Status.ISSUED,
]

# Подписи и цветовые акценты для колонок и бейджей.
STATUS_META: dict[str, dict[str, str]] = {
    Status.NEW: {"label": "Новая заявка", "tone": "new"},
    Status.ADMIN_WORK: {"label": "В работе администратора", "tone": "admin"},
    Status.CLARIFYING: {"label": "Уточняются детали", "tone": "clarify"},
    Status.BOOKED: {"label": "Записан", "tone": "booked"},
    Status.ACCEPTED: {"label": "Автомобиль принят", "tone": "accepted"},
    Status.IN_PROGRESS: {"label": "В работе у мастера", "tone": "progress"},
    Status.READY: {"label": "Готово", "tone": "ready"},
    Status.ISSUED: {"label": "Выдан клиенту", "tone": "issued"},
    Status.DECLINED: {"label": "Отказ", "tone": "outcome"},
    Status.CANCELLED: {"label": "Отмена клиентом", "tone": "outcome"},
    Status.NO_SHOW: {"label": "Не пришёл", "tone": "outcome"},
    Status.LOST: {"label": "Потерян", "tone": "outcome"},
}

OUTCOME_STATUSES: list[str] = [
    Status.DECLINED,
    Status.CANCELLED,
    Status.NO_SHOW,
    Status.LOST,
]

# Разрешённые прямые (forward) переходы. Всё остальное — только
# «Исправить статус» с причиной и правами владельца/администратора.
ALLOWED_TRANSITIONS: dict[str, list[str]] = {
    Status.NEW: [Status.ADMIN_WORK, Status.CLARIFYING, Status.BOOKED],
    Status.ADMIN_WORK: [Status.CLARIFYING, Status.BOOKED],
    Status.CLARIFYING: [Status.BOOKED],
    Status.BOOKED: [Status.ACCEPTED],
    Status.ACCEPTED: [Status.IN_PROGRESS],
    Status.IN_PROGRESS: [Status.READY],
    Status.READY: [Status.ISSUED],
    Status.ISSUED: [],
    Status.DECLINED: [],
    Status.CANCELLED: [],
    Status.NO_SHOW: [],
    Status.LOST: [],
}

# Куда можно вернуть заказ через «Исправить статус» (владелец/администратор).
OVERRIDE_TARGETS: dict[str, list[str]] = {
    Status.NEW: [],
    Status.ADMIN_WORK: [Status.NEW],
    Status.CLARIFYING: [Status.NEW, Status.ADMIN_WORK],
    Status.BOOKED: [Status.NEW, Status.ADMIN_WORK, Status.CLARIFYING],
    Status.ACCEPTED: [Status.BOOKED],
    Status.IN_PROGRESS: [Status.ACCEPTED, Status.BOOKED],
    Status.READY: [Status.IN_PROGRESS, Status.ACCEPTED, Status.BOOKED],
    Status.ISSUED: [Status.READY],
    Status.DECLINED: [Status.NEW, Status.ADMIN_WORK, Status.CLARIFYING],
    Status.CANCELLED: [Status.NEW, Status.ADMIN_WORK, Status.CLARIFYING, Status.BOOKED],
    Status.NO_SHOW: [Status.NEW, Status.ADMIN_WORK, Status.CLARIFYING, Status.BOOKED],
    Status.LOST: [Status.NEW, Status.ADMIN_WORK, Status.CLARIFYING],
}

# Заказы, занимающие место в студии. «Готово» ещё занимает место до выдачи
# или до окончания планового интервала.
CAPACITY_STATUSES: list[str] = [
    Status.BOOKED,
    Status.ACCEPTED,
    Status.IN_PROGRESS,
    Status.READY,
]

MAIN_ACTION: dict[str, dict[str, str]] = {
    Status.NEW: {"to": Status.ADMIN_WORK, "label": "Взять в работу"},
    Status.ADMIN_WORK: {"to": Status.CLARIFYING, "label": "Уточнить детали"},
    Status.CLARIFYING: {"to": Status.BOOKED, "label": "Записать"},
    Status.BOOKED: {"to": Status.ACCEPTED, "label": "Автомобиль принят"},
    Status.ACCEPTED: {"to": Status.IN_PROGRESS, "label": "Начать работы"},
    Status.IN_PROGRESS: {"to": Status.READY, "label": "Автомобиль готов"},
    Status.READY: {"to": Status.ISSUED, "label": "Выдать клиенту"},
}


@dataclass(frozen=True)
class Target:
    value: str
    label: str
    kind: str  # "forward" | "outcome" | "override"
    tone: str

    @property
    def is_outcome(self) -> bool:
        return self.kind == "outcome"


def status_label(value: str) -> str:
    return STATUS_META.get(value, {}).get("label", value)


def status_tone(value: str) -> str:
    return STATUS_META.get(value, {}).get("tone", "new")


def outcomes_available(order: Order) -> bool:
    return order.status in {
        Status.NEW,
        Status.ADMIN_WORK,
        Status.CLARIFYING,
        Status.BOOKED,
    }


def forward_targets(order: Order) -> list[Target]:
    return [
        Target(
            value=value,
            label=status_label(value),
            kind="forward",
            tone=status_tone(value),
        )
        for value in ALLOWED_TRANSITIONS.get(order.status, [])
    ]


def outcome_targets(order: Order) -> list[Target]:
    if not outcomes_available(order):
        return []
    return [
        Target(
            value=value,
            label=status_label(value),
            kind="outcome",
            tone=status_tone(value),
        )
        for value in OUTCOME_STATUSES
    ]


def override_targets(order: Order) -> list[Target]:
    return [
        Target(
            value=value,
            label=status_label(value),
            kind="override",
            tone=status_tone(value),
        )
        for value in OVERRIDE_TARGETS.get(order.status, [])
    ]


def main_action(order: Order) -> dict[str, str] | None:
    action = MAIN_ACTION.get(order.status)
    return dict(action) if action else None


def action_needs_reason(order: Order, target: str) -> bool:
    """Причина обязательна для исправлений и ручных закрытий."""
    if target in OUTCOME_STATUSES:
        return True
    return target not in ALLOWED_TRANSITIONS.get(order.status, [])


def ready_blocker(order: Order) -> str | None:
    """Возвращает причину, по которой нельзя перевести заказ в «Готово»."""
    if order.status != Status.IN_PROGRESS:
        return None
    rows = order.active_service_rows
    if not rows:
        return "В заказе нет активных услуг — завершать нечего."
    pending = [row.name for row in rows if not row.is_done]
    if pending:
        return "Сначала отметьте выполненные услуги: " + ", ".join(pending)
    return None
