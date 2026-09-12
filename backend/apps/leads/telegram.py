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
    actions = [("Завершить заказ", "finish")] if accepted else [("Принять", "accept"), ("Отклонить", "reject")]
    return {"inline_keyboard": [[
        {"text": label, "callback_data": f"{notice.pk.hex}:{action}"} for label, action in actions
    ]]}


def notification_text(lead):
    service_name = lead.service.name if lead.service_id else "Услуга не выбрана"
    return (
        f"🔔 Новый заказ\n{service_name}\n"
        f"Клиент: {lead.client.name}\nРайон: {lead.client.district or 'Не указан'}\n"
        f"Телефон: {lead.client.phone}\nЗаявка № {lead.pk}"
    )


def target_for(notice):
    model = Order if notice.order_id else Lead
    pk = notice.order_id or notice.lead_id
    return model.objects.select_for_update().get(pk=pk)


def is_current(notice, lead):
    return (
        notice.active and lead.employee_id == notice.employee_id
        and not TelegramNotice.objects.filter(**({"order": lead} if notice.order_id else {"lead": lead}), created_at__gt=notice.created_at).exists()
    )


def deliver_pending(api):
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
            if not is_current(notice, lead) or lead.status != Lead.Status.ASSIGNED:
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
                                  text=notification_text(lead), reply_markup=keyboard(notice))
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
    if notice.order_id:
        from apps.orders.services import transition_order, record_event
        if action == "accept" and lead.status == Order.Status.ASSIGNED:
            transition_order(lead.pk, "start", actor=notice.employee)
            return "Заказ принят.", notice, keyboard(notice, accepted=True)
        if action == "finish" and lead.status == Order.Status.IN_PROGRESS:
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
        if notice and notice.order_id and callback.get("data", "").endswith(":finish"):
            result = api.call("sendMessage", chat_id=notice.chat_id,
                text=f"Заказ № {notice.order_id}. Сколько получили? Введите сумму в KZT ответом на это сообщение (например, 15000 или 15000,50). Если оплаты нет — 0. Отмена — /cancel.",
                reply_markup={"force_reply": True, "selective": True})
            TelegramNotice.objects.filter(pk=notice.pk, active=True).update(amount_prompt_id=result["message_id"])
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
    result = apply_amount_message(message)
    if result:
        logger.info("amount_reply chat=%s result=%s", chat.get("id"), "processed")
        api.call("sendMessage", chat_id=chat["id"], text=result)
        return
    if chat.get("type") == "private" and message.get("text", "").split("@")[0].split(" ")[0] in ("/start", "/id"):
        logger.info("identity_command chat=%s", chat["id"])
        api.call("sendMessage", chat_id=chat["id"],
                 text=f"Ваш Telegram ID: {chat['id']}. Передайте его администратору CRM для подключения уведомлений.")


@transaction.atomic
def apply_amount_message(message):
    from django import forms
    from django.core.exceptions import ValidationError
    from apps.orders.services import transition_order, record_event
    chat = message.get("chat", {})
    sender = message.get("from", {}).get("id")
    reply_id = message.get("reply_to_message", {}).get("message_id")
    if chat.get("type") != "private" or not sender or sender != chat.get("id") or not reply_id:
        return None
    snapshot = TelegramNotice.objects.filter(chat_id=sender, amount_prompt_id=reply_id, order__isnull=False).first()
    if snapshot is None:
        return None
    order = target_for(snapshot)
    notice = TelegramNotice.objects.select_for_update().select_related("employee").get(pk=snapshot.pk)
    if (not is_current(notice, order) or notice.state != "sent" or notice.amount_prompt_id != reply_id
        or not notice.employee.is_active or notice.employee.role != "worker"
        or notice.employee.telegram_chat_id != sender or order.status != Order.Status.IN_PROGRESS):
        return "Заказ уже изменён или завершён. Сумма не сохранена."
    raw = message.get("text", "").strip()
    if raw == "/cancel":
        notice.amount_prompt_id = None
        notice.save(update_fields=("amount_prompt_id",))
        return "Ввод отменён. Заказ остаётся в работе."
    try:
        amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0).clean(raw.replace(",", "."))
    except ValidationError:
        return "Введите число от 0 с максимум двумя знаками после запятой ответом на тот же запрос суммы."
    transition_order(order.pk, "complete", actor=notice.employee)
    if amount > 0:
        order.amount = amount
        order.received_amount = amount
        order.received_at = timezone.now()
        order.save(update_fields=("amount", "received_amount", "received_at"))
        transition_order(order.pk, "pay", actor=notice.employee)
    record_event(order, notice.employee, f"Telegram: получено {amount} KZT" + ("; способ оплаты не указан" if amount else "; без оплаты"))
    notice.active = False
    notice.save(update_fields=("active",))
    return f"Заказ № {order.pk}: " + (f"оплачен, {amount} KZT. Перенесён в историю." if amount else "работа завершена, ожидает оплаты.")


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
