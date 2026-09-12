import hashlib
import hmac
import os

from django.db import connection, transaction
from rest_framework import serializers
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.customers.models import Client
from apps.customers.phones import normalize_phone
from apps.services.models import Service
from .models import IncomingCall, Lead


class PBXPermission(BasePermission):
    def has_permission(self, request, view):
        secret = os.environ.get("TELEPHONY_WEBHOOK_TOKEN", "")
        supplied = request.headers.get("Authorization", "")
        return bool(secret) and hmac.compare_digest(supplied.encode(), ("Bearer " + secret).encode())


class CallSerializer(serializers.Serializer):
    event_id = serializers.CharField(max_length=128)
    phone = serializers.CharField(max_length=32)
    service_id = serializers.PrimaryKeyRelatedField(queryset=Service.objects.all(), required=False)

    def validate_phone(self, value):
        try:
            return normalize_phone(value)
        except ValueError as error:
            raise serializers.ValidationError(str(error))


@transaction.atomic
def receive_call(data):
    # Serialize inbound calls while identifying/creating the same phone number.
    if connection.vendor == "postgresql":
        event_key = int.from_bytes(hashlib.sha256(("event:" + data["event_id"]).encode()).digest()[:8], "big", signed=True)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [event_key])
        key = int.from_bytes(hashlib.sha256(data["phone"].encode()).digest()[:8], "big", signed=True)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])
    existing = IncomingCall.objects.filter(event_id=data["event_id"]).first()
    if existing:
        if existing.phone != data["phone"]:
            raise serializers.ValidationError("Этот event_id уже относится к другому номеру.")
        return existing, False
    matches = list(Client.objects.filter(normalized_phone=data["phone"]).order_by("pk")[:2])
    if len(matches) > 1:
        return IncomingCall.objects.create(event_id=data["event_id"], phone=data["phone"]), True
    customer = matches[0] if matches else Client.objects.create(name="Новый клиент " + data["phone"], phone=data["phone"])
    lead = Lead.objects.create(
        title="Входящий звонок", client=customer, service=data.get("service_id"), source="Телефон",
    )
    return IncomingCall.objects.create(event_id=data["event_id"], phone=data["phone"], client=customer, lead=lead), True


class IncomingCallAPI(APIView):
    authentication_classes = ()
    permission_classes = (PBXPermission,)

    def post(self, request):
        serializer = CallSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        call, created = receive_call(serializer.validated_data)
        return Response({
            "call_id": call.pk, "client_id": call.client_id, "lead_id": call.lead_id,
            "needs_review": call.client_id is None,
            "history_url": f"/clients/{call.client_id}/history/" if call.client_id else None,
        }, status=201 if created else 200)
