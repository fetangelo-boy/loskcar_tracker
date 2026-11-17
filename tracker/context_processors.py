from django.conf import settings

from .access import (
    can_create_order,
    can_manage_catalog,
    can_view_catalog,
    can_view_prices,
    is_master,
)


def tracker_context(request):
    user = getattr(request, "user", None)
    actor = user if user is not None and user.is_authenticated else None
    return {
        "WORKSHOP_CAPACITY": settings.WORKSHOP_CAPACITY,
        "TRACKER_ACTOR": actor,
        "can_create_order": can_create_order(user),
        "can_view_catalog": can_view_catalog(user),
        "can_manage_catalog": can_manage_catalog(user),
        "can_view_prices": can_view_prices(user),
        "is_master_role": is_master(user),
    }
