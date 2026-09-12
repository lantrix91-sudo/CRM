import logging
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.leads.models import TelegramBotState
from apps.leads.telegram import TelegramAPI, TelegramError, deliver_pending, process_update


class Command(BaseCommand):
    help = "Run the single Telegram polling worker and deliver queued assignments."

    def handle(self, *args, **options):
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise CommandError("Укажите TELEGRAM_BOT_TOKEN в локальном .env.")
        if connection.vendor != "postgresql":
            raise CommandError("Telegram worker требует PostgreSQL.")
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(847208)")
            if not cursor.fetchone()[0]:
                raise CommandError("Telegram worker уже запущен.")
        api = TelegramAPI(token)
        try:
            # Do not silently remove an existing webhook.
            if api.call("getWebhookInfo").get("url"):
                raise CommandError("У бота настроен webhook. Для polling используйте отдельного бота или явно отключите webhook.")
            logging.getLogger("crm.telegram").info("worker_started")
            state, _ = TelegramBotState.objects.get_or_create(pk=1)
            self.stdout.write("Telegram worker запущен. Остановка: Ctrl+C.")
            while True:
                try:
                    deliver_pending(api)
                    updates = api.call("getUpdates", offset=state.offset, timeout=10, allowed_updates=["message", "callback_query"])
                    for update in updates:
                        try:
                            process_update(api, update)
                        except TelegramError:
                            self.stderr.write("Не удалось отправить ответ Telegram. Статус в CRM мог уже сохраниться.")
                        state.offset = update["update_id"] + 1
                        state.save(update_fields=("offset",))
                except TelegramError:
                    self.stderr.write("Telegram недоступен. Повтор через 5 секунд.")
                    time.sleep(5)
        except KeyboardInterrupt:
            self.stdout.write("Telegram worker остановлен.")
        except TelegramError as error:
            raise CommandError(str(error)) from None
        finally:
            logging.getLogger("crm.telegram").info("worker_stopped")
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(847208)")
