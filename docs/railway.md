# Развёртывание CRM на Railway через GitHub

Веб-интерфейс и мобильный API работают в одном web-сервисе.
Telegram запускается отдельным worker-сервисом. Внутри одного развёртывания
web и worker используют одну PostgreSQL.

## Второй сервер

1. Для независимого второго сервера создать отдельную PostgreSQL.
   Не копировать подключение к базе первого сервера, если данные должны быть раздельными.
2. Отправить проверенные исходники в GitHub. Перед публикацией проверить
   [результаты проверки](release-check.md), включая бэкап в старой истории.
3. Открыть web-сервис Railway с доменом `crm-production-1469.up.railway.app`,
   подключить репозиторий через Settings → Source → Connect Repo и выбрать нужную ветку.
4. Оставить Root Directory: `/`. Config File Path: `/railway.toml`.
   Сборка использует корневой `Dockerfile`.
5. В Variables задать:

| Переменная | Значение |
| --- | --- |
| DJANGO_SECRET_KEY | Новый случайный ключ для второго сервера |
| DJANGO_PRODUCTION | true |
| DJANGO_DEBUG | false |
| DJANGO_ALLOWED_HOSTS | crm-production-1469.up.railway.app |
| PGHOST | ${{Postgres.PGHOST}} |
| PGPORT | ${{Postgres.PGPORT}} |
| PGDATABASE | ${{Postgres.PGDATABASE}} |
| PGUSER | ${{Postgres.PGUSER}} |
| PGPASSWORD | ${{Postgres.PGPASSWORD}} |

Если сервис БД называется иначе, заменить `Postgres` в ссылках.
Не задавать локальные `POSTGRES_*`: они имеют приоритет над `PG*`.
Приложение использует отдельные переменные подключения; одного `DATABASE_URL` недостаточно.
`PORT` выдаёт Railway. При смене домена обновить `DJANGO_ALLOWED_HOSTS`.

Для генерации ключа локально: `python -c "import secrets; print(secrets.token_urlsafe(64))"`.
Ключ сохранить в Railway Variables.

6. Для пустой базы можно задать временный `CRM_BOOTSTRAP_PASSWORD`:
   pre-deploy создаст пользователя `admin` с ролью руководителя и правами superuser,
   только если пользователей ещё нет. После успешного создания убрать переменную.
   Альтернатива — `python backend/manage.py createsuperuser` через Railway SSH.
7. Запустить Deploy и проверить Build Logs / Deploy Logs.

## Порядок запуска

- Docker устанавливает frontend из `package-lock.json` и выполняет production-сборку.
- Pre-deploy: `sh /app/deploy/pre-deploy.sh` применяет миграции,
  создаёт таблицу общего кеша входа и, при настройке пароля, первого администратора.
- Start: `sh /app/deploy/start-web.sh` собирает статику и запускает Gunicorn
  на `0.0.0.0:$PORT`. Повторный запуск web не выполняет миграции.
- WhiteNoise обслуживает React и CSS. `collectstatic` остаётся в start,
  поскольку файлы из pre-deploy-контейнера не переносятся в рабочий контейнер.
- `/health/` возвращает 200 при доступной БД и 503 при ошибке подключения.
  Railway ждёт успешной проверки до 120 секунд перед переключением трафика.
  Только этот технический маршрут исключён из перенаправления HTTP → HTTPS.
- Файлы `*.sh` хранятся с LF через `.gitattributes`.
- Логи production пишутся в stdout/stderr.

При запуске Docker вне Railway сначала выполнить `sh /app/deploy/pre-deploy.sh`
в отдельном контейнере с теми же переменными БД.

## Telegram и телефония

Worker использует тот же репозиторий, Root Directory `/`,
Config File Path `/deploy/worker.railway.toml`.
Ему нужны те же переменные БД, `DJANGO_SECRET_KEY`, `DJANGO_PRODUCTION`,
`DJANGO_ALLOWED_HOSTS`, а также `TELEGRAM_BOT_TOKEN`.
Публичный домен worker не требуется; replica должна быть одна.

Для независимого второго сервера использовать отдельного Telegram-бота.
Один токен нельзя одновременно обслуживать двумя polling-worker.
Запускать/обновлять worker после успешных миграций web.
Для телефонии задать отдельный `TELEPHONY_WEBHOOK_TOKEN` в web.

## Проверка после деплоя

1. `https://crm-production-1469.up.railway.app/health/` возвращает `{"status":"ok"}`.
2. `/login/` открывается по HTTPS, CSS загружается.
3. Вход работает, `/kanban/` загружает React и API.
4. Создание и редактирование тестовой заявки проходит без ошибок CSRF.
5. Проверены роли пользователей и, если используются, Telegram и мобильный API.

Новая PostgreSQL изначально пустая. Локальные записи автоматически не переносятся.
Перенос данных и резервное копирование настраиваются отдельно.

## CLI как альтернатива

Из корня проекта:

```powershell
npx @railway/cli login
npx @railway/cli link
npx @railway/cli up --service web
```

Выбрать проект и окружение второго сервера. Имя `web` заменить на фактическое.
`.railwayignore` исключает локальные секреты, backups, зависимости и mobile.

Документация Railway:
[GitHub](https://docs.railway.com/services),
[pre-deploy](https://docs.railway.com/deployments/pre-deploy-command),
[healthchecks](https://docs.railway.com/deployments/healthchecks).
