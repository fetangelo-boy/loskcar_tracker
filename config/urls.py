from django.contrib import admin
from django.urls import include, path

from tracker import api

urlpatterns = [
    path("admin/", admin.site.urls),
    # Вход, выход и смена пароля. Шаблоны лежат в templates/registration/.
    path("accounts/", include("django.contrib.auth.urls")),
    # /health — короткий alias для внешних проверок доступности.
    path("health", api.healthz, name="health"),
    path("healthz", api.healthz, name="healthz"),
    path("", include("tracker.urls")),
]
