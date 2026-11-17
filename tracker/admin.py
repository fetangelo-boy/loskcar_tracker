from django.contrib import admin

from .models import (
    AuditLog,
    Client,
    Comment,
    Order,
    OrderEvent,
    OrderService,
    Service,
    Vehicle,
)


class OrderServiceInline(admin.TabularInline):
    model = OrderService
    extra = 0
    fields = (
        "name",
        "service",
        "unit_price",
        "price_is_from",
        "duration_minutes",
        "is_active",
        "is_done",
        "position",
    )


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "is_regular", "created_at")
    search_fields = ("name", "phone", "phone_raw", "telegram")
    list_filter = ("is_regular",)


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("make", "model", "plate", "client")
    search_fields = ("make", "model", "plate", "plate_normalized", "client__name")
    list_select_related = ("client",)


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "base_price",
        "price_is_from",
        "duration_minutes",
        "is_active",
        "is_confirmed",
    )
    list_filter = ("is_active", "is_confirmed", "price_is_from")
    search_fields = ("name", "category", "description")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "number",
        "client",
        "vehicle",
        "status",
        "source",
        "planned_start",
        "master",
        "created_at",
    )
    list_filter = ("status", "source", "master")
    search_fields = ("number", "client__name", "client__phone", "vehicle__plate")
    date_hierarchy = "created_at"
    list_select_related = ("client", "vehicle", "master")
    inlines = [OrderServiceInline]
    readonly_fields = ("version", "created_at", "updated_at", "external_id")


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("order", "kind", "author", "created_at", "resolved_at")
    list_filter = ("kind",)
    search_fields = ("order__number", "text")
    list_select_related = ("order", "author")


@admin.register(OrderEvent)
class OrderEventAdmin(admin.ModelAdmin):
    list_display = ("order", "kind", "from_status", "to_status", "author", "created_at")
    list_filter = ("kind",)
    search_fields = ("order__number", "note")
    list_select_related = ("order", "author")
    readonly_fields = [field.name for field in OrderEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "entity_type", "entity_id", "actor", "created_at")
    list_filter = ("action", "entity_type")
    search_fields = ("summary", "entity_id")
    readonly_fields = [field.name for field in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
