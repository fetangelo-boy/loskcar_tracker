# LoskCar Tracker

Демонстрационная версия трекера заказов для детейлинг-студии на Django.

Возможности: канбан заказов, календарь загрузки, карточки клиентов и заказов,
каталог услуг, экран мастера, роли сотрудников, журнал действий.

Стек: Python 3.10+, Django 5.2, SQLite, HTML/CSS/JavaScript без фреймворков.
Данные в демонстрации тестовые.

## Запуск локально

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Приложение откроется на `http://127.0.0.1:8000/`.
