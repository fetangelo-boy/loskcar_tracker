from django.urls import path

from . import api, views

app_name = "tracker"

urlpatterns = [
    path("", views.board, name="board"),
    path("board/fragment/", views.board_fragment, name="board_fragment"),
    path("calendar/", views.calendar_view, name="calendar"),
    path("master/", views.master_screen, name="master_screen"),
    path("master/pulse/", views.master_pulse, name="master_pulse"),
    path("orders/", views.order_list, name="order_list"),
    path("orders/new/", views.order_create, name="order_create"),
    path("orders/<int:pk>/", views.order_detail, name="order_detail"),
    path("orders/<int:pk>/edit/", views.order_edit, name="order_edit"),
    path("orders/<int:pk>/status/", views.order_status, name="order_status"),
    path(
        "orders/<int:pk>/services/add/",
        views.order_add_service,
        name="order_add_service",
    ),
    path(
        "orders/<int:pk>/services/<int:row_id>/remove/",
        views.order_remove_service,
        name="order_remove_service",
    ),
    path(
        "orders/<int:pk>/services/<int:row_id>/toggle/",
        views.order_toggle_service,
        name="order_toggle_service",
    ),
    path("orders/<int:pk>/comments/", views.order_comment, name="order_comment"),
    path(
        "orders/<int:pk>/problems/resolve/",
        views.order_resolve_problems,
        name="order_resolve_problems",
    ),
    path("orders/<int:pk>/pulse/", views.order_pulse, name="order_pulse"),
    path("clients/", views.client_list, name="client_list"),
    path("clients/<int:pk>/", views.client_detail, name="client_detail"),
    path("clients/<int:pk>/edit/", views.client_edit, name="client_edit"),
    path(
        "clients/<int:pk>/vehicles/add/",
        views.client_vehicle_add,
        name="client_vehicle_add",
    ),
    path("services/", views.service_list, name="service_list"),
    path("services/new/", views.service_create, name="service_create"),
    path("services/<int:pk>/edit/", views.service_edit, name="service_edit"),
    path("services/<int:pk>/toggle/", views.service_toggle, name="service_toggle"),
    path("api/leads/", api.lead_webhook, name="lead_webhook"),
]
