"""Синтетические демо-данные для показа MVP. Не использовать на проде."""

import os
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from tracker.models import Client, Comment, Order, Service
from tracker.services import (
    add_comment,
    add_vehicle,
    create_client,
    create_order,
    set_order_status,
    toggle_service_done,
)
from tracker.workflow import Status

DEMO_PREFIX = "+7999000"
DEMO_TAG = "[ДЕМО]"


class Command(BaseCommand):
    help = "Создаёт синтетические клиентов, автомобили и заказы для демонстрации."

    def add_arguments(self, parser):
        parser.add_argument(
            "--passwords",
            action="store_true",
            help=(
                "Задать owner/admin/master демонстрационный пароль для локального "
                "входа. Пароль берётся из DEMO_PASSWORD или 'loskcar-demo'."
            ),
        )

    def _service(self, name: str) -> Service:
        service = Service.objects.filter(name=name).first()
        if service is None:
            raise RuntimeError(f"В каталоге нет услуги «{name}». Запустите seed_reference.")
        return service

    @transaction.atomic
    def handle(self, *args, **options):
        call_command("seed_reference")

        owner = User.objects.filter(role=User.Role.OWNER).order_by("pk").first()
        if owner is None:
            raise RuntimeError("Нужен служебный владелец. Запустите seed_reference.")

        admin, _ = User.objects.get_or_create(
            username="admin",
            defaults={
                "first_name": "Администратор",
                "role": User.Role.ADMIN,
                "is_staff": True,
            },
        )
        if not admin.password or not admin.has_usable_password():
            admin.set_unusable_password()
            admin.save(update_fields=["password"])

        master, _ = User.objects.get_or_create(
            username="master",
            defaults={
                "first_name": "Мастер",
                "role": User.Role.MASTER,
            },
        )
        if not master.password or not master.has_usable_password():
            master.set_unusable_password()
            master.save(update_fields=["password"])

        if options.get("passwords"):
            password = os.environ.get("DEMO_PASSWORD", "loskcar-demo")
            for user in (owner, admin, master):
                user.set_password(password)
                user.is_active = True
                user.save(update_fields=["password", "is_active"])
            self.stdout.write(
                self.style.WARNING(
                    "Демонстрационные пароли включены для owner, admin и master. "
                    "Логин — имя пользователя, пароль — "
                    f"«{password}». Не использовать на production."
                )
            )

        if Client.objects.filter(phone__startswith=DEMO_PREFIX).exists():
            self.stdout.write(
                self.style.WARNING(
                    "Демо-клиенты уже есть. Повторно не создаю, чтобы не плодить дубли."
                )
            )
            return

        now = timezone.localtime()

        def at(days: int, hour: int, minute: int = 0):
            return (now + timedelta(days=days)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )

        # --- Клиенты ---
        andrey = create_client(
            name="Андрей Петров",
            phone="+7 999 000-00-01",
            telegram="@demo_andrey",
            actor=owner,
        )
        camry, _ = add_vehicle(
            andrey, make="Toyota", model="Camry", plate="А123ВС77", actor=owner
        )

        maria = create_client(
            name="Мария Смирнова",
            phone="+7 999 000-00-02",
            actor=owner,
        )
        rio, _ = add_vehicle(
            maria, make="Kia", model="Rio", plate="В456ЕК77", actor=owner
        )

        alexey = create_client(
            name="Алексей Кузнецов",
            phone="+7 999 000-00-03",
            telegram="@demo_alexey",
            is_regular=True,
            actor=owner,
        )
        x5, _ = add_vehicle(
            alexey, make="BMW", model="X5", plate="С789МН77", actor=owner
        )

        olga = create_client(
            name="Ольга Лебедева",
            phone="+7 999 000-00-04",
            actor=owner,
        )
        mercedes, _ = add_vehicle(
            olga, make="Mercedes-Benz", model="C 200", plate="Е321ОР77", actor=owner
        )

        dmitry = create_client(
            name="Дмитрий Орлов",
            phone="+7 999 000-00-05",
            actor=owner,
        )
        audi, _ = add_vehicle(
            dmitry, make="Audi", model="A6", plate="К654ТУ77", actor=owner
        )

        sergey = create_client(
            name="Сергей Волков",
            phone="+7 999 000-00-06",
            actor=owner,
        )
        skoda, _ = add_vehicle(
            sergey, make="Skoda", model="Octavia", plate="М987ХА77", actor=owner
        )

        anna = create_client(
            name="Анна Морозова",
            phone="+7 999 000-00-07",
            actor=owner,
        )
        polo, _ = add_vehicle(
            anna, make="Volkswagen", model="Polo", plate="Н147РС77", actor=owner
        )

        # --- 1. Новая заявка без даты ---
        create_order(
            client=andrey,
            vehicle=camry,
            services=[self._service("Химчистка салона")],
            source=Order.Source.CALL,
            comment=f"{DEMO_TAG} Звонок: интересуется химчисткой салона, время не подтверждено.",
            administrator=admin,
            actor=owner,
        )

        # --- 2. В работе администратора ---
        order_admin = create_order(
            client=maria,
            vehicle=rio,
            services=[self._service("Полировка кузова и фар")],
            source=Order.Source.SITE,
            comment=f"{DEMO_TAG} Заявка с сайта, нужно уточнить дату.",
            desired_at=at(3, 12),
            administrator=admin,
            actor=owner,
        )
        set_order_status(order_admin, Status.ADMIN_WORK, actor=owner)

        # --- 3. Записан на завтра ---
        order_booked = create_order(
            client=alexey,
            vehicle=x5,
            services=[
                self._service("Полировка кузова и фар"),
                self._service("Нанесение защитных составов"),
            ],
            source=Order.Source.CALL,
            comment=f"{DEMO_TAG} Подтверждена запись после осмотра.",
            planned_start=at(1, 10),
            planned_end=at(1, 15),
            master=master,
            administrator=admin,
            actor=owner,
        )
        set_order_status(order_booked, Status.BOOKED, actor=owner)

        # --- 4. В работе у мастера, одна услуга выполнена ---
        order_progress = create_order(
            client=olga,
            vehicle=mercedes,
            services=[
                self._service("Химчистка салона"),
                self._service("Полировка стекла"),
            ],
            source=Order.Source.CALL,
            comment=f"{DEMO_TAG} Автомобиль в работе.",
            planned_start=at(0, 10),
            planned_end=at(0, 17),
            master=master,
            administrator=admin,
            actor=owner,
        )
        set_order_status(order_progress, Status.BOOKED, actor=owner)
        set_order_status(order_progress, Status.ACCEPTED, actor=owner)
        set_order_status(order_progress, Status.IN_PROGRESS, actor=owner)
        first_row = order_progress.services.order_by("position", "id").first()
        if first_row:
            toggle_service_done(first_row, done=True, actor=owner)
        add_comment(
            order_progress,
            text=f"{DEMO_TAG} Клиент просит проверить небольшой скол на двери.",
            kind=Comment.Kind.PROBLEM,
            actor=owner,
        )

        # --- 5. Готово, но ещё не выдано ---
        order_ready = create_order(
            client=dmitry,
            vehicle=audi,
            services=[self._service("Ремонт царапин")],
            source=Order.Source.MESSENGER,
            comment=f"{DEMO_TAG} Работы завершены, автомобиль ожидает выдачи.",
            planned_start=at(-1, 10),
            planned_end=at(-1, 13),
            master=master,
            administrator=admin,
            actor=owner,
        )
        set_order_status(order_ready, Status.BOOKED, actor=owner)
        set_order_status(order_ready, Status.ACCEPTED, actor=owner)
        set_order_status(order_ready, Status.IN_PROGRESS, actor=owner)
        for row in order_ready.services.all():
            toggle_service_done(row, done=True, actor=owner)
        set_order_status(order_ready, Status.READY, actor=owner)

        # --- 6. Выдан ---
        order_issued = create_order(
            client=sergey,
            vehicle=skoda,
            services=[self._service("Озонирование")],
            source=Order.Source.CALL,
            comment=f"{DEMO_TAG} Выдан клиенту.",
            planned_start=at(-5, 12),
            planned_end=at(-5, 14),
            master=master,
            administrator=admin,
            actor=owner,
        )
        set_order_status(order_issued, Status.BOOKED, actor=owner)
        set_order_status(order_issued, Status.ACCEPTED, actor=owner)
        set_order_status(order_issued, Status.IN_PROGRESS, actor=owner)
        for row in order_issued.services.all():
            toggle_service_done(row, done=True, actor=owner)
        set_order_status(order_issued, Status.READY, actor=owner)
        set_order_status(order_issued, Status.ISSUED, actor=owner)

        # --- 7. Отказ ---
        order_declined = create_order(
            client=anna,
            vehicle=polo,
            services=[self._service("Ремонт кожи")],
            source=Order.Source.CALL,
            comment=f"{DEMO_TAG} Клиент выбрал другую студию.",
            administrator=admin,
            actor=owner,
        )
        set_order_status(
            order_declined,
            Status.DECLINED,
            actor=owner,
            reason="Демо: клиент отказался.",
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Демо-данные созданы: 7 клиентов, 7 заказов в разных статусах. "
                "Телефоны начинаются на +7 999 000-00-XX."
            )
        )
        self.stdout.write(
            "Финансовые суммы отсутствуют намеренно: цены в каталоге не подтверждены."
        )
