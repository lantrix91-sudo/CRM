# Развёртывание CRM на Railway

Веб-интерфейс и мобильный API работают в одном web-сервисе.
Telegram запускается отдельным worker-сервисом, PostgreSQL общий.

## Сервисы

1. Создать проект Railway и добавить PostgreSQL.
2. Создать web-сервис из репозитория или загрузить корень проекта через CLI.
   Root Directory: / (не backend). Используется Dockerfile и railway.toml.
3. В web → Networking нажать Generate Domain.
4. Настроить переменные web:
   - DJANGO_SECRET_KEY: новый случайный ключ (например python -c "import secrets; print(secrets.token_urlsafe(64))").
   - DJANGO_PRODUCTION=true
   - DJANGO_ALLOWED_HOSTS: домен без https:// и без пути.
   - PGHOST=${{Postgres.PGHOST}}
   - PGPORT=${{Postgres.PGPORT}}
   - PGDATABASE=${{Postgres.PGDATABASE}}
   - PGUSER=${{Postgres.PGUSER}}
   - PGPASSWORD=${{Postgres.PGPASSWORD}}
   Если сервис БД назван иначе, заменить Postgres в ссылках.
   Не задавать POSTGRES_* из локального .env: они имеют приоритет над PG*.
5. Дождаться успешной миграции и запуска. Проверить /login/, /kanban/ и вход мобильного мастера.
6. Создать администратора в web через Railway SSH: python backend/manage.py createsuperuser.
7. Создать worker из того же исходного кода.
   Config File Path: /deploy/worker.railway.toml.
   Те же переменные БД, DJANGO_SECRET_KEY, DJANGO_ALLOWED_HOSTS, DJANGO_PRODUCTION.
   Дополнительно TELEGRAM_BOT_TOKEN. Домена worker не требует.
   Ровно 1 replica. Перед первым запуском остановить локальный Telegram-worker.
   Для обновлений worker сначала дождаться применения миграций web.
8. При использовании телефонии добавить TELEPHONY_WEBHOOK_TOKEN в web.
9. В Android указать https://<домен-web>.

Gunicorn использует выданный Railway PORT. WhiteNoise раздаёт статику.
Frontend собирается внутри Docker. collectstatic выполняется при старте web.
Миграции и createcachetable — в pre-deploy; DB cache используется для общей защиты входа.
Логи пишутся в stdout/stderr Railway, без зависимости от локального telegram.log.

## Публикация через CLI

В терминале в корне E:\CRM:

    npx @railway/cli login
    npx @railway/cli link
    npx @railway/cli up --service web

link выбирает проект и окружение. Имя web заменить на фактическое.
Авторизация интерактивная; пароли и токены не отправлять в чат.
Перед up должны быть готовы сервис, переменные и подключение БД.

Локальные секреты, mobile и backups исключены из CLI-загрузки через .railwayignore.
Docker-контекст ограничен исходниками сервера, frontend и deploy.
Android APK не публикуется на Railway: Railway размещает его сервер.

## Данные

Новая PostgreSQL Railway изначально пустая. Локальные клиенты, пользователи, заказы
и Telegram-привязки автоматически не копируются. Перенос существующей базы
выполняется отдельным согласованным шагом после резервной копии.
Не запускать одного бота одновременно локально и в Railway с разными базами.
Настроить резервное копирование PostgreSQL перед реальной эксплуатацией.

## Проверка

Локально проверены frontend build и Django production-настройки.
Docker-образ и HTTPS необходимо проверить после первого Railway deployment:
на этом компьютере Docker отсутствует.

Документация: https://docs.railway.com/guides/django
