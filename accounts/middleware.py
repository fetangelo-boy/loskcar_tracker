"""Сессионные ограничения для сотрудников."""

from django.contrib.auth import logout


class EnforceActiveUserMiddleware:
    """Отключённый сотрудник теряет доступ немедленно.

    Django не всегда перепроверяет is_active для уже открытой сессии, поэтому
    middleware завершает такую сессию при следующем запросе. Дальше сработает
    login_required и пользователь увидит форму входа.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not user.is_active:
            logout(request)
        return self.get_response(request)

