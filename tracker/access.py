"""Права ролей и фильтрация действий на backend.

Правила живут здесь, а не в шаблонах: скрытая кнопка не считается защитой.
Все проверки должны срабатывать и для прямого POST-запроса по URL.
"""

from __future__ import annotations

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from accounts.models import User

from .workflow import (
    Status,
    forward_targets,
    main_action,
    outcome_targets,
    override_targets,
)

# Производственные переходы, которые мастер выполняет сам.
# Администратор и владелец могут делать их за мастера и управлять остальными
# этапами канбана.
MASTER_FORWARD_TARGETS: dict[str, set[str]] = {
    Status.BOOKED: {Status.ACCEPTED},
    Status.ACCEPTED: {Status.IN_PROGRESS},
    Status.IN_PROGRESS: {Status.READY},
}


def _role(user) -> str | None:
    return getattr(user, "role", None) if user is not None else None


def is_owner(user) -> bool:
    return _role(user) == User.Role.OWNER


def is_admin(user) -> bool:
    return _role(user) == User.Role.ADMIN


def is_master(user) -> bool:
    return _role(user) == User.Role.MASTER


def can_view_catalog(user) -> bool:
    """Каталог содержит цены, поэтому мастер его не открывает."""
    return is_owner(user) or is_admin(user)


def can_manage_catalog(user) -> bool:
    return is_owner(user)


def can_view_prices(user) -> bool:
    """Предварительную стоимость видят владелец и администратор."""
    return is_owner(user) or is_admin(user)


def can_create_order(user) -> bool:
    return is_owner(user) or is_admin(user)


def can_edit_order(user) -> bool:
    return is_owner(user) or is_admin(user)


def can_manage_clients(user) -> bool:
    return is_owner(user) or is_admin(user)


def can_view_internal_notes(user) -> bool:
    return is_owner(user) or is_admin(user)


def can_override_status(user) -> bool:
    return is_owner(user) or is_admin(user)


def can_add_service(user) -> bool:
    return is_owner(user) or is_admin(user)


def can_remove_service(user) -> bool:
    """Мастер не снимает согласованную услугу — только владелец/администратор."""
    return is_owner(user) or is_admin(user)


def can_toggle_service(user) -> bool:
    return is_owner(user) or is_admin(user) or is_master(user)


def can_resolve_problems(user) -> bool:
    return is_owner(user) or is_admin(user)


def role_required(*roles: str):
    """Декоратор: требует вход и одну из указанных ролей."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if _role(request.user) not in roles:
                raise PermissionDenied("Недостаточно прав для этого действия.")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def forward_targets_for(user, order):
    """Переходы, доступные конкретной роли."""
    targets = forward_targets(order)
    if is_master(user):
        allowed = MASTER_FORWARD_TARGETS.get(order.status, set())
        return [target for target in targets if target.value in allowed]
    return targets


def main_action_for(user, order):
    """Главное действие следующего шага с учётом роли."""
    action = main_action(order)
    if action is None:
        return None
    if is_master(user):
        allowed = MASTER_FORWARD_TARGETS.get(order.status, set())
        if action["to"] not in allowed:
            return None
    return action


def outcome_targets_for(user, order):
    """Закрытие заказа доступно только владельцу и администратору."""
    if not can_override_status(user):
        return []
    return outcome_targets(order)


def override_targets_for(user, order):
    if not can_override_status(user):
        return []
    return override_targets(order)


def can_change_status(user, order, target: str) -> bool:
    """Совпадает ли целевой статус с доступным переходом роли."""
    return target in {item.value for item in forward_targets_for(user, order)}
