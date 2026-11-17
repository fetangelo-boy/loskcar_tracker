"""Загружает предварительный каталог из брифа и служебного владельца.

Цены и длительности намеренно не заполняются: в ТЗ они не подтверждены.
Повторный запуск не перезаписывает услуги, которые владелец уже отредактировал.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import User
from tracker.models import Service

SERVICES_FROM_BRIEF = [
    "Нанесение защитных составов",
    "Полировка кузова и фар",
    "Химчистка салона",
    "Ремонт царапин",
    "Антихром",
    "Полировка",
    "Ремонт кожи",
    "Полировка стекла",
    "Обслуживание дисков",
    "Химчистка потолка",
    "Шумоизоляция арок",
    "Плёнка на кузов",
    "Антигравийная плёнка",
    "Удаление вмятин без покраски",
    "Предпродажная подготовка",
    "Озонирование",
]


class Command(BaseCommand):
    help = "Создаёт предварительный каталог услуг и служебного владельца (без пароля)."

    @transaction.atomic
    def handle(self, *args, **options):
        created = 0
        for name in SERVICES_FROM_BRIEF:
            _, was_created = Service.objects.get_or_create(
                name=name,
                defaults={
                    "is_confirmed": False,
                    "is_active": True,
                },
            )
            created += int(was_created)

        owner, owner_created = User.objects.get_or_create(
            username="owner",
            defaults={
                "first_name": "Владелец",
                "role": User.Role.OWNER,
                "is_staff": True,
            },
        )
        if owner_created:
            owner.set_unusable_password()
            owner.save(update_fields=["password"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Каталог: добавлено {created} услуг, всего {Service.objects.count()}. "
                f"Владелец: {'создан' if owner_created else 'уже есть'}."
            )
        )
        self.stdout.write(
            "Цены и длительности не заполнены — их подтверждает владелец в каталоге."
        )
