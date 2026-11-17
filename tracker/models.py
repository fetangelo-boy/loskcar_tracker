from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from .utils import format_money, format_phone, normalize_plate


class Client(models.Model):
    name = models.CharField("Имя", max_length=200, db_index=True)
    phone = models.CharField(
        "Телефон (нормализованный)", max_length=24, db_index=True
    )
    phone_raw = models.CharField("Телефон как сообщён", max_length=50, blank=True)
    telegram = models.CharField("Telegram", max_length=120, blank=True)
    notes = models.TextField("Внутренние заметки", blank=True)
    is_regular = models.BooleanField("Постоянный клиент", default=False)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"

    def __str__(self) -> str:
        return f"{self.name} · {self.phone_display}"

    @property
    def phone_display(self) -> str:
        return format_phone(self.phone) or self.phone_raw or "—"

    @property
    def vehicle_count(self) -> int:
        return self.vehicles.count()

    @property
    def orders_count(self) -> int:
        return self.orders.count()


class Vehicle(models.Model):
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="vehicles",
        verbose_name="Клиент",
    )
    make = models.CharField("Марка", max_length=80)
    model = models.CharField("Модель", max_length=80)
    plate = models.CharField("Госномер", max_length=20, blank=True)
    plate_normalized = models.CharField(
        "Госномер (нормализованный)", max_length=20, blank=True, db_index=True
    )
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        ordering = ["make", "model", "id"]
        verbose_name = "Автомобиль"
        verbose_name_plural = "Автомобили"
        constraints = [
            models.UniqueConstraint(
                fields=["client", "plate_normalized"],
                condition=~Q(plate_normalized=""),
                name="uniq_client_plate",
            )
        ]

    def __str__(self) -> str:
        label = f"{self.make} {self.model}".strip()
        return f"{label} · {self.plate}" if self.plate else label

    def save(self, *args, **kwargs):
        self.plate = (self.plate or "").strip().upper()
        self.plate_normalized = normalize_plate(self.plate)
        super().save(*args, **kwargs)

    @property
    def display_name(self) -> str:
        return f"{self.make} {self.model}".strip()


class Service(models.Model):
    """Услуга каталога.

    Цены и длительности из ТЗ не подтверждены, поэтому оба поля допускают
    пустое значение; в интерфейсе пустая цена показывается как
    «требует оценки», а не как ноль.
    """

    name = models.CharField("Название", max_length=200, unique=True)
    category = models.CharField("Категория", max_length=120, blank=True)
    description = models.TextField("Описание", blank=True)
    base_price = models.DecimalField(
        "Базовая цена, ₽",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    price_is_from = models.BooleanField("Цена «от»", default=False)
    duration_minutes = models.PositiveIntegerField(
        "Базовая длительность, мин", null=True, blank=True
    )
    is_active = models.BooleanField("Активна", default=True)
    is_confirmed = models.BooleanField(
        "Подтверждена владельцем", default=False, db_index=True
    )
    created_at = models.DateTimeField("Создана", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлена", auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Услуга"
        verbose_name_plural = "Каталог услуг"

    def __str__(self) -> str:
        return self.name

    @property
    def price_display(self) -> str:
        if self.base_price is None:
            return "требует оценки"
        prefix = "от " if self.price_is_from else ""
        return prefix + format_money(self.base_price)


class Order(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новая заявка"
        ADMIN_WORK = "admin_work", "В работе администратора"
        CLARIFYING = "clarifying", "Уточняются детали"
        BOOKED = "booked", "Записан"
        ACCEPTED = "accepted", "Автомобиль принят"
        IN_PROGRESS = "in_progress", "В работе у мастера"
        READY = "ready", "Готово"
        ISSUED = "issued", "Выдан клиенту"
        DECLINED = "declined", "Отказ"
        CANCELLED = "cancelled", "Отмена клиентом"
        NO_SHOW = "no_show", "Не пришёл"
        LOST = "lost", "Потерян"

    class Source(models.TextChoices):
        SITE = "site", "Сайт"
        CALL = "call", "Звонок"
        MESSENGER = "messenger", "Мессенджер"
        OTHER = "other", "Другое"

    number = models.CharField(
        "Номер", max_length=20, unique=True, null=True, blank=True
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name="Клиент",
    )
    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.SET_NULL,
        related_name="orders",
        verbose_name="Автомобиль",
        null=True,
        blank=True,
    )
    source = models.CharField(
        "Источник", max_length=20, choices=Source.choices, default=Source.CALL
    )
    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.NEW
    )
    comment = models.TextField("Комментарий / пожелания", blank=True)

    desired_at = models.DateTimeField("Желаемое время", null=True, blank=True)
    planned_start = models.DateTimeField("Подтверждённое начало", null=True, blank=True)
    planned_end = models.DateTimeField("Плановое окончание", null=True, blank=True)

    administrator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="Администратор",
        null=True,
        blank=True,
    )
    master = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="Мастер",
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="Создал",
        null=True,
        blank=True,
    )

    problem_flag = models.BooleanField("Есть проблема / задержка", default=False)
    version = models.PositiveIntegerField("Версия", default=1)
    external_id = models.CharField("Внешний идентификатор", max_length=120, blank=True)

    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Заказ"
        verbose_name_plural = "Заказы"
        indexes = [
            models.Index(fields=["status", "planned_start"]),
            models.Index(fields=["created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "external_id"],
                condition=~Q(external_id=""),
                name="uniq_source_external_id",
            )
        ]

    def __str__(self) -> str:
        return f"{self.display_number} · {self.client.name}"

    def save(self, *args, **kwargs):
        creating = self._state.adding
        super().save(*args, **kwargs)
        if creating and not self.number:
            number = f"LC-{self.pk:05d}"
            Order.objects.filter(pk=self.pk).update(number=number)
            self.number = number

    @property
    def display_number(self) -> str:
        if not self.pk:
            return "LC-новый"
        return self.number or f"LC-{self.pk:05d}"

    @property
    def is_outcome(self) -> bool:
        return self.status in {
            self.Status.DECLINED,
            self.Status.CANCELLED,
            self.Status.NO_SHOW,
            self.Status.LOST,
        }

    @property
    def is_production(self) -> bool:
        return self.status in {
            self.Status.BOOKED,
            self.Status.ACCEPTED,
            self.Status.IN_PROGRESS,
            self.Status.READY,
            self.Status.ISSUED,
        }

    @property
    def is_lead(self) -> bool:
        return self.status in {
            self.Status.NEW,
            self.Status.ADMIN_WORK,
            self.Status.CLARIFYING,
        }

    @property
    def active_service_rows(self) -> list[OrderService]:
        return [row for row in self.services.all() if row.is_active]

    @property
    def services_are_done(self) -> bool:
        rows = self.active_service_rows
        return bool(rows) and all(row.is_done for row in rows)

    @property
    def service_names_short(self) -> str:
        names = [row.name for row in self.active_service_rows]
        if not names:
            return "Услуги не выбраны"
        if len(names) <= 2:
            return ", ".join(names)
        return f"{names[0]}, {names[1]} +{len(names) - 2}"

    @property
    def services_total(self) -> Decimal:
        total = Decimal("0")
        for row in self.active_service_rows:
            if row.unit_price is not None:
                total += row.unit_price
        return total

    @property
    def services_unknown_count(self) -> int:
        return len([row for row in self.active_service_rows if row.unit_price is None])

    @property
    def services_known_count(self) -> int:
        return len([row for row in self.active_service_rows if row.unit_price is not None])

    @property
    def duration_minutes(self) -> int:
        return sum(
            row.duration_minutes or 0 for row in self.active_service_rows
        )

    @property
    def duration_has_unknown(self) -> bool:
        return any(row.duration_minutes is None for row in self.active_service_rows)

    @property
    def vehicle_label(self) -> str:
        if not self.vehicle:
            return "Автомобиль не указан"
        return str(self.vehicle)


class OrderService(models.Model):
    """Историческая строка работ: название и цена фиксируются на момент выбора."""

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="services",
        verbose_name="Заказ",
    )
    service = models.ForeignKey(
        Service,
        on_delete=models.SET_NULL,
        related_name="order_rows",
        verbose_name="Услуга каталога",
        null=True,
        blank=True,
    )
    name = models.CharField("Название на момент заказа", max_length=200)
    unit_price = models.DecimalField(
        "Цена на момент заказа, ₽",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    price_is_from = models.BooleanField("Цена «от»", default=False)
    duration_minutes = models.PositiveIntegerField(
        "Длительность, мин", null=True, blank=True
    )
    is_active = models.BooleanField("Активна", default=True)
    is_done = models.BooleanField("Выполнена", default=False)
    done_at = models.DateTimeField("Выполнена в", null=True, blank=True)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="Отметил",
        null=True,
        blank=True,
    )
    position = models.PositiveIntegerField("Порядок", default=0)
    created_at = models.DateTimeField("Добавлена", auto_now_add=True)

    class Meta:
        ordering = ["position", "id"]
        verbose_name = "Строка работ"
        verbose_name_plural = "Строки работ"

    def __str__(self) -> str:
        return self.name

    @property
    def price_display(self) -> str:
        if self.unit_price is None:
            return "требует оценки"
        prefix = "от " if self.price_is_from else ""
        return prefix + format_money(self.unit_price)


class Comment(models.Model):
    class Kind(models.TextChoices):
        COMMENT = "comment", "Комментарий"
        PROBLEM = "problem", "Проблема / задержка"
        ADDITIONAL_OFFER = "additional_offer", "Допуслуга (задел)"
        SYSTEM = "system", "Система"

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="comments",
        verbose_name="Заказ",
    )
    kind = models.CharField(
        "Тип", max_length=30, choices=Kind.choices, default=Kind.COMMENT
    )
    text = models.TextField("Текст")
    expected_at = models.DateTimeField(
        "Новое ожидаемое время", null=True, blank=True
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="Автор",
        null=True,
        blank=True,
    )
    resolved_at = models.DateTimeField("Решено", null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="Решил",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Комментарий"
        verbose_name_plural = "Комментарии"

    def __str__(self) -> str:
        return f"{self.get_kind_display()} · {self.order.display_number}"


class OrderEvent(models.Model):
    """История заказа: статусы, услуги, правки. Используется в таймлайне."""

    class Kind(models.TextChoices):
        CREATED = "created", "Заказ создан"
        STATUS = "status", "Смена статуса"
        STATUS_OVERRIDE = "status_override", "Исправление статуса"
        SERVICE_DONE = "service_done", "Услуга выполнена"
        SERVICE_UNDONE = "service_undone", "Отметка услуги снята"
        SERVICE_ADDED = "service_added", "Услуга добавлена"
        SERVICE_REMOVED = "service_removed", "Услуга убрана"
        COMMENT = "comment", "Комментарий"
        PROBLEM = "problem", "Проблема / задержка"
        PROBLEM_RESOLVED = "problem_resolved", "Проблема решена"
        UPDATE = "update", "Заказ изменён"
        BOOKING = "booking", "Запись изменена"

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="events",
        verbose_name="Заказ",
    )
    kind = models.CharField("Тип", max_length=30, choices=Kind.choices)
    from_status = models.CharField("Из статуса", max_length=20, blank=True)
    to_status = models.CharField("В статус", max_length=20, blank=True)
    note = models.TextField("Примечание", blank=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="Автор",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Событие заказа"
        verbose_name_plural = "События заказов"
        indexes = [models.Index(fields=["order", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.kind} · {self.order_id}"


class AuditLog(models.Model):
    """Общий журнал действий. В MVP пишется для заказов/клиентов/каталога."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="Автор",
        null=True,
        blank=True,
    )
    action = models.CharField("Действие", max_length=80)
    entity_type = models.CharField("Тип объекта", max_length=80)
    entity_id = models.CharField("ID объекта", max_length=40, blank=True)
    summary = models.CharField("Кратко", max_length=300, blank=True)
    details = models.JSONField("Детали", default=dict, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Запись аудита"
        verbose_name_plural = "Аудит"
        indexes = [models.Index(fields=["entity_type", "entity_id"])]

    def __str__(self) -> str:
        return f"{self.action} · {self.entity_type}#{self.entity_id}"


def assign_order_number(order: Order) -> None:
    """Служебный помощник на случай ручного создания через shell/admin."""
    if order.pk and not order.number:
        order.number = f"LC-{order.pk:05d}"
        Order.objects.filter(pk=order.pk).update(number=order.number)
