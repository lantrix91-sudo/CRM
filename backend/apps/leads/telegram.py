"""Telegram transport and assignment actions; credentials never appear in errors."""
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import Lead, TelegramNotice
from apps.orders.models import Order


logger = logging.getLogger("crm.telegram")


class TelegramError(Exception):
    pass


class TelegramAPI:
    def __init__(self, token):
        self.token = token

    def call(self, method, **data):
        request = Request(
            f"https://api.telegram.org/bot{self.token}/{method}",
            data=json.dumps(data).encode(), headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=25) as response:
                body = json.load(response)
        except HTTPError as error:
            try:
                payload = json.loads(error.read(8192))
            except (ValueError, OSError):
                payload = {}
            log_api_error(method, error.code, payload)
            raise TelegramError("Telegram отклонил запрос. Причина записана в telegram.log.") from None
        except (URLError, TimeoutError, ValueError, OSError):
            logger.warning("api_failed method=%s reason=connection_or_invalid_response", method)
            raise TelegramError("Telegram недоступен или отклонил запрос; проверьте настройки бота.") from None
        if not body.get("ok"):
            log_api_error(method, body.get("error_code"), body)
            raise TelegramError("Telegram отклонил запрос.") from None
        return body["result"]


def keyboard(notice, accepted=False):
    if accepted and notice.order_id:
        markup = {"inline_keyboard": [
            [{"text": "Мастер отказался", "callback_data": f"{notice.pk.hex}:worker_reject"},
             {"text": "Клиент отказался", "callback_data": f"{notice.pk.hex}:client_reject"}],
            [{"text": "Отложить на 1 день", "callback_data": f"{notice.pk.hex}:defer_1"},
             {"text": "Отложить на 2 дня", "callback_data": f"{notice.pk.hex}:defer_2"}],
            [{"text": "Завершить заказ", "callback_data": f"{notice.pk.hex}:finish"}],
        ]}
        if notice.order.repeat_of_id:
            markup["inline_keyboard"] = markup["inline_keyboard"][1:]
        return markup
    actions = [("Завершить заказ", "finish")] if accepted else [("Принять", "accept"), ("Отклонить", "reject")]
    return {"inline_keyboard": [[
        {"text": label, "callback_data": f"{notice.pk.hex}:{action}"} for label, action in actions
    ]]}


def notification_text(lead, accepted=False, completed=False):
    service_name = lead.service.name if lead.service_id else "Услуга не выбрана"
    lines = ["✅ Выполнено" if completed else "Заказ принят" if accepted else "🔔 Новый заказ", service_name]
    if getattr(lead, "repeat_of_id", None):
        lines.append(f"❤️ Повторка · бесплатно · исходный заказ № {lead.repeat_of_id}")
    if lead.client.name:
        lines.append(f"Имя: {lead.client.name}")
    if lead.client.address:
        lines.append(f"Адрес: {lead.client.address}")
    if accepted:
        lines.append(f"WhatsApp: {lead.client.phone}")
        if lead.client.normalized_phone:
            lines.append(f"https://wa.me/{lead.client.normalized_phone.lstrip('+')}")
    else:
        lines.append("WhatsApp будет доступен после принятия заказа.")
    lines.append(f"Заказ № {lead.pk}")
    return "\n".join(lines)


def target_for(notice):
    model = Order if notice.order_id else Lead
    pk = notice.order_id or notice.lead_id
    return model.objects.select_for_update().get(pk=pk)


def is_current(notice, lead):
    return (
        notice.active and lead.employee_id == notice.employee_id
        and not TelegramNotice.objects.filter(**({"order": lead} if notice.order_id else {"lead": lead}), created_at__gt=notice.created_at).exists()
    )


def deliver_reminders(api):
    ids = list(TelegramNotice.objects.filter(active=True, reminder_at__lte=timezone.now()).values_list("pk", flat=True)[:20])
    for pk in ids:
        snapshot = TelegramNotice.objects.get(pk=pk)
        with transaction.atomic():
            order = target_for(snapshot)
            notice = TelegramNotice.objects.select_for_update().select_related("employee").get(pk=pk)
            if not notice.reminder_at or notice.reminder_at > timezone.now():
                continue
            if not is_current(notice, order) or order.status != "in_progress":
                notice.reminder_at = None
                notice.save(update_fields=("reminder_at",))
                continue
            if not notice.employee.is_active or notice.employee.telegram_chat_id != notice.chat_id:
                continue
            try:
                api.call("sendMessage", chat_id=notice.chat_id,
                         text="⏰ Напоминание: срок переноса истёк.\n" + notification_text(order, accepted=True))
            except TelegramError:
                notice.reminder_at = timezone.now() + timedelta(minutes=1)
                notice.save(update_fields=("reminder_at",))
                continue
            notice.reminder_at = None
            notice.save(update_fields=("reminder_at",))


def deliver_pending(api):
    deliver_reminders(api)
    ids = list(TelegramNotice.objects.filter(
        state="pending", next_attempt_at__lte=timezone.now(),
    ).order_by("created_at").values_list("pk", flat=True)[:20])
    for pk in ids:
        snapshot = TelegramNotice.objects.filter(pk=pk).first()
        if snapshot is None:
            continue
        with transaction.atomic():
            lead = target_for(snapshot)
            notice = TelegramNotice.objects.select_for_update().get(pk=pk)
            if notice.state != "pending":
                continue
            accepted_repeat = bool(notice.order_id and lead.repeat_of_id and lead.status == Order.Status.IN_PROGRESS)
            if not is_current(notice, lead) or (lead.status != Lead.Status.ASSIGNED and not accepted_repeat):
                logger.info("delivery_cancelled notice=%s order=%s worker=%s", notice.pk, notice.order_id, notice.employee_id)
                notice.state = "cancelled"
                notice.active = False
                notice.save(update_fields=("state", "active"))
                continue
            employee = notice.employee
            if not employee.is_active or employee.role != "worker" or not employee.telegram_chat_id:
                logger.info("delivery_waiting notice=%s worker=%s active=%s role=%s chat_linked=%s", notice.pk, employee.pk, employee.is_active, employee.role, bool(employee.telegram_chat_id))
                notice.next_attempt_at = timezone.now() + timedelta(seconds=60)
                notice.save(update_fields=("next_attempt_at",))
                continue
            logger.info("delivery_attempt notice=%s order=%s worker=%s chat=%s attempt=%s", notice.pk, notice.order_id, employee.pk, employee.telegram_chat_id, notice.attempts + 1)
            try:
                result = api.call("sendMessage", chat_id=employee.telegram_chat_id,
                                  text=notification_text(lead, accepted=accepted_repeat), reply_markup=keyboard(notice, accepted=accepted_repeat))
            except TelegramError:
                logger.warning("delivery_failed notice=%s worker=%s", notice.pk, employee.pk)
                notice.attempts += 1
                notice.next_attempt_at = timezone.now() + timedelta(seconds=min(3600, 30 * 2 ** min(notice.attempts, 7)))
                notice.save(update_fields=("attempts", "next_attempt_at"))
                continue
            logger.info("delivery_sent notice=%s worker=%s message=%s", notice.pk, employee.pk, result["message_id"])
            notice.chat_id = employee.telegram_chat_id
            notice.message_id = result["message_id"]
            notice.state = "sent"
            notice.save(update_fields=("chat_id", "message_id", "state"))


@transaction.atomic
def apply_callback(callback):
    import uuid

    try:
        raw_id, action = callback["data"].split(":")
        pk = uuid.UUID(hex=raw_id)
    except (KeyError, ValueError, TypeError, AttributeError):
        return "Неизвестная кнопка.", None, None
    snapshot = TelegramNotice.objects.filter(pk=pk).first()
    if snapshot is None:
        return "Уведомление устарело.", None, None
    lead = target_for(snapshot)
    notice = TelegramNotice.objects.select_for_update().select_related("employee").get(pk=pk)
    message = callback.get("message", {})
    chat = message.get("chat", {})
    sender = callback.get("from", {}).get("id")
    if (
        chat.get("type") != "private" or sender != chat.get("id")
        or sender != notice.chat_id or sender != notice.employee.telegram_chat_id
        or message.get("message_id") != notice.message_id
        or not notice.employee.is_active or notice.employee.role != "worker"
        or notice.state != "sent" or not is_current(notice, lead)
    ):
        return "Нет доступа или назначение уже изменилось.", None, None
    if notice.order_id and action in ("confirm_payment", "edit_payment"):
        if lead.status != Order.Status.IN_PROGRESS or notice.payment_step != "confirm":
            return "Расчёт уже изменён. Продолжите текущий ввод.", None, None
        if action == "edit_payment":
            notice.payment_step = ""
            notice.amount_prompt_id = None
            notice.save(update_fields=("payment_step", "amount_prompt_id"))
            return "Введите данные заново.", notice, keyboard(notice, accepted=True)
        text = apply_amount_message({"chat": {"id": notice.chat_id, "type": "private"},
            "from": {"id": notice.chat_id}, "reply_to_message": {"message_id": notice.amount_prompt_id},
            "text": notice.draft_comment}, confirmed=True)
        notice.refresh_from_db()
        return text, notice, {"inline_keyboard": []}
    if notice.order_id:
        from apps.orders.services import transition_order, record_event
        if lead.repeat_of_id and action in ("reject", "worker_reject", "client_reject"):
            return "Для повторки доступны перенос и завершение.", None, None
        if lead.status == Order.Status.IN_PROGRESS:
            if action in ("defer_1", "defer_2"):
                days = int(action[-1])
                notice.reminder_at = timezone.now() + timedelta(days=days)
                notice.amount_prompt_id = None
                notice.save(update_fields=("reminder_at", "amount_prompt_id"))
                text = f"⏳ Отложено до {timezone.localtime(notice.reminder_at):%d.%m.%Y %H:%M}"
                record_event(lead, notice.employee, text)
                return text, notice, keyboard(notice, accepted=True)
            if action in ("worker_reject", "client_reject"):
                if action == "worker_reject":
                    lead.employee = None
                    lead.status = Order.Status.NEW
                    lead.save(update_fields=("employee", "status"))
                    text = "Мастер отказался. Заказ возвращён оператору."
                else:
                    lead.status = Order.Status.CANCELLED
                    lead.cancellation_reason = "Клиент отказался (со слов мастера)"
                    lead.cancelled_at = timezone.now()
                    lead.save(update_fields=("status", "cancellation_reason", "cancelled_at"))
                    text = "Клиент отказался. Заказ отменён."
                notice.active = False
                notice.reminder_at = None
                notice.amount_prompt_id = None
                notice.save(update_fields=("active", "reminder_at", "amount_prompt_id"))
                record_event(lead, notice.employee, text)
                return text, notice, {"inline_keyboard": []}
        if action == "accept" and lead.status == Order.Status.ASSIGNED:
            transition_order(lead.pk, "start", actor=notice.employee)
            return "Заказ принят.", notice, keyboard(notice, accepted=True)
        if action == "finish" and lead.status == Order.Status.IN_PROGRESS:
            if lead.repeat_of_id:
                from apps.orders.services import complete_order_with_payment
                complete_order_with_payment(
                    lead.pk, 0, 0, "Повторка выполнена бесплатно",
                    actor=notice.employee,
                    completion_event="❤️ Повторка выполнена бесплатно",
                )
                notice.active = False
                notice.reminder_at = None
                notice.amount_prompt_id = None
                notice.save(update_fields=("active", "reminder_at", "amount_prompt_id"))
                return "✅ Повторка выполнена бесплатно.", notice, {"inline_keyboard": []}
            return "Введите полученную сумму в ответ на сообщение бота.", notice, keyboard(notice, accepted=True)
        if action == "reject" and lead.status == Order.Status.ASSIGNED:
            record_event(lead, notice.employee, "Мастер отклонил заказ через Telegram")
    if action == "accept" and lead.status in (Lead.Status.ASSIGNED, Lead.Status.IN_PROGRESS):
        lead.status = Lead.Status.IN_PROGRESS
        lead.save(update_fields=("status",))
        return "Заказ принят.", notice, keyboard(notice, accepted=True)
    if action == "reject" and lead.status == Lead.Status.ASSIGNED:
        lead.status = Lead.Status.NEW
        lead.employee = None
        lead.save(update_fields=("status", "employee"))
        notice.active = False
        notice.save(update_fields=("active",))
        return "Заказ отклонён. Диспетчер сможет назначить другого сотрудника.", notice, {"inline_keyboard": []}
    if action == "finish" and lead.status == Lead.Status.IN_PROGRESS:
        if notice.order_id:
            lead.status = Order.Status.COMPLETED
            lead.completed_at = timezone.now()
            lead.save(update_fields=("status", "completed_at"))
        else:
            lead.status = Lead.Status.WON
            lead.save(update_fields=("status",))
        notice.active = False
        notice.save(update_fields=("active",))
        return "Заказ выполнен.", notice, {"inline_keyboard": []}
    return "Действие недоступно для текущего статуса.", None, None


def process_update(api, update):
    callback = update.get("callback_query")
    if callback:
        text, notice, markup = apply_callback(callback)
        action = callback.get("data", "").rsplit(":", 1)[-1]
        logger.info("callback action=%s accepted=%s", action if action in ("accept", "reject", "finish") else "unknown", notice is not None)
        api.call("answerCallbackQuery", callback_query_id=callback["id"], text=text)
        if notice and action in ("worker_reject", "client_reject", "defer_1", "defer_2"):
            target = Order.objects.get(pk=notice.order_id)
            details = notification_text(target, accepted=True).split("\n", 1)[1]
            api.call("editMessageText", chat_id=notice.chat_id, message_id=notice.message_id,
                     text=text + "\n" + details, reply_markup=markup)
            return
        if notice and action == "confirm_payment":
            api.call("editMessageText", chat_id=notice.chat_id, message_id=notice.message_id,
                     text=notification_text(notice.order, accepted=True, completed=True) + "\n" + text,
                     reply_markup=markup)
            return
        if notice and action == "edit_payment":
            result = api.call("sendMessage", chat_id=notice.chat_id,
                text=f"Заказ № {notice.order_id}. 1/3 · Сколько получил? /cancel — отмена.",
                reply_markup={"force_reply": True})
            TelegramNotice.objects.filter(pk=notice.pk).update(payment_step="amount", amount_prompt_id=result["message_id"], draft_amount=None, draft_expenses=None, draft_comment="")
            api.call("editMessageReplyMarkup", chat_id=notice.chat_id, message_id=notice.message_id, reply_markup=markup)
            return
        if notice and action == "accept":
            with transaction.atomic():
                target = target_for(notice)
                notice.refresh_from_db()
                if is_current(notice, target) and target.status == "in_progress":
                    api.call("editMessageText", chat_id=notice.chat_id, message_id=notice.message_id,
                        text=notification_text(target, accepted=True), reply_markup=markup)
            return
        if notice and notice.order_id and callback.get("data", "").endswith(":finish"):
            if notice.order.repeat_of_id:
                api.call("editMessageText", chat_id=notice.chat_id, message_id=notice.message_id,
                         text=notification_text(notice.order, accepted=True, completed=True), reply_markup=markup)
                return
            if notice.amount_prompt_id and notice.payment_step:
                return
            result = api.call("sendMessage", chat_id=notice.chat_id,
                text=f"Заказ № {notice.order_id}. 1/3 · Сколько получил? Сумма в KZT. /cancel — отмена.",
                reply_markup={"force_reply": True, "selective": True})
            TelegramNotice.objects.filter(pk=notice.pk, active=True).update(amount_prompt_id=result["message_id"], payment_step="amount", draft_amount=None, draft_expenses=None)
            return
        if notice:
            api.call("editMessageReplyMarkup", chat_id=notice.chat_id, message_id=notice.message_id, reply_markup=markup)
        return
    message = update.get("message", {})
    chat = message.get("chat", {})
    text_message = message.get("text", "")
    parts = text_message.split()
    if parts and parts[0].split("@")[0] == "/start" and len(parts) == 2 and parts[1].startswith("link_"):
        result = link_telegram_account(message, parts[1][5:])
        if result:
            api.call("sendMessage", chat_id=chat["id"], text=result)
        return
    result = apply_amount_message(message, api=api)
    if result:
        logger.info("amount_reply chat=%s result=%s", chat.get("id"), "processed")
        notice = TelegramNotice.objects.filter(
            chat_id=chat["id"],
            amount_prompt_id=message.get("reply_to_message", {}).get("message_id"),
            active=False, state="sent", order__status__in=("completed", "paid"),
            employee__telegram_chat_id=chat["id"],
        ).first()
        if notice and notice.message_id:
            try:
                api.call("editMessageText", chat_id=notice.chat_id,
                         message_id=notice.message_id,
                         text=notification_text(notice.order, accepted=True, completed=True),
                         reply_markup={"inline_keyboard": []})
            except TelegramError:
                logger.warning("completion_message_update_failed notice=%s", notice.pk)
        api.call("sendMessage", chat_id=chat["id"], text=result)
        return
    if chat.get("type") == "private" and message.get("text", "").split("@")[0].split(" ")[0] in ("/start", "/id"):
        logger.info("identity_command chat=%s", chat["id"])
        api.call("sendMessage", chat_id=chat["id"],
                 text=f"Ваш Telegram ID: {chat['id']}. Передайте его администратору CRM для подключения уведомлений.")


def clear_payment_messages(api, message):
    if api is None:
        return
    chat_id = message.get("chat", {}).get("id")
    for message_id in (message.get("reply_to_message", {}).get("message_id"), message.get("message_id")):
        if not message_id:
            continue
        try:
            api.call("deleteMessage", chat_id=chat_id, message_id=message_id)
        except TelegramError:
            logger.warning("payment_message_cleanup_failed chat=%s message=%s", chat_id, message_id)


@transaction.atomic
def apply_amount_message(message, api=None, confirmed=False):
    from django import forms
    from django.core.exceptions import ValidationError
    from apps.orders.services import complete_order_with_payment
    chat = message.get("chat", {})
    sender = message.get("from", {}).get("id")
    reply_id = message.get("reply_to_message", {}).get("message_id")
    if chat.get("type") != "private" or not sender or sender != chat.get("id"):
        return None
    if reply_id:
        snapshot = TelegramNotice.objects.filter(chat_id=sender, amount_prompt_id=reply_id, order__isnull=False).first()
    else:
        pending = list(TelegramNotice.objects.filter(chat_id=sender, active=True, state="sent",
            amount_prompt_id__isnull=False, order__status="in_progress").exclude(payment_step="")[:2])
        if len(pending) != 1:
            return "Ответьте на вопрос нужного заказа через «Ответить»." if pending else None
        snapshot = pending[0]
        reply_id = snapshot.amount_prompt_id
        message["reply_to_message"] = {"message_id": reply_id}
    if snapshot is None:
        return None
    order = target_for(snapshot)
    notice = TelegramNotice.objects.select_for_update().select_related("employee").get(pk=snapshot.pk)
    if (not is_current(notice, order) or notice.state != "sent" or notice.amount_prompt_id != reply_id
        or not notice.employee.is_active or notice.employee.role != "worker"
        or notice.employee.telegram_chat_id != sender or order.status != Order.Status.IN_PROGRESS):
        return "Заказ уже изменён или завершён. Сумма не сохранена."
    if order.repeat_of_id:
        return "Повторка бесплатная. Нажмите «Завершить заказ», ввод суммы не требуется."
    raw = message.get("text", "").strip()
    if raw == "/cancel":
        notice.amount_prompt_id = None
        notice.payment_step = ""
        notice.draft_amount = None
        notice.draft_expenses = None
        notice.save(update_fields=("amount_prompt_id", "payment_step", "draft_amount", "draft_expenses"))
        clear_payment_messages(api, message)
        return "Ввод отменён. Заказ остаётся в работе."
    if notice.payment_step == "confirm" and not confirmed:
        return "Проверьте расчёт в сообщении заказа и нажмите «Подтвердить» или «Исправить»."
    if notice.payment_step:
        money = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0)
        if notice.payment_step in ("amount", "expenses"):
            try:
                value = money.clean(raw.replace(",", "."))
            except ValidationError:
                return "Введите сумму от 0, не более двух знаков после запятой. Ответьте на тот же вопрос."
            if notice.payment_step == "expenses" and value > notice.draft_amount:
                return "Расходы не могут превышать полученную сумму. Ответьте на тот же вопрос."
            if api is None:
                return "Продолжите ввод через Telegram."
            next_step = "expenses" if notice.payment_step == "amount" else "comment"
            prompt = "2/3 · Расходы, KZT (если нет — 0)." if next_step == "expenses" else "3/3 · Комментарий: что поменял или сделал? Затем проверим расчёт."
            sent = api.call("sendMessage", chat_id=sender,
                            text=f"Заказ № {order.pk}. {prompt} /cancel — отмена.",
                            reply_markup={"force_reply": True, "selective": True})
            if next_step == "expenses":
                notice.draft_amount = value
            else:
                notice.draft_expenses = value
            notice.payment_step = next_step
            notice.amount_prompt_id = sent["message_id"]
            notice.save(update_fields=("draft_amount", "draft_expenses", "payment_step", "amount_prompt_id"))
            clear_payment_messages(api, message)
            return None
        if not raw or len(raw) > 2000:
            return "Напишите, что поменяли или сделали (до 2000 символов)."
        amount, expenses, comment = notice.draft_amount, notice.draft_expenses, raw
    else:
        try:
            lines = raw.split("\n", 2)
            if len(lines) != 3 or not lines[2].strip() or len(lines[2].strip()) > 2000:
                return "Ответьте тремя строками: сумма, расходы, что сделано (до 2000 символов)."
            money = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0)
            amount = money.clean(lines[0].strip().replace(",", "."))
            expenses = money.clean(lines[1].strip().replace(",", "."))
            if expenses > amount:
                return "Расходы не могут превышать полученную сумму. Проверьте ответ."
            comment = lines[2].strip()
        except ValidationError:
            return "Введите число от 0 с максимум двумя знаками после запятой ответом на тот же запрос суммы."
    if notice.payment_step == "comment" and not confirmed:
        if api is None:
            return "Подтвердите расчёт в Telegram."
        from decimal import Decimal, ROUND_HALF_UP
        net = amount - expenses
        percentage = notice.employee.percentage
        split = "Процент не установлен — доли пока не рассчитаны."
        if percentage is not None:
            worker_part = (net * percentage / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            split = f"Мастеру ({percentage}%): {worker_part} KZT\nРуководителю: {net - worker_part} KZT"
        api.call("editMessageText", chat_id=sender, message_id=notice.message_id,
            text=f"Заказ № {order.pk}. Проверьте расчёт\nПолучено: {amount} KZT\nРасходы: {expenses} KZT\nПосле расходов: {net} KZT\n{split}\nЧто сделано: {comment}",
            reply_markup={"inline_keyboard": [[
                {"text": "Подтвердить", "callback_data": f"{notice.pk.hex}:confirm_payment"},
                {"text": "Исправить", "callback_data": f"{notice.pk.hex}:edit_payment"}]]})
        notice.draft_comment = comment
        notice.payment_step = "confirm"
        notice.save(update_fields=("draft_comment", "payment_step"))
        clear_payment_messages(api, message)
        return None
    complete_order_with_payment(
        order.pk, amount, expenses, comment, actor=notice.employee,
        completion_event=f"Telegram: получено {amount} KZT" + (
            "; способ оплаты не указан" if amount else "; без оплаты"
        ),
    )
    notice.active = False
    notice.save(update_fields=("active",))
    clear_payment_messages(api, message)
    return f"Заказ № {order.pk}: " + (f"оплачен, {amount} KZT. Перенесён в историю." if amount else "завершён бесплатно. Оплата не требуется. Перенесён в историю.")


def log_api_error(method, code, payload):
    # Only known categories: never log arbitrary API text, URLs, credentials or message bodies.
    description = str(payload.get("description", "")).lower() if isinstance(payload, dict) else ""
    reason = "telegram_rejected"
    for fragment, category in (("chat not found", "chat_not_found"), ("blocked by the user", "bot_blocked"), ("user is deactivated", "user_deactivated"), ("too many requests", "rate_limited"), ("unauthorized", "invalid_token"), ("conflict", "polling_conflict")):
        if fragment in description:
            reason = category
            break
    logger.warning("api_failed method=%s code=%s reason=%s", method, code if isinstance(code, int) else "unknown", reason)


@transaction.atomic
def link_telegram_account(message, token):
    import uuid
    from django.db import IntegrityError
    from apps.accounts.models import User
    chat = message.get("chat", {})
    sender = message.get("from", {}).get("id")
    if chat.get("type") != "private" or type(sender) is not int or sender <= 0 or sender != chat.get("id"):
        return None
    try:
        token = uuid.UUID(token)
    except (ValueError, TypeError, AttributeError):
        return "Ссылка недействительна. Попросите руководителя создать новую."
    user = User.objects.select_for_update().filter(telegram_link_token=token).first()
    if not user or not user.is_active or user.role != "worker" or not user.telegram_link_expires or user.telegram_link_expires <= timezone.now():
        return "Ссылка истекла или уже использована. Попросите руководителя создать новую."
    if user.telegram_chat_id or User.objects.filter(telegram_chat_id=sender).exists():
        return "Telegram уже связан с сотрудником. Обратитесь к руководителю."
    try:
        with transaction.atomic():
            user.telegram_chat_id = sender
            user.telegram_link_token = None
            user.telegram_link_expires = None
            user.save(update_fields=("telegram_chat_id", "telegram_link_token", "telegram_link_expires"))
    except IntegrityError:
        return "Этот Telegram уже подключён. Обратитесь к руководителю."
    TelegramNotice.objects.filter(employee=user, state="pending", active=True).update(next_attempt_at=timezone.now())
    logger.info("account_linked worker=%s chat=%s", user.pk, sender)
    return "Telegram подключён к CRM. Здесь вы будете получать назначенные заказы."
