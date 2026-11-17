import json
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User

from .models import AuditLog, Client, Comment, Order, OrderService, Service, Vehicle
from .services import (
    StaleOrderError,
    WorkflowError,
    capacity_conflicts,
    create_order,
    current_actor,
    find_client_matches,
    set_order_status,
    toggle_service_done,
)
from .utils import normalize_phone
from .calendar import build_day, day_schedule, work_hours


def date_at(day_offset: int, hour: int) -> datetime:
    base = timezone.localtime() + timedelta(days=day_offset)
    return timezone.make_aware(
        datetime(base.year, base.month, base.day, hour, 0),
        timezone.get_current_timezone(),
    )


class BaseTrackerTest(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(
            "owner_test", password="x", role=User.Role.OWNER, first_name="Владелец"
        )
        self.admin = User.objects.create_user(
            "admin_test",
            password="x",
            role=User.Role.ADMIN,
            first_name="Администратор",
        )
        self.master = User.objects.create_user(
            "master_test", password="x", role=User.Role.MASTER, first_name="Мастер"
        )
        self.client_obj = Client.objects.create(
            name="Иван Петров", phone="+79990000001", phone_raw="8 999 000-00-01"
        )
        self.vehicle = Vehicle.objects.create(
            client=self.client_obj, make="Toyota", model="Camry", plate="А123ВС77"
        )
        self.service_a = Service.objects.create(name="Химчистка салона")
        self.service_b = Service.objects.create(name="Полировка кузова и фар")

    def make_order(self, *, with_times=True, services=None, status=None):
        now = timezone.localtime()
        start = date_at(10, 10) if with_times else None
        end = date_at(10, 13) if with_times else None
        order = create_order(
            client=self.client_obj,
            vehicle=self.vehicle,
            services=services or [self.service_a],
            planned_start=start,
            planned_end=end,
            master=self.master,
            actor=self.owner,
        )
        if status:
            set_order_status(order, status, actor=self.owner)
        return order


class PhoneTests(TestCase):
    def test_russian_phone_variants_normalize(self):
        self.assertEqual(normalize_phone("8 (999) 123-45-67"), "+79991234567")
        self.assertEqual(normalize_phone("+7 999 123 45 67"), "+79991234567")
        self.assertEqual(normalize_phone("9991234567"), "+79991234567")
        self.assertEqual(normalize_phone(""), "")

    def test_client_match_by_normalized_phone(self):
        client = Client.objects.create(name="Тест", phone="+79991112233")
        matches = list(find_client_matches("", "8 999 111-22-33"))
        self.assertIn(client, matches)


class OrderBasicsTests(BaseTrackerTest):
    def test_order_number_assigned(self):
        order = self.make_order()
        self.assertEqual(order.number, f"LC-{order.pk:05d}")
        self.assertEqual(order.services.first().name, "Химчистка салона")

    def test_price_is_not_invented(self):
        order = self.make_order()
        row = order.services.first()
        self.assertIsNone(row.unit_price)
        self.assertEqual(row.price_display, "требует оценки")
        self.assertIsNone(self.service_a.base_price)

    def test_service_snapshot_survives_catalog_change(self):
        order = self.make_order()
        row = order.services.first()
        self.assertIsNone(row.unit_price)
        self.service_a.base_price = Decimal("7000.00")
        self.service_a.save(update_fields=["base_price"])
        row.refresh_from_db()
        self.assertIsNone(row.unit_price)


class StatusWorkflowTests(BaseTrackerTest):
    def test_cannot_skip_production_steps(self):
        order = self.make_order()
        set_order_status(order, Order.Status.BOOKED, actor=self.owner)
        with self.assertRaises(WorkflowError):
            set_order_status(order, Order.Status.READY, actor=self.owner)

    def test_booking_requires_services_vehicle_and_interval(self):
        order = create_order(
            client=self.client_obj,
            vehicle=None,
            services=[self.service_a],
            comment="Без автомобиля",
            actor=self.owner,
        )
        with self.assertRaises(WorkflowError):
            set_order_status(order, Order.Status.BOOKED, actor=self.owner)

    def test_ready_requires_all_services_done(self):
        order = self.make_order()
        set_order_status(order, Order.Status.BOOKED, actor=self.owner)
        set_order_status(order, Order.Status.ACCEPTED, actor=self.owner)
        set_order_status(order, Order.Status.IN_PROGRESS, actor=self.owner)
        self.master_order = order
        row = order.services.first()
        with self.assertRaises(WorkflowError):
            set_order_status(order, Order.Status.READY, actor=self.owner)
        toggle_service_done(row, done=True, actor=self.owner)
        order.refresh_from_db()
        updated = set_order_status(order, Order.Status.READY, actor=self.owner)
        self.assertEqual(updated.status, Order.Status.READY)

    def test_outcome_requires_reason(self):
        order = self.make_order()
        with self.assertRaises(WorkflowError):
            set_order_status(order, Order.Status.DECLINED, actor=self.owner)
        updated = set_order_status(
            order, Order.Status.DECLINED, actor=self.owner, reason="Клиент отказался"
        )
        self.assertEqual(updated.status, Order.Status.DECLINED)

    def test_version_conflict_is_not_silent(self):
        order = self.make_order()
        with self.assertRaises(StaleOrderError):
            set_order_status(
                order,
                Order.Status.ADMIN_WORK,
                actor=self.owner,
                expected_version=order.version + 5,
            )

    def test_override_requires_reason_and_logs(self):
        order = self.make_order()
        set_order_status(order, Order.Status.BOOKED, actor=self.owner)
        set_order_status(order, Order.Status.ACCEPTED, actor=self.owner)
        with self.assertRaises(WorkflowError):
            set_order_status(
                order,
                Order.Status.BOOKED,
                actor=self.owner,
                allow_override=True,
            )
        updated = set_order_status(
            order,
            Order.Status.BOOKED,
            actor=self.owner,
            allow_override=True,
            reason="Ошибочно приняли автомобиль",
        )
        self.assertEqual(updated.status, Order.Status.BOOKED)
        self.assertTrue(
            updated.events.filter(kind="status_override", note__contains="Ошибочно").exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(action="order_status_override").exists()
        )

    def test_master_cannot_override(self):
        order = self.make_order()
        set_order_status(order, Order.Status.BOOKED, actor=self.owner)
        with self.assertRaises(WorkflowError):
            set_order_status(
                order,
                Order.Status.CLARIFYING,
                actor=self.master,
                allow_override=True,
                reason="Нельзя",
            )

    def test_service_done_only_after_acceptance(self):
        order = self.make_order()
        with self.assertRaises(WorkflowError):
            toggle_service_done(
                order.services.first(), done=True, actor=self.master
            )


class CapacityTests(BaseTrackerTest):
    def test_third_overlapping_booking_rejected(self):
        second_client = Client.objects.create(name="Второй", phone="+79990000002")
        second_vehicle = Vehicle.objects.create(
            client=second_client, make="Kia", model="Rio"
        )
        third_client = Client.objects.create(name="Третий", phone="+79990000003")
        third_vehicle = Vehicle.objects.create(
            client=third_client, make="Audi", model="A6"
        )

        first = create_order(
            client=self.client_obj,
            vehicle=self.vehicle,
            services=[self.service_a],
            planned_start=date_at(20, 10),
            planned_end=date_at(20, 12),
            actor=self.owner,
        )
        set_order_status(first, Order.Status.BOOKED, actor=self.owner)

        second = create_order(
            client=second_client,
            vehicle=second_vehicle,
            services=[self.service_a],
            planned_start=date_at(20, 12),
            planned_end=date_at(20, 14),
            actor=self.owner,
        )
        set_order_status(second, Order.Status.BOOKED, actor=self.owner)

        third = create_order(
            client=third_client,
            vehicle=third_vehicle,
            services=[self.service_a],
            planned_start=date_at(20, 11),
            planned_end=date_at(20, 13),
            actor=self.owner,
        )
        self.assertEqual(
            capacity_conflicts(third.planned_start, third.planned_end), 2
        )
        with self.assertRaises(WorkflowError):
            set_order_status(third, Order.Status.BOOKED, actor=self.owner)

    def test_touching_intervals_do_not_conflict(self):
        now = timezone.localtime()
        start = date_at(21, 10)
        end = date_at(21, 12)
        order = create_order(
            client=self.client_obj,
            vehicle=self.vehicle,
            services=[self.service_a],
            planned_start=start,
            planned_end=end,
            actor=self.owner,
        )
        set_order_status(order, Order.Status.BOOKED, actor=self.owner)
        self.assertEqual(capacity_conflicts(end, date_at(21, 14)), 0)


@override_settings(LEAD_API_KEY="test-lead-key")
class LeadApiTests(BaseTrackerTest):
    def payload(self, **overrides):
        data = {
            "external_id": "site-event-1",
            "name": "Новый Клиент",
            "phone": "+7 999 222-33-44",
            "services": ["Химчистка салона"],
            "comment": "Хочу записаться",
            "vehicle": {"make": "Lada", "model": "Vesta", "plate": "О001АА77"},
        }
        data.update(overrides)
        return data

    def post(self, payload, key="test-lead-key"):
        return self.client.post(
            reverse("tracker:lead_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_API_KEY=key,
        )

    def test_webhook_creates_order_and_is_idempotent(self):
        first = self.post(self.payload())
        self.assertEqual(first.status_code, 201, first.content)
        data = first.json()
        self.assertTrue(data["ok"])
        self.assertEqual(Order.objects.filter(external_id="site-event-1").count(), 1)

        second = self.post(self.payload())
        self.assertEqual(second.status_code, 200, second.content)
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(Order.objects.filter(external_id="site-event-1").count(), 1)

    def test_webhook_rejects_bad_key(self):
        response = self.post(self.payload(), key="wrong")
        self.assertEqual(response.status_code, 403)

    def test_webhook_requires_external_id(self):
        response = self.post(self.payload(external_id=""))
        self.assertEqual(response.status_code, 400)

    def test_webhook_keeps_unknown_service_as_comment(self):
        response = self.post(
            self.payload(
                external_id="site-event-2",
                services=["Неизвестная услуга с сайта"],
            )
        )
        self.assertEqual(response.status_code, 201, response.content)
        order = Order.objects.get(external_id="site-event-2")
        self.assertIn("Неизвестная услуга с сайта", order.comment)
        self.assertFalse(order.services.exists())


class ViewTests(BaseTrackerTest):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_board_renders_eight_columns(self):
        self.make_order()
        response = self.client.get(reverse("tracker:board"))
        self.assertEqual(response.status_code, 200)
        for label in [
            "Новая заявка",
            "В работе администратора",
            "Уточняются детали",
            "Записан",
            "Автомобиль принят",
            "В работе у мастера",
            "Готово",
            "Выдан клиенту",
        ]:
            self.assertContains(response, label)
        self.assertEqual(len(response.context["columns"]), 8)

    def test_outcome_not_on_board_but_available_in_list(self):
        order = self.make_order()
        set_order_status(
            order, Order.Status.DECLINED, actor=self.owner, reason="Нет"
        )
        response = self.client.get(reverse("tracker:board"))
        self.assertNotContains(response, order.display_number)
        response = self.client.get(reverse("tracker:board"), {"closed": "1"})
        self.assertNotContains(response, order.display_number)

        response = self.client.get(
            reverse("tracker:order_list"), {"status": Order.Status.DECLINED}
        )
        self.assertContains(response, order.display_number)

    def test_order_detail_and_list_render(self):
        order = self.make_order()
        self.assertEqual(
            self.client.get(reverse("tracker:order_detail", args=[order.pk])).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse("tracker:order_list")).status_code, 200
        )

    def test_new_order_offers_existing_client_and_does_not_duplicate_silently(self):
        url = reverse("tracker:order_create")
        response = self.client.post(
            url,
            {
                "client_name": "Иван Петров",
                "client_phone": "8 999 000-00-01",
                "source": Order.Source.CALL,
                "services": [self.service_a.pk],
                "comment": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["needs_client_choice"])
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Client.objects.filter(phone="+79990000001").count(), 1)

        response = self.client.post(
            url,
            {
                "client_name": "Иван Петров",
                "client_phone": "8 999 000-00-01",
                "source": Order.Source.CALL,
                "services": [self.service_a.pk],
                "comment": "",
                "client_choice": str(self.client_obj.pk),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Client.objects.count(), 1)


class AuthenticationTests(BaseTrackerTest):
    def test_anonymous_cannot_open_working_pages(self):
        for name in ["tracker:board", "tracker:order_list", "tracker:client_list"]:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn(reverse("login"), response["Location"])

        order = self.make_order()
        response = self.client.get(reverse("tracker:order_detail", args=[order.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_health_and_login_are_public(self):
        self.assertEqual(self.client.get(reverse("health")).status_code, 200)
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)

    def test_login_and_logout_work(self):
        response = self.client.post(
            reverse("login"),
            {"username": "owner_test", "password": "x"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("tracker:board"))
        self.assertEqual(self.client.get(reverse("tracker:board")).status_code, 200)

        response = self.client.post(reverse("logout"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.client.get(reverse("tracker:board")).status_code, 302
        )

    def test_inactive_user_loses_session(self):
        self.client.force_login(self.master)
        self.master.is_active = False
        self.master.save(update_fields=["is_active"])
        response = self.client.get(reverse("tracker:board"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_password_change_page_renders(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("password_change"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Смена пароля")

    def test_owner_can_manage_catalog(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("tracker:service_list")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("tracker:service_create")).status_code, 200
        )

    def test_admin_views_catalog_but_cannot_change_it(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("tracker:service_list")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("tracker:service_create")).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                reverse("tracker:service_toggle", args=[self.service_a.pk])
            ).status_code,
            403,
        )

    def test_master_cannot_create_orders_or_open_catalog(self):
        self.client.force_login(self.master)
        self.assertEqual(self.client.get(reverse("tracker:board")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("tracker:order_create")).status_code, 403
        )
        self.assertEqual(self.client.get(reverse("tracker:service_list")).status_code, 403)
        self.assertEqual(
            self.client.post(
                reverse("tracker:client_edit", args=[self.client_obj.pk])
            ).status_code,
            403,
        )

    def test_master_does_not_receive_prices_in_html(self):
        self.service_a.base_price = Decimal("7000.00")
        self.service_a.save(update_fields=["base_price"])
        order = self.make_order()
        self.client.force_login(self.master)
        response = self.client.get(reverse("tracker:order_detail", args=[order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_view_prices"])
        self.assertNotContains(response, "7 000")
        self.assertNotContains(response, "Предварительная сумма")

        self.client.force_login(self.owner)
        response = self.client.get(reverse("tracker:order_detail", args=[order.pk]))
        self.assertTrue(response.context["can_view_prices"])
        self.assertContains(response, "7 000")

    def test_master_only_changes_production_statuses(self):
        order = self.make_order()
        with self.assertRaises(WorkflowError):
            set_order_status(order, Order.Status.ADMIN_WORK, actor=self.master)

        set_order_status(order, Order.Status.BOOKED, actor=self.owner)
        set_order_status(order, Order.Status.ACCEPTED, actor=self.master)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.ACCEPTED)

        with self.assertRaises(WorkflowError):
            set_order_status(
                order,
                Order.Status.DECLINED,
                actor=self.master,
                reason="Нельзя мастеру",
            )

    def test_master_cannot_remove_service(self):
        order = self.make_order()
        row = order.services.first()
        with self.assertRaises(WorkflowError):
            from .services import remove_service_row

            remove_service_row(row, actor=self.master, reason="Нельзя")


class CalendarTests(BaseTrackerTest):
    def setUp(self):
        super().setUp()
        self.day = timezone.localdate() + timedelta(days=10)
        self.order = self.make_order()
        set_order_status(self.order, Order.Status.BOOKED, actor=self.owner)

    def get_calendar(self, **params):
        params.setdefault("date", self.day.isoformat())
        return self.client.get(reverse("tracker:calendar"), params)

    def test_calendar_requires_login(self):
        response = self.client.get(reverse("tracker:calendar"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_week_view_lists_confirmed_booking(self):
        self.client.force_login(self.admin)
        response = self.get_calendar()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Календарь")
        self.assertContains(response, "Записей: 1")
        self.assertContains(response, "Пересечений нет")
        self.assertContains(response, self.order.display_number)
        self.assertEqual(response.context["total_count"], 1)
        self.assertEqual(response.context["conflict_total"], 0)

    def test_day_view_shows_details_table(self):
        self.client.force_login(self.owner)
        response = self.get_calendar(view="day")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Записи дня")
        self.assertContains(response, self.client_obj.name)
        self.assertContains(response, "А123ВС77")
        self.assertEqual(response.context["schedules"][0].count, 1)

    def test_unbooked_lead_is_not_shown(self):
        lead = create_order(
            client=self.client_obj,
            vehicle=self.vehicle,
            services=[self.service_a],
            comment="Ещё не записан",
            planned_start=date_at(10, 14),
            planned_end=date_at(10, 16),
            actor=self.owner,
        )
        self.assertEqual(lead.status, Order.Status.NEW)
        self.client.force_login(self.owner)
        response = self.get_calendar()
        self.assertEqual(response.context["total_count"], 1)
        self.assertNotContains(response, lead.display_number)

    def test_master_filter_limits_period(self):
        other_master = User.objects.create_user(
            "master2_test",
            password="x",
            role=User.Role.MASTER,
            first_name="Пётр",
        )
        second = create_order(
            client=self.client_obj,
            vehicle=self.vehicle,
            services=[self.service_b],
            planned_start=date_at(10, 15),
            planned_end=date_at(10, 16),
            master=other_master,
            actor=self.owner,
        )
        set_order_status(second, Order.Status.BOOKED, actor=self.owner)

        self.client.force_login(self.owner)
        response = self.get_calendar(master=other_master.pk)
        self.assertEqual(response.context["total_count"], 1)
        self.assertContains(response, second.display_number)
        self.assertNotContains(response, self.order.display_number)

    def test_third_overlap_is_flagged_not_hidden(self):
        second = create_order(
            client=self.client_obj,
            vehicle=self.vehicle,
            services=[self.service_b],
            planned_start=date_at(10, 10),
            planned_end=date_at(10, 12),
            master=self.master,
            actor=self.owner,
        )
        set_order_status(second, Order.Status.BOOKED, actor=self.owner)

        third = create_order(
            client=self.client_obj,
            vehicle=self.vehicle,
            services=[self.service_a],
            planned_start=date_at(10, 11),
            planned_end=date_at(10, 14),
            master=self.master,
            actor=self.owner,
        )
        # Конфликт, попавший в базу раньше проверки: новые сервер не пропускает,
        # но уже существующие не удаляет и не скрывает.
        Order.objects.filter(pk=third.pk).update(status=Order.Status.BOOKED)

        self.client.force_login(self.owner)
        response = self.get_calendar(view="day")
        self.assertEqual(response.context["conflict_total"], 1)
        self.assertEqual(response.context["schedules"][0].peak, 3)
        self.assertContains(response, "Пересечений сверх вместимости: 1")
        self.assertContains(response, third.display_number)
        self.assertContains(response, "пересечение")

    @override_settings(WORKSHOP_OPEN_HOUR=None, WORKSHOP_CLOSE_HOUR=None)
    def test_unconfirmed_hours_show_full_day_with_note(self):
        self.assertEqual(work_hours(), (0, 24, False))
        self.client.force_login(self.owner)
        response = self.get_calendar(view="day")
        self.assertFalse(response.context["hours_confirmed"])
        self.assertContains(response, "Рабочие часы студии ещё не подтверждены")
        self.assertNotContains(response, "Есть место для ещё одной записи")

    @override_settings(WORKSHOP_OPEN_HOUR=10, WORKSHOP_CLOSE_HOUR=20)
    def test_confirmed_hours_narrow_grid_and_show_free_windows(self):
        self.assertEqual(work_hours(), (10, 20, True))
        self.client.force_login(self.owner)
        response = self.get_calendar(view="day")
        self.assertTrue(response.context["hours_confirmed"])
        self.assertContains(response, "Есть место для ещё одной записи")
        # Одна машина занимает одно из двух мест, поэтому записать можно в любой час.
        self.assertContains(response, "10:00–20:00")
        self.assertNotContains(response, "не подтверждены")

    @override_settings(WORKSHOP_OPEN_HOUR=10, WORKSHOP_CLOSE_HOUR=20)
    def test_booking_outside_hours_is_clipped_not_dropped(self):
        early = create_order(
            client=self.client_obj,
            vehicle=self.vehicle,
            services=[self.service_b],
            planned_start=date_at(11, 8),
            planned_end=date_at(11, 11),
            actor=self.owner,
        )
        set_order_status(early, Order.Status.BOOKED, actor=self.owner)
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("tracker:calendar"),
            {
                "view": "day",
                "date": (timezone.localdate() + timedelta(days=11)).isoformat(),
            },
        )
        entry = response.context["schedules"][0].entries[0]
        self.assertTrue(entry.starts_before)
        self.assertEqual(entry.actual_start, early.planned_start)
        self.assertEqual(entry.time_label.split("–")[0], "10:00")


class MasterScreenTests(BaseTrackerTest):
    def setUp(self):
        super().setUp()
        self.service_a.base_price = Decimal("7000.00")
        self.service_a.save(update_fields=["base_price"])
        self.order = self.make_order()
        set_order_status(self.order, Order.Status.BOOKED, actor=self.owner)
        self.order.refresh_from_db()

    def test_master_screen_requires_login(self):
        response = self.client.get(reverse("tracker:master_screen"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_master_sees_work_actions_without_prices(self):
        self.client.force_login(self.master)
        response = self.client.get(reverse("tracker:master_screen"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Экран мастера")
        self.assertContains(response, self.client_obj.name)
        self.assertContains(response, "А123ВС77")
        self.assertContains(response, "Автомобиль принят")
        self.assertNotContains(response, "7 000")
        self.assertNotContains(response, "Предварительная сумма")

    def test_owner_sees_the_same_production_actions(self):
        # Экран мастера — производственный: цены не показываются ни одной роли.
        self.client.force_login(self.owner)
        response = self.client.get(reverse("tracker:master_screen"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Автомобиль принят")
        self.assertNotContains(response, "7 000")

    def test_leads_and_issued_orders_are_not_shown(self):
        lead = create_order(
            client=self.client_obj,
            vehicle=self.vehicle,
            services=[self.service_a],
            comment="Только заявка",
            actor=self.owner,
        )
        issued = create_order(
            client=self.client_obj,
            vehicle=self.vehicle,
            services=[self.service_a],
            planned_start=date_at(12, 10),
            planned_end=date_at(12, 12),
            actor=self.owner,
        )
        set_order_status(issued, Order.Status.BOOKED, actor=self.owner)
        set_order_status(issued, Order.Status.ACCEPTED, actor=self.owner)
        set_order_status(issued, Order.Status.IN_PROGRESS, actor=self.owner)
        toggle_service_done(issued.services.first(), done=True, actor=self.owner)
        issued.refresh_from_db()
        set_order_status(issued, Order.Status.READY, actor=self.owner)
        set_order_status(issued, Order.Status.ISSUED, actor=self.owner)

        self.client.force_login(self.owner)
        response = self.client.get(reverse("tracker:master_screen"))
        self.assertNotContains(response, lead.display_number)
        self.assertNotContains(response, issued.display_number)
        self.assertContains(response, self.order.display_number)

    def test_master_status_action_returns_to_master_screen(self):
        self.client.force_login(self.master)
        response = self.client.post(
            reverse("tracker:order_status", args=[self.order.pk]),
            {
                "to": Order.Status.ACCEPTED,
                "version": self.order.version,
                "next": reverse("tracker:master_screen"),
            },
        )
        self.assertRedirects(response, reverse("tracker:master_screen"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.ACCEPTED)

    def test_service_toggle_returns_to_master_screen(self):
        set_order_status(self.order, Order.Status.ACCEPTED, actor=self.owner)
        self.order.refresh_from_db()
        row = self.order.services.first()
        self.client.force_login(self.master)
        response = self.client.post(
            reverse("tracker:order_toggle_service", args=[self.order.pk, row.pk]),
            {
                "done": "1",
                "version": self.order.version,
                "next": reverse("tracker:master_screen"),
            },
        )
        self.assertRedirects(response, reverse("tracker:master_screen"))
        row.refresh_from_db()
        self.assertTrue(row.is_done)

    def test_ready_is_blocked_until_services_done(self):
        set_order_status(self.order, Order.Status.ACCEPTED, actor=self.owner)
        set_order_status(self.order, Order.Status.IN_PROGRESS, actor=self.owner)
        self.order.refresh_from_db()

        self.client.force_login(self.master)
        response = self.client.get(reverse("tracker:master_screen"))
        self.assertContains(response, "Сначала отметьте выполненные услуги")
        self.assertContains(response, "disabled")

        response = self.client.post(
            reverse("tracker:order_status", args=[self.order.pk]),
            {"to": Order.Status.READY, "version": self.order.version},
        )
        self.assertEqual(response.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.IN_PROGRESS)

    def test_master_pulse_returns_signature(self):
        self.client.force_login(self.master)
        response = self.client.get(reverse("tracker:master_pulse"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("signature", response.json())
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_master_pulse_requires_login(self):
        response = self.client.get(reverse("tracker:master_pulse"))
        self.assertEqual(response.status_code, 302)
