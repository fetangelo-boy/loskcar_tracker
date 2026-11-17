# PaaS (Render, Railway): запуск процесса и миграции перед релизом.
# На VPS без release-фазы строки release можно удалить — миграции запускаются вручную.
release: python manage.py migrate --noinput
web: gunicorn config.wsgi:application --workers 2 --bind 0.0.0.0:$PORT --access-logfile - --error-logfile -
