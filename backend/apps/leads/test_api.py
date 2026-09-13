from django.test import TestCase
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.customers.models import Client
from apps.services.models import Service
from apps.orders.models import Order
from apps.leads.models import Lead


class WorkflowBoardTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(username="operator", role="operator")
        self.worker = User.objects.create_user(username="worker", role="worker", is_available=True)
        self.manager = User.objects.create_user(username="manager", role="manager")
        self.service = Service.objects.create(name="Repair")
        self.worker.services.add(self.service)
        self.customer = Client.objects.create(name="Client", phone="123")
        self.lead = Lead.objects.create(title="Repair", service=self.service, client=self.customer)
        self.api = APIClient(enforce_csrf_checks=True)
        self.api.force_login(self.operator)
        self.api.get("/kanban/")
        self.csrf = self.api.cookies["csrftoken"].value

    def post(self, url, data):
        return self.api.post(url, data, HTTP_X_CSRFTOKEN=self.csrf)

    def card(self):
        return self.api.get("/api/leads/board/").data["leads"][0]

    def test_full_lifecycle_and_history(self):
        self.assertEqual(self.card()["status"], "new")
        url = f"/workspace/lead/{self.lead.pk}/"
        self.assertEqual(self.post(url, {"action": "convert", "return_to": "kanban"}).url, "/kanban/")
        order = Order.objects.get(lead=self.lead)
        self.post(url, {"action": "convert"})
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(self.card()["status"], "in_progress")
        response = self.api.post(f"/api/orders/{order.pk}/assign/", {"employee_id": self.worker.pk}, format="json", HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.card()["status"], "assigned")
        order_url = f"/orders/{order.pk}/"
        self.assertEqual(self.post(order_url, {"action": "complete"}).status_code, 403)
        self.api.force_login(self.worker)
        self.assertContains(self.api.get("/my-orders/"), order_url)
        self.post(order_url, {"action": "start"})
        self.post(order_url, {"action": "complete"})
        self.api.force_login(self.operator)
        self.assertEqual(self.card()["status"], "completed")
        self.assertEqual(self.post(order_url, {"action": "pay"}).status_code, 403)
        order.refresh_from_db()
        order.amount = 100
        order.save()
        self.api.force_login(self.manager)
        self.post(order_url, {"action": "pay"})
        self.assertEqual(self.card()["status"], "paid")
        self.assertEqual(list(order.events.values_list("actor_id", flat=True)), [self.operator.pk, self.operator.pk, self.worker.pk, self.worker.pk, self.manager.pk])

    def test_old_status_does_not_fabricate_consent(self):
        self.lead.status = "won"
        self.lead.save()
        self.assertEqual(self.card()["status"], "new")
        self.assertEqual(Order.objects.count(), 0)

    def test_cannot_change_stage_arbitrarily(self):
        response = self.api.patch(f"/api/leads/{self.lead.pk}/status/", {"status": "won", "expected_status": "new"}, format="json", HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 409)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "new")

    def test_permissions_and_csrf(self):
        self.assertEqual(self.api.post(f"/workspace/lead/{self.lead.pk}/", {"action": "convert"}).status_code, 403)
        self.api.force_login(self.worker)
        self.assertEqual(self.api.get("/api/leads/board/").status_code, 403)
        self.assertEqual(self.post(f"/workspace/lead/{self.lead.pk}/", {"action": "convert"}).status_code, 403)

    def test_assignment_explains_missing_skill(self):
        from apps.orders.services import assign_order
        from django.core.exceptions import ValidationError
        order = Order.objects.create(title="Cleaning", service=self.service, client=self.customer)
        self.worker.services.clear()
        with self.assertRaisesMessage(ValidationError, "Нет активного мастера с услугой"):
            assign_order(order.pk, None, self.operator)
        order.refresh_from_db()
        self.assertIsNone(order.employee_id)
        self.worker.services.add(self.service)
        self.assertEqual(assign_order(order.pk, None, self.operator).employee_id, self.worker.pk)

    def test_reassignment_invalidates_old_buttons_and_records_reason(self):
        from apps.orders.services import convert_lead, assign_order, transition_order
        from apps.leads.models import TelegramNotice
        from apps.leads.telegram import apply_callback
        from django.core.exceptions import ValidationError
        order, _ = convert_lead(self.lead.pk)
        assign_order(order.pk, self.worker.pk, self.operator)
        notice = TelegramNotice.objects.get(order=order)
        other = User.objects.create_user(username="replacement", role="worker", is_available=True)
        other.services.add(self.service)
        with self.assertRaises(ValidationError):
            assign_order(order.pk, other.pk, self.operator, reason="", expected_notice=str(notice.pk))
        assign_order(order.pk, other.pk, self.operator, reason="No response", expected_notice=str(notice.pk))
        notice.refresh_from_db()
        self.assertFalse(notice.active)
        self.assertEqual(TelegramNotice.objects.filter(order=order, active=True).count(), 1)
        self.assertIn("No response", order.events.last().description)
        with self.assertRaises(ValidationError):
            assign_order(order.pk, self.worker.pk, self.operator, reason="Stale", expected_notice=str(notice.pk))
        current = TelegramNotice.objects.get(order=order, active=True)
        transition_order(order.pk, "start", actor=other)
        with self.assertRaises(ValidationError):
            assign_order(order.pk, self.worker.pk, self.operator, reason="Busy", expected_notice=str(current.pk))

    def test_board_delivery_and_waiting(self):
        from apps.orders.services import convert_lead, assign_order
        from apps.leads.models import TelegramNotice
        from django.utils import timezone
        from datetime import timedelta
        order, _ = convert_lead(self.lead.pk)
        self.worker.telegram_chat_id = 12345
        self.worker.save()
        assign_order(order.pk, self.worker.pk, self.operator)
        notice = TelegramNotice.objects.get(order=order)
        TelegramNotice.objects.filter(pk=notice.pk).update(created_at=timezone.now()-timedelta(minutes=16), attempts=1)
        card = self.card()
        self.assertTrue(card["overdue"])
        self.assertEqual(card["waiting_minutes"], 16)
        self.assertIn("assignment_notice", card)
        TelegramNotice.objects.filter(pk=notice.pk).update(state="sent")
        self.assertNotEqual(self.card()["delivery"], card["delivery"])

    def test_return_waits_for_operator_assignment(self):
        from apps.orders.services import convert_lead, assign_order
        from apps.leads.models import TelegramNotice
        order, _ = convert_lead(self.lead.pk)
        assign_order(order.pk, self.worker.pk, self.operator)
        notice = TelegramNotice.objects.get(order=order)
        url = f"/api/orders/{order.pk}/assign/"
        response = self.api.post(url, {"action":"return","reason":"No response","expected_notice":str(notice.pk)}, format="json", HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status,"new")
        self.assertIsNone(order.employee_id)
        self.assertEqual(TelegramNotice.objects.filter(order=order).count(),1)
        self.assertFalse(TelegramNotice.objects.filter(order=order,active=True).exists())
        self.assertEqual(self.card()["status"],"in_progress")
        response = self.api.post(url,{"employee_id":self.worker.pk},format="json",HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code,200)
        self.assertEqual(TelegramNotice.objects.filter(order=order,active=True,state="pending").count(),1)

    def test_lost_deal_reason_audit_and_guards(self):
        url = f"/workspace/lead/{self.lead.pk}/"
        self.post(url,{"action":"lost","reason":""})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status,"new")
        self.post(url,{"action":"lost","reason":"Price declined"})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status,"lost")
        self.assertEqual(self.lead.lost_by_id,self.operator.pk)
        self.assertIsNotNone(self.lead.lost_at)
        self.assertEqual(self.card()["status"],"lost")
        self.post(url,{"action":"lost","reason":"Overwrite"})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.lost_reason,"Price declined")
        self.post(url,{"action":"convert"})
        self.assertFalse(Order.objects.filter(lead=self.lead).exists())
        self.api.force_login(self.worker)
        self.assertEqual(self.post(url,{"action":"lost","reason":"No"}).status_code,403)

    def test_existing_order_cannot_be_lost(self):
        from apps.orders.services import convert_lead
        convert_lead(self.lead.pk)
        self.post(f"/workspace/lead/{self.lead.pk}/",{"action":"lost","reason":"No"})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status,"converted")

    def test_cancel_started_order_preserves_history_and_invalidates_notice(self):
        from apps.orders.services import convert_lead, assign_order, transition_order
        from apps.leads.models import TelegramNotice
        order, _ = convert_lead(self.lead.pk)
        assign_order(order.pk,self.worker.pk,self.operator)
        transition_order(order.pk,"start",actor=self.worker)
        url=f"/orders/{order.pk}/"
        self.api.force_login(self.worker)
        self.assertEqual(self.post(url,{"action":"cancel","reason":"No"}).status_code,403)
        self.api.force_login(self.operator)
        self.post(url,{"action":"cancel","reason":""})
        order.refresh_from_db()
        self.assertEqual(order.status,"in_progress")
        self.post(url,{"action":"cancel","reason":"Client refused","return_to":"kanban"})
        order.refresh_from_db()
        self.assertEqual(order.status,"cancelled")
        self.assertIsNotNone(order.cancelled_at)
        self.assertFalse(TelegramNotice.objects.filter(order=order,active=True).exists())
        self.assertEqual(self.card()["status"],"lost")
        self.assertEqual(order.events.last().actor_id,self.operator.pk)
        self.api.force_login(self.worker)
        self.assertNotContains(self.api.get("/my-orders/"),url)
        self.assertContains(self.api.get("/my-orders/history/"),url)

    def test_worker_load_counts_active_requests_without_double_counting(self):
        self.lead.employee = self.worker
        self.lead.save()
        for status in ("new", "assigned", "in_progress", "completed", "paid", "cancelled"):
            Order.objects.create(title=status, client=self.customer, service=self.service,
                                 employee=self.worker, status=status)
        converted = Lead.objects.create(title="Converted", client=self.customer,
                                        employee=self.worker, status="assigned")
        Order.objects.create(title="Linked", lead=converted, client=self.customer,
                             service=self.service, employee=self.worker, status="assigned")
        for status in ("won", "lost", "converted"):
            Lead.objects.create(title=status, client=self.customer, employee=self.worker, status=status)
        unassigned = Lead.objects.create(title="Unassigned", client=self.customer)
        response = self.api.get("/api/leads/board/")
        self.assertEqual(response.status_code, 200)
        for card in response.data["leads"]:
            self.assertEqual(card["employee_active_count"], 5 if card["employee_id"] else 0)
        self.lead.status = "lost"
        self.lead.save()
        response = self.api.get("/api/leads/board/")
        self.assertTrue(all(card["employee_active_count"] == 4 for card in response.data["leads"] if card["employee_id"]))

    def test_returning_client_matches_phone_and_counts_conversion_once(self):
        self.customer.phone = "8 (777) 123-45-67"
        self.customer.save()
        duplicate = Client.objects.create(name="Different name", phone="+77771234567")
        order = Order.objects.create(title="First order", lead=self.lead,
                                     client=self.customer, service=self.service, status="cancelled")
        second = Lead.objects.create(title="Again", client=duplicate)
        unrelated = Client.objects.create(name=self.customer.name, phone="+77771234568")
        third = Lead.objects.create(title="Other phone", client=unrelated)
        cards = {card["key"]: card for card in self.api.get("/api/leads/board/").data["leads"]}
        self.assertEqual(cards[f"order-{order.pk}"]["client_previous_count"], 0)
        self.assertEqual(cards[f"lead-{second.pk}"]["client_previous_count"], 1)
        self.assertEqual(cards[f"lead-{third.pk}"]["client_previous_count"], 0)

    def test_invalid_phones_do_not_merge_different_clients(self):
        other = Client.objects.create(name="Other", phone=self.customer.phone)
        lead = Lead.objects.create(title="Other", client=other)
        cards = {card["key"]: card for card in self.api.get("/api/leads/board/").data["leads"]}
        self.assertEqual(cards[f"lead-{lead.pk}"]["client_previous_count"], 0)

    def test_completed_repeat_is_shown_as_paid_without_new_payment(self):
        original = Order.objects.create(title="Original", client=self.customer, service=self.service,
                                        employee=self.worker, status="paid", amount=20000)
        repeated = Order.objects.create(title="Repeat", client=self.customer, service=self.service,
                                        employee=self.worker, repeat_of=original, status="completed")
        regular = Order.objects.create(title="Regular", client=self.customer, service=self.service,
                                       employee=self.worker, status="completed")
        cards = {c["key"]: c for c in self.api.get("/api/leads/board/").data["leads"]}
        self.assertEqual(cards[f"order-{repeated.pk}"]["status"], "paid")
        self.assertEqual(cards[f"order-{repeated.pk}"]["detail"], "Повторка выполнена · оплачено в исходном заказе")
        self.assertEqual(cards[f"order-{regular.pk}"]["status"], "completed")
        repeated.refresh_from_db()
        self.assertIsNone(repeated.amount)
        self.assertIsNone(repeated.received_amount)
        self.assertIsNone(repeated.paid_at)
