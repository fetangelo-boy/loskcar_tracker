from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Сотрудник студии.

    Роли заложены сразу, но ограничение доступа включается на этапе
    авторизации (см. docs/ROADMAP.md): в MVP трекер работает в одну роль.
    """

    class Role(models.TextChoices):
        OWNER = "owner", "Владелец"
        ADMIN = "admin", "Администратор"
        MASTER = "master", "Мастер"

    role = models.CharField(
        "Роль",
        max_length=20,
        choices=Role.choices,
        default=Role.MASTER,
    )

    class Meta:
        verbose_name = "Сотрудник"
        verbose_name_plural = "Сотрудники"

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        return self.get_full_name() or self.username

    @property
    def is_owner(self) -> bool:
        return self.role == self.Role.OWNER

    @property
    def is_admin_role(self) -> bool:
        return self.role == self.Role.ADMIN
