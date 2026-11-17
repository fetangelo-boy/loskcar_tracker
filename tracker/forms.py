from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from accounts.models import User

from .models import Client, Comment, Order, Service, Vehicle
from .services import find_client_matches
from .utils import normalize_phone, phone_is_plausible


class DateTimeLocalInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, attrs=None):
        super().__init__(
            attrs=attrs,
            format="%Y-%m-%dT%H:%M",
        )


class DateInputMixin:
    """Единый формат datetime-local для всех форм."""


def _dt_field(label, required=False):
    return forms.DateTimeField(
        label=label,
        required=required,
        widget=DateTimeLocalInput(),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y %H:%M"],
        help_text="Время студии (Europe/Moscow).",
    )


def dt_initial(value):
    if not value:
        return ""
    return timezone.localtime(value).strftime("%Y-%m-%dT%H:%M")


class OrderCreateForm(forms.Form):
    client_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    client_name = forms.CharField(
        label="Имя клиента", max_length=200, required=False
    )
    client_phone = forms.CharField(
        label="Телефон", max_length=50, required=False
    )
    client_telegram = forms.CharField(
        label="Telegram (необязательно)", max_length=120, required=False
    )

    vehicle = forms.ModelChoiceField(
        label="Автомобиль клиента",
        queryset=Vehicle.objects.none(),
        required=False,
        empty_label="— выберите автомобиль —",
    )
    vehicle_make = forms.CharField(
        label="Марка", max_length=80, required=False
    )
    vehicle_model = forms.CharField(
        label="Модель", max_length=80, required=False
    )
    vehicle_plate = forms.CharField(
        label="Госномер (необязательно)", max_length=20, required=False
    )

    services = forms.ModelMultipleChoiceField(
        label="Услуги",
        queryset=Service.objects.filter(is_active=True),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    source = forms.ChoiceField(
        label="Источник",
        choices=Order.Source.choices,
        initial=Order.Source.CALL,
    )
    comment = forms.CharField(
        label="Комментарий / первоначальный запрос",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    desired_at = _dt_field("Желаемое время (необязательно)")
    planned_start = _dt_field("Подтверждённое начало")
    planned_end = _dt_field("Плановое окончание")
    master = forms.ModelChoiceField(
        label="Мастер",
        queryset=User.objects.none(),
        required=False,
        empty_label="— назначить позже —",
    )

    client_choice = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_obj = client
        self.fields["services"].queryset = Service.objects.filter(is_active=True)
        self.fields["master"].queryset = User.objects.filter(
            is_active=True, role=User.Role.MASTER
        ).order_by("first_name", "username")
        if client is not None:
            self.fields["vehicle"].queryset = client.vehicles.all()
            self.fields["client_id"].initial = client.pk
            self.fields["client_name"].initial = client.name
            self.fields["client_phone"].initial = client.phone_raw or client.phone
            self.fields["client_telegram"].initial = client.telegram

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("client_id"):
            name = (cleaned.get("client_name") or "").strip()
            phone = (cleaned.get("client_phone") or "").strip()
            if not name:
                self.add_error("client_name", "Укажите имя клиента.")
            if not phone:
                self.add_error("client_phone", "Укажите телефон клиента.")
            elif not phone_is_plausible(normalize_phone(phone)):
                self.add_error("client_phone", "Проверьте номер телефона.")

        services = cleaned.get("services")
        comment = (cleaned.get("comment") or "").strip()
        if not services and not comment:
            raise ValidationError(
                "Выберите хотя бы одну услугу или опишите первоначальный запрос."
            )

        start, end = cleaned.get("planned_start"), cleaned.get("planned_end")
        if bool(start) != bool(end):
            raise ValidationError(
                "Укажите и начало, и плановое окончание — либо оставьте оба поля пустыми."
            )
        if start and end and end <= start:
            raise ValidationError("Плановое окончание должно быть позже начала.")
        return cleaned


class OrderEditForm(forms.Form):
    version = forms.IntegerField(widget=forms.HiddenInput)
    vehicle = forms.ModelChoiceField(
        label="Автомобиль",
        queryset=Vehicle.objects.none(),
        required=False,
        empty_label="— не указан —",
    )
    source = forms.ChoiceField(label="Источник", choices=Order.Source.choices)
    comment = forms.CharField(
        label="Комментарий / пожелания",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    desired_at = _dt_field("Желаемое время")
    planned_start = _dt_field("Подтверждённое начало")
    planned_end = _dt_field("Плановое окончание")
    master = forms.ModelChoiceField(
        label="Мастер",
        queryset=User.objects.none(),
        required=False,
        empty_label="— не назначен —",
    )

    def __init__(self, *args, order: Order, **kwargs):
        super().__init__(*args, **kwargs)
        self.order = order
        self.fields["vehicle"].queryset = order.client.vehicles.all()
        self.fields["master"].queryset = User.objects.filter(
            is_active=True, role=User.Role.MASTER
        ).order_by("first_name", "username")
        if not self.is_bound:
            self.initial.update(
                {
                    "version": order.version,
                    "vehicle": order.vehicle_id,
                    "source": order.source,
                    "comment": order.comment,
                    "desired_at": dt_initial(order.desired_at),
                    "planned_start": dt_initial(order.planned_start),
                    "planned_end": dt_initial(order.planned_end),
                    "master": order.master_id,
                }
            )

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("planned_start"), cleaned.get("planned_end")
        if bool(start) != bool(end):
            raise ValidationError(
                "Заполните оба поля времени записи либо оставьте оба пустыми."
            )
        if start and end and end <= start:
            raise ValidationError("Плановое окончание должно быть позже начала.")
        return cleaned


class CommentForm(forms.Form):
    kind = forms.ChoiceField(
        label="Тип",
        choices=[
            (Comment.Kind.COMMENT, "Комментарий"),
            (Comment.Kind.PROBLEM, "Проблема / задержка"),
        ],
        initial=Comment.Kind.COMMENT,
    )
    text = forms.CharField(
        label="Текст",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Коротко, если нужно"}),
    )
    expected_at = _dt_field("Новое ожидаемое время (для проблемы)")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("kind") == Comment.Kind.PROBLEM and not (
            cleaned.get("text") or ""
        ).strip():
            raise ValidationError("Опишите проблему или задержку.")
        return cleaned


class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = [
            "name",
            "category",
            "description",
            "base_price",
            "price_is_from",
            "duration_minutes",
            "is_active",
            "is_confirmed",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "base_price": forms.NumberInput(
                attrs={"step": "0.01", "min": "0", "placeholder": "не заполнять, если неизвестно"}
            ),
            "duration_minutes": forms.NumberInput(
                attrs={"step": "5", "min": "0", "placeholder": "не заполнять, если неизвестно"}
            ),
        }
        help_texts = {
            "base_price": "Оставьте пустым, если цена требует оценки. Ноль не подставляется.",
            "duration_minutes": "Оставьте пустым, если длительность не подтверждена.",
            "is_confirmed": "Отметьте, когда владелец подтвердил услугу и цену.",
        }

    def clean_base_price(self):
        value = self.cleaned_data.get("base_price")
        if value is not None and value < 0:
            raise ValidationError("Цена не может быть отрицательной.")
        return value


class ClientEditForm(forms.Form):
    name = forms.CharField(label="Имя", max_length=200)
    phone = forms.CharField(label="Телефон", max_length=50)
    telegram = forms.CharField(
        label="Telegram", max_length=120, required=False
    )
    notes = forms.CharField(
        label="Внутренние заметки",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    is_regular = forms.BooleanField(label="Постоянный клиент", required=False)

    def __init__(self, *args, client: Client, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phone"].initial = client.phone_raw or client.phone
        if not self.is_bound:
            self.initial.update(
                {
                    "name": client.name,
                    "telegram": client.telegram,
                    "notes": client.notes,
                    "is_regular": client.is_regular,
                }
            )


class VehicleForm(forms.Form):
    make = forms.CharField(label="Марка", max_length=80)
    model = forms.CharField(label="Модель", max_length=80)
    plate = forms.CharField(label="Госномер", max_length=20, required=False)


class AddServiceForm(forms.Form):
    service = forms.ModelChoiceField(
        label="Услуга",
        queryset=Service.objects.filter(is_active=True),
        empty_label="— выберите услугу —",
    )


class ServiceRowRemoveForm(forms.Form):
    reason = forms.CharField(label="Причина", max_length=300)


class StatusForm(forms.Form):
    to = forms.ChoiceField(choices=Order.Status.choices)
    version = forms.IntegerField(required=False)
    reason = forms.CharField(required=False, max_length=500)
    override = forms.BooleanField(required=False)


class PhoneLookupForm(forms.Form):
    """Используется для подсказки совпадений на форме новой заявки."""

    phone = forms.CharField(max_length=50)
    name = forms.CharField(max_length=200, required=False)

    def matches(self):
        return find_client_matches(
            self.cleaned_data.get("name", ""), self.cleaned_data.get("phone", "")
        )
