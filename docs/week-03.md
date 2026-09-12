# Неделя 3 — Django

## Project и app
Проект — общая конфигурация сайта: backend/config/settings.py подключает приложения и базу, urls.py задаёт адреса,
asgi.py и wsgi.py служат точками входа для серверов. manage.py запускает команды Django.
App — отдельная область приложения.

| Учебная схема | Текущий каталог | Модель |
| --- | --- | --- |
| users | backend/apps/accounts | User (сотрудник / Employee) |
| clients | backend/apps/customers | Client |
| leads | backend/apps/leads | Lead |
| orders | backend/apps/orders | Order |
| services | backend/apps/services | Service |

accounts и customers сохраняют свои имена: на них уже ссылаются миграции.
activity и workflows пока заготовки.

В каждом приложении models.py описывает данные, admin.py — интерфейс админки,
apps.py — конфигурацию приложения, migrations/ — историю схемы.
tests.py содержит проверки (добавлены для заказов), __init__.py обозначает Python-пакет.

## Как читать модель
Класс models.Model обычно соответствует таблице, объект — строке, поле — столбцу.
Django добавляет id типа BigAutoField как primary key автоматически.
CharField — текст ограниченной длины, TextField — длинный текст.
auto_now_add=True записывает время создания через ORM, но не создаёт SQL DEFAULT для прямого INSERT.
Meta задаёт параметры модели; __str__ — подпись объекта, а не столбец.
db_index=True создаёт индекс; unique=True обеспечивает уникальность.
blank=True разрешает пустое значение при валидации, null=True разрешает NULL в базе.

## User — сотрудник
Файл: backend/apps/accounts/models.py. Таблица: accounts_user.
Наследуется от AbstractUser, поэтому уже имеет username, first_name, last_name, email, password,
is_active, is_staff, is_superuser, date_joined и другие стандартные поля.
Группы и права — встроенные many-to-many связи через промежуточные таблицы.

Сотрудник входит в систему; клиент — отдельная запись CRM.
is_staff разрешает вход в админку, но обычному сотруднику также нужны права на модели.
Создавайте сотрудников через create_user() или админку, чтобы пароль хешировался.
AUTH_USER_MODEL указывает на accounts.User. ForeignKey использует settings.AUTH_USER_MODEL,
а рабочий Python-код получает класс через get_user_model().

## Client — клиент
Файл: backend/apps/customers/models.py. Таблица: customers_client.

| Поле | Значение |
| --- | --- |
| id | Уникальный номер |
| name | Имя или название, до 200 символов |
| phone | Телефон как текст, до 32 символов, индексирован |
| created_at | Время создания |

Текст сохраняет плюс и ведущие нули. Формат номера сейчас не проверяется.
Телефон не уникален: у нескольких клиентов может быть общий номер.
client.leads.all() и client.orders.all() возвращают связанные обращения и заказы.

## Service — услуга
Файл: backend/apps/services/models.py. Таблица: services_service.
Поля: id, name (уникальное название до 200 символов), description (необязательный текст), created_at.
Пустое описание хранится как пустая строка.
Услуга — элемент каталога: одну услугу могут заказывать разные клиенты.
Цены и расчёты пока не добавлены.

## Lead — предварительное обращение
Файл: backend/apps/leads/models.py. Таблица: leads_lead.
Поля: id, title (до 200 символов), client, service, employee, created_at (с индексом).
Клиент и услуга обязательны, ответственный сотрудник — нет.
В текущей упрощённой схеме клиент и услуга уже известны при создании лида.

## Order — подтверждённая заявка
Файл: backend/apps/orders/models.py. Таблица: orders_order.
Поля: id, title, client, service, employee, created_at — тех же типов, что у Lead.
Отличается смысл: лид — предварительный интерес, заказ — подтверждённая заявка на одну услугу.
Заказ создаётся отдельно. Связь с исходным лидом и автоматическая конвертация пока не реализованы.
Несколько услуг на заказ, статусы и оплаты — следующие этапы.

## Связи
У лида/заказа один клиент, у клиента много лидов/заказов: many-to-one.
ForeignKey хранит идентификатор: order.client_id — число, order.client — объект.
Обращение к объекту может выполнять дополнительный запрос.
related_name задаёт обратную связь: client.orders.all(), service.leads.all(), employee.orders.all().

client и service используют PROTECT: нельзя удалить их через Django, пока есть связанные лиды/заказы.
employee использует SET_NULL: удаление сотрудника сохраняет запись, очищая ответственного.
Удаление заказа не удаляет клиента или услугу. Сотрудника в обычной работе можно деактивировать через is_active.
on_delete исполняет ORM Django, а не прямой SQL.
Django автоматически индексирует ForeignKey.

## Миграции и админка
Из корня проекта в PowerShell:
```powershell
.\.venv\Scripts\python.exe backend/manage.py makemigrations
.\.venv\Scripts\python.exe backend/manage.py migrate --plan
.\.venv\Scripts\python.exe backend/manage.py migrate
.\.venv\Scripts\python.exe backend/manage.py showmigrations
.\.venv\Scripts\python.exe backend/manage.py check
```
makemigrations сравнивает модели с историей миграций и создаёт файл изменений.
migrate применяет изменения к базе; showmigrations показывает применённые миграции.
Редактирование models.py само по себе не меняет таблицы. Миграции храните в Git; применённые не переписывайте.

В /admin/ зарегистрированы все пять моделей. Сначала создайте клиента и услугу, затем лид или заказ.
В заказах есть поиск и автодополнение связей. Дата создания доступна только для чтения.
list_select_related загружает связанные объекты вместе с заказами, сокращая число запросов.

## Практика ORM
Запустите shell:
```powershell
.\.venv\Scripts\python.exe backend/manage.py shell
```
Вставьте блок целиком. Все учебные записи откатятся; номера id могут иметь пропуски:
```python
exec("""
from uuid import uuid4
from django.db import transaction
from apps.customers.models import Client
from apps.services.models import Service
from apps.leads.models import Lead
from apps.orders.models import Order

with transaction.atomic():
    client = Client.objects.create(name="Учебный клиент", phone="+77000000000")
    service = Service.objects.create(name="Учебная услуга " + uuid4().hex)
    lead = Lead.objects.create(title="Обращение", client=client, service=service)
    order = Order.objects.create(title="Заказ", client=client, service=service)

    found = Client.objects.get(pk=client.pk)
    print(list(Client.objects.filter(pk=client.pk).values("id", "name")))
    found.name = "Новое имя"
    found.save(update_fields=["name"])

    loaded = Order.objects.select_related("client", "service", "employee").get(pk=order.pk)
    print(loaded.client.name, loaded.service.name, loaded.employee)
    print(client.orders.count())
    print(list(Order.objects.filter(client__id=client.pk).values_list("title", flat=True)))

    order.delete()
    lead.delete()
    client.delete()
    service.delete()
    transaction.set_rollback(True)
""")
```
create выполняет INSERT, get/filter — чтение, save существующего объекта — UPDATE, delete — DELETE.
QuerySet обычно ленивый: filter строит запрос, list и перебор выполняют его.
get сразу запрашивает одну строку и может выбросить DoesNotExist или MultipleObjectsReturned.
save не вызывает full_clean автоматически; формы админки выполняют валидацию.
select_related подходит для ForeignKey и OneToOne; для обратных коллекций обычно нужен prefetch_related.

## Проверки и вопросы
Команда тестов:
```powershell
.\.venv\Scripts\python.exe backend/manage.py test apps.orders --noinput
```
Для стандартного запуска роль PostgreSQL должна иметь право создавать отдельную тестовую базу.
Тесты проверяют загрузку связей одним запросом, защиту клиента/услуги,
сохранение заказа после удаления сотрудника и создание без ответственного.

Проверь понимание:
1. Почему сотрудник и клиент — разные модели?
2. Что хранится в order.client_id?
3. Чем blank отличается от null?
4. Почему нельзя удалить услугу с заказом?
5. Что будет с заказом при удалении сотрудника?
6. Чем makemigrations отличается от migrate?
7. Как select_related сокращает количество запросов?
8. Чем лид отличается от заказа?
